"""Host-side model API relay for gateway-mode VMs.

QEMU guestfwd delivers guest HTTP requests for 10.0.2.100:8443 to the host.
Two transports are supported: `--stdio` for guestfwd-cmd (QEMU spawns this
program per guest connection and pipes it to stdin/stdout) and `--socket` for
a unix listener. The relay forwards each request to the real model endpoint
and injects the real API key. The key stays in this process's memory; the
guest only ever sees a placeholder credential.
"""

import argparse
import http.client
import io
import socket
import socketserver
import sys
import threading
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

GUESTFWD_PORT: Final = 8443
BODY_CHUNK_BYTES: Final = 65536
UPSTREAM_TIMEOUT: Final = 900.0
HOP_BY_HOP: Final = frozenset({
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'transfer-encoding',
    'upgrade',
    'host',
    'content-length',
    'authorization',
    'x-api-key'
})


class RelayError(RuntimeError):
    """The relay configuration or upstream endpoint is unusable."""


def load_env(path: Path) -> dict[str, str]:
    """Parse a KEY=value env file without exposing values.

    `path` (Path): The .env file.

    Returns:
        dict[str, str]: Parsed key/value pairs, comments and blanks skipped.

    Raises:
        RelayError: The file is missing or a line is malformed.
    """
    if not path.is_file():
        raise RelayError(f'env file not found: {path}')
    values: dict[str, str] = {}
    with path.open() as stream:
        for lineno, line in enumerate(stream, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if '=' not in stripped:
                raise RelayError(f'{path}:{lineno}: expected KEY=value')
            key, _, value = stripped.partition('=')
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection restricted to IPv4; this host's IPv6 route is broken and hangs on connect."""

    def connect(self) -> None:
        addresses = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
        last_error: OSError | None = None
        sock: socket.socket | None = None
        for family, socktype, proto, _, address in addresses:
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(self.timeout)
                sock.connect(address)
                break
            except OSError as error:
                last_error = error
                if sock is not None:
                    sock.close()
                sock = None
        if sock is None:
            raise RelayError(f'IPv4 connect to {self.host} failed: {last_error}')
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class RelayCore:
    """Upstream endpoint plus key injection, shared by both transports."""

    def __init__(self, upstream: str, api_key: str):
        self._upstream = urlsplit(upstream)
        if self._upstream.scheme != 'https':
            raise RelayError(f'upstream must be https: {self._upstream.scheme}://...')
        self._base_path = self._upstream.path.rstrip('/')
        self._api_key = api_key
        self._lock = threading.Lock()

    def log(self, method: str, path: str, status: int) -> None:
        """Record one request; never log credentials or bodies."""
        with self._lock:
            print(f'{method} {path} -> {status}', file=sys.stderr, flush=True)

    def forward(self, method: str, path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], http.client.HTTPResponse]:
        """Send one request upstream with the real key and return the live response.

        `method` (str): HTTP method.
        `path` (str): Request path as sent by the guest, may include the base path prefix.
        `headers` (dict[str, str]): Lowercased request headers.
        `body` (bytes): Request body.

        Returns:
            tuple[int, dict[str, str], http.client.HTTPResponse]: Status, safe response headers,
            and the open response to stream; the caller must close it.
        """
        if self._base_path and path.startswith(self._base_path):
            upstream_path = path
        else:
            upstream_path = f'{self._base_path}{path}'
        outgoing = {
            name: value for name, value in headers.items() if name not in HOP_BY_HOP
        }
        if 'x-api-key' in headers and 'authorization' not in headers:
            outgoing['x-api-key'] = self._api_key
        else:
            outgoing['authorization'] = f'Bearer {self._api_key}'
        connection = _IPv4HTTPSConnection(self._upstream.hostname, self._upstream.port or 443, timeout=UPSTREAM_TIMEOUT)
        try:
            connection.request(method, upstream_path, body=body, headers=outgoing)
            response = connection.getresponse()
        except Exception:
            connection.close()
            raise
        safe_headers = {
            name.lower(): value
            for name, value in response.getheaders()
            if name.lower() not in HOP_BY_HOP and name.lower() != 'content-length'
        }
        return response.status, safe_headers, response


