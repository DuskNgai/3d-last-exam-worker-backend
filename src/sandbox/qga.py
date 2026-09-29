"""
QEMU Guest Agent and QMP protocol clients over unix sockets.

Both protocols exchange newline-delimited JSON. QGA lives inside the guest on a
virtio-serial channel, so it works without any guest network. QMP talks to the
QEMU process on the host side.
"""

import base64
from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import time
from typing import Any, Final, Self
import uuid

FILE_CHUNK_BYTES: Final = 262144
EXEC_POLL_INTERVAL: Final = 2.0


class QgaError(RuntimeError):
    """QGA or QMP returned an error reply or the connection dropped."""


@dataclass
class ExecResult:
    """Outcome of a finished guest-exec process."""

    exitcode: int
    stdout: bytes
    stderr: bytes
    signal: int | None


class _Channel:
    """Newline-delimited JSON request/reply channel over a unix socket."""

    def __init__(self, path: Path, timeout: float):
        self._socket = socket.socket(socket.AF_UNIX)
        try:
            self._socket.settimeout(timeout)
            self._socket.connect(str(path))
            self._reader = self._socket.makefile('r')
        except Exception:
            self._socket.close()
            raise

    def close(self) -> None:
        self._reader.close()
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_line(self) -> str:
        """Read one newline-terminated line; empty when the peer closed the channel."""
        return self._reader.readline()

    def request(self, execute: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Send one command and return its reply payload.

        Args:
            `execute` (str): QGA or QMP command name.
            `arguments` (dict[str, Any] | None): Command arguments.

        Returns:
            `reply` (dict[str, Any]): The `return` value of the reply, or the full reply for handshake commands.

        Raises:
            QgaError: The peer returned an error object or closed the connection.
        """
        command = {
            'execute': execute
        }
        if arguments is not None:
            command['arguments'] = arguments
        self._socket.sendall(json.dumps(command).encode() + b'\n')
        while True:
            line = self.read_line()
            if not line:
                raise QgaError(f'Connection closed while waiting for {execute}')
            reply = json.loads(line)
            if 'error' in reply:
                error = reply['error']
                raise QgaError(f"{execute} failed: {error.get('class')}: {error.get('desc')}")
            if 'return' in reply:
                return reply['return'] if reply['return'] is not None else {}


class QgaClient:
    """QEMU Guest Agent client: command execution and file transfer."""

    def __init__(self, socket_path: Path, timeout: float = 30.0):
        self._socket_path = Path(socket_path)
        self._timeout = timeout

    def _connect(self) -> _Channel:
        channel = _Channel(self._socket_path, self._timeout)
        sync_id = int(time.time() * 1000) % 2147483647
        while True:
            reply = channel.request('guest-sync', {
                'id': sync_id
            })
            if reply == sync_id:
                return channel

    def ping(self) -> None:
        """
        Verify the guest agent answers.

        Raises:
            QgaError: The agent did not answer cleanly.
        """
        with self._connect() as channel:
            channel.request('guest-ping')

    def shutdown(self) -> None:
        """
        Ask the guest to power off cleanly.

        Raises:
            QgaError: The agent rejected or could not schedule the shutdown.
        """
        with self._connect() as channel:
            channel.request('guest-shutdown', {
                'mode': 'powerdown'
            })

    def exec_start(self, command: list[str], env: dict[str, str] | None = None, input_data: bytes | None = None) -> int:
        """
        Start a guest process and return its pid without waiting.

        Args:
            `command` (list[str]): Absolute program path followed by arguments.
            `env` (dict[str, str] | None): Extra environment variables.
            `input_data` (bytes | None): Data fed to stdin.

        Returns:
            `pid` (int): Guest pid of the started process.

        Raises:
            QgaError: The agent rejected the request or the connection dropped.
        """
        arguments: dict[str, Any] = {
            'path': command[0],
            'arg': command[1 :],
            'capture-output': True
        }
        if env:
            arguments['env'] = [f'{key}={value}' for key, value in env.items()]
        if input_data is not None:
            arguments['input-data'] = base64.b64encode(input_data).decode()
        with self._connect() as channel:
            reply = channel.request('guest-exec', arguments)
            return int(reply['pid'])

    def exec_status(self, pid: int) -> ExecResult | None:
        """
        Poll a guest process; return None while it is still running.

        Args:
            `pid` (int): Guest pid from exec_start.

        Returns:
            `result` (ExecResult | None): Final result once the process exited, else None.

        Raises:
            QgaError: The agent rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            reply = channel.request('guest-exec-status', {
                'pid': pid
            })
        if not reply.get('exited'):
            return None
        return ExecResult(
            exitcode=int(reply.get('exitcode', -1)),
            stdout=base64.b64decode(reply.get('out-data', '')),
            stderr=base64.b64decode(reply.get('err-data', '')),
            signal=reply.get('signal'),
        )

    def run(self, command: list[str], timeout: float = 120.0, env: dict[str, str] | None = None) -> ExecResult:
        """
        Run a guest command to completion and collect its output.

        Args:
            `command` (list[str]): Absolute program path followed by arguments.
            `timeout` (float): Seconds before raising; the guest process is not killed here.
            `env` (dict[str, str] | None): Extra environment variables.

        Returns:
            `result` (ExecResult): Exit code and captured output.

        Raises:
            QgaError: The agent rejected the request or the connection dropped.
            TimeoutError: The process did not exit within timeout.
        """
        pid = self.exec_start(command, env=env)
        deadline = time.monotonic() + timeout
        while True:
            result = self.exec_status(pid)
            if result is not None:
                return result
            if time.monotonic() > deadline:
                raise TimeoutError(f'Guest process {pid} still running after {timeout}s: {command[0]}')
            time.sleep(EXEC_POLL_INTERVAL)

    def upload(self, remote_path: str, data: bytes) -> None:
        """
        Write bytes to a guest file, replacing existing content.

        Args:
            `remote_path` (str): Absolute guest path.
            `data` (bytes): File content.

        Raises:
            QgaError: The agent rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            handle = int(channel.request('guest-file-open', {
                'path': remote_path,
                'mode': 'w+'
            }))
            try:
                for offset in range(0, len(data), FILE_CHUNK_BYTES):
                    chunk = base64.b64encode(data[offset : offset + FILE_CHUNK_BYTES]).decode()
                    channel.request('guest-file-write', {
                        'handle': handle,
                        'buf-b64': chunk
                    })
            finally:
                channel.request('guest-file-close', {
                    'handle': handle
                })

    def upload_large(self, remote_path: str, parts: Iterable[bytes], timeout: float = 600.0) -> None:
        """
        Upload a large payload as numbered parts and merge it guest-side.

        QGA base64 framing makes single uploads slow past a few hundred MB;
        splitting into parts keeps memory flat and allows streamed sources.
        Parts are removed in the guest whether the merge succeeds or not.

        Args:
            `remote_path` (str): Final guest path that receives the merged payload.
            `parts` (Iterable[bytes]): Payload chunks, uploaded in iteration order.
            `timeout` (float): Guest-side merge timeout.

        Raises:
            QgaError: The guest-side merge failed.
            TimeoutError: The guest-side merge did not finish within timeout.
        """
        tag = uuid.uuid4().hex[: 8]
        names = []
        try:
            for index, part in enumerate(parts):
                name = f'/tmp/qga-{tag}.part.{index:04d}'
                self.upload(name, part)
                names.append(name)
            joined = ' '.join(names)
            result = self.run(['/bin/bash', '-c', f'cat {joined} > {remote_path} && rm -f {joined}'], timeout=timeout)
            if result.exitcode != 0:
                raise QgaError(f'merge failed for {remote_path}: {result.stderr.decode(errors="replace")[:500]}')
        finally:
            if names:
                try:
                    self.run(['/bin/bash', '-c', f'rm -f /tmp/qga-{tag}.part.*'], timeout=30.0)
                except (QgaError, TimeoutError, OSError):
                    pass

    def download(self, remote_path: str) -> bytes:
        """
        Read a guest file and return its content.

        Args:
            `remote_path` (str): Absolute guest path.

        Returns:
            `content` (bytes): The file content.

        Raises:
            QgaError: The agent rejected the request or the connection dropped.
        """
        chunks: list[bytes] = []
        with self._connect() as channel:
            handle = int(channel.request('guest-file-open', {
                'path': remote_path,
                'mode': 'r'
            }))
            try:
                while True:
                    reply = channel.request('guest-file-read', {
                        'handle': handle
                    })
                    chunks.append(base64.b64decode(reply.get('buf-b64', '')))
                    if reply.get('eof'):
                        return b''.join(chunks)
            finally:
                channel.request('guest-file-close', {
                    'handle': handle
                })


class QmpClient:
    """QEMU Machine Protocol client: screenshots and power control."""

    def __init__(self, socket_path: Path, timeout: float = 30.0):
        self._socket_path = Path(socket_path)
        self._timeout = timeout

    def _connect(self) -> _Channel:
        channel = _Channel(self._socket_path, self._timeout)
        try:
            greeting = channel.read_line()
            if 'QMP' not in greeting:
                raise QgaError(f'Unexpected QMP greeting: {greeting[:80]!r}')
            channel.request('qmp_capabilities')
            return channel
        except Exception:
            channel.close()
            raise

    def screendump(self, output: Path) -> None:
        """
        Capture the virtual display to a ppm file.

        Args:
            `output` (Path): Absolute host path for the ppm file.

        Raises:
            QgaError: The QEMU process rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            channel.request('screendump', {
                'filename': str(output)
            })

    def system_powerdown(self) -> None:
        """
        Send an ACPI power button event to the guest.

        Raises:
            QgaError: The QEMU process rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            channel.request('system_powerdown')

    def quit(self) -> None:
        """
        Terminate the QEMU process immediately.

        Raises:
            QgaError: The QEMU process rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            channel.request('quit')

    def query_status(self) -> str:
        """
        Return the guest runstate such as running or suspended.

        Returns:
            `runstate` (str): The QMP runstate string.

        Raises:
            QgaError: The QEMU process rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            reply = channel.request('query-status')
            return str(reply.get('status', 'unknown'))

    def query_block_write_stats(self) -> tuple[int, int]:
        """
        Return cumulative bytes and operations written to guest block devices.

        Returns:
            `write_bytes` (int): Cumulative bytes written across all guest block devices.
            `write_operations` (int): Cumulative write operations across all guest block devices.

        Raises:
            QgaError: The QEMU process rejected the request or the connection dropped.
        """
        with self._connect() as channel:
            devices = channel.request('query-blockstats')
        write_bytes = 0
        write_operations = 0
        for device in devices:
            stats = device.get('stats', {})
            write_bytes += int(stats.get('wr_bytes', 0))
            write_operations += int(stats.get('wr_operations', 0))
        return write_bytes, write_operations