def _read_request(rfile: io.BufferedIOBase) -> tuple[str, str, dict[str, str], bytes] | None:
    """Parse one HTTP/1.1 request; return None on clean EOF."""
    line = rfile.readline(65536)
    if not line:
        return None
    parts = line.decode('latin-1').strip().split(' ', 2)
    if len(parts) != 3:
        return None
    method, path, _ = parts
    headers: dict[str, str] = {}
    while True:
        line = rfile.readline(65536)
        if line in (b'\r\n', b'\n', b''):
            break
        name, _, value = line.decode('latin-1').partition(':')
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get('content-length', '0'))
    body = rfile.read(length) if length else b''
    return method, path, headers, body


def _write_response(
    wfile: io.BufferedIOBase, status: int, headers: dict[str, str], response: http.client.HTTPResponse, keep_alive: bool
) -> None:
    """Stream an upstream response to the guest, framing chunks when the length is unknown."""
    reason = http.client.responses.get(status, 'status')
    head = [f'HTTP/1.1 {status} {reason}']
    head += [f'{name}: {value}' for name, value in headers.items()]
    known_length = response.length is not None and response.will_close is False
    if known_length:
        head.append(f'Content-Length: {response.length}')
    else:
        head.append('Transfer-Encoding: chunked')
    head.append(f'Connection: {"keep-alive" if keep_alive else "close"}')
    wfile.write(('\r\n'.join(head) + '\r\n\r\n').encode('latin-1'))
    while True:
        chunk = response.read1(BODY_CHUNK_BYTES)
        if not chunk:
            break
        if known_length:
            wfile.write(chunk)
        else:
            wfile.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
        wfile.flush()
    if not known_length:
        wfile.write(b'0\r\n\r\n')
    wfile.flush()


def _write_simple(wfile: io.BufferedIOBase, status: int, body: bytes) -> None:
    """Write a one-shot plain-text response and signal close."""
    reason = http.client.responses.get(status, 'status')
    wfile.write(
        f'HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n'
        f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()
    )
    wfile.write(body)
    wfile.flush()


def serve_channel(rfile: io.BufferedIOBase, wfile: io.BufferedIOBase, core: RelayCore) -> None:
    """Serve sequential HTTP requests on one channel until the guest closes it.

    `rfile` (io.BufferedIOBase): Request source.
    `wfile` (io.BufferedIOBase): Response sink.
    `core` (RelayCore): Upstream forwarder with key injection.
    """
    while True:
        request = _read_request(rfile)
        if request is None:
            return
        method, path, headers, body = request
        try:
            status, response_headers, response = core.forward(method, path, headers, body)
        except (RelayError, OSError, http.client.HTTPException) as error:
            _write_simple(wfile, 502, f'relay upstream error: {error}'.encode())
            return
        keep_alive = headers.get('connection', '').lower() != 'close'
        try:
            _write_response(wfile, status, response_headers, response, keep_alive)
        except BrokenPipeError:
            return
        finally:
            response.close()
        core.log(method, path, status)
        if not keep_alive:
            return


class _RelayHandler(socketserver.StreamRequestHandler):
    """One guest connection on the unix socket transport."""

    def handle(self) -> None:
        relay: '_RelayServer' = self.server # type: ignore[assignment]
        serve_channel(self.rfile, self.wfile, relay.core)


class _RelayServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: Path, core: RelayCore):
        self.core = core
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        super().__init__(str(socket_path), _RelayHandler)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument('--stdio', action='store_true', help='serve stdin/stdout; used with guestfwd-cmd')
    transport.add_argument('--socket', type=Path, help='unix socket to listen on')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    args = parser.parse_args()
    try:
        env = load_env(args.env_file.resolve())
        upstream = env.get('BASE_URL')
        api_key = env.get('API_KEY')
        if not upstream or not api_key:
            raise RelayError('env file must define BASE_URL and API_KEY')
        core = RelayCore(upstream, api_key)
        if args.stdio:
            serve_channel(sys.stdin.buffer, sys.stdout.buffer, core)
            return
        with _RelayServer(args.socket.resolve(), core) as server:
            print(f'relay listening on {args.socket.resolve()} for guestfwd port {GUESTFWD_PORT}', flush=True)
            server.serve_forever()
    except RelayError as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
