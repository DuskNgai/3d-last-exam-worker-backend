"""
Host-side model API relay for gateway-mode VMs.

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
import json
from pathlib import Path
import socket
import socketserver
import sys
import threading
from typing import Callable, Final
from urllib.parse import urlsplit

# The relay runs as a standalone script spawned by QEMU's guestfwd command, so it puts src/ on the path
# itself rather than relying on a package import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.deploy.credentials import CredentialsError, find_profile, KEYS_FILENAME, load_keys

GUESTFWD_PORT: Final = 8443
# The guest reaches the relay through harness.GUESTFWD_BASE_URL, which is rooted at this path; it is dropped
# before forwarding, so the credential's own path is what decides the upstream path.
GUEST_PATH_PREFIX: Final = '/v1'

# One response body is inspected for usage at most up to this many bytes when it is not an event stream.
USAGE_BODY_LIMIT: Final = 8 * 1024 * 1024
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


class _MalformedRequestError(Exception):
    """A request the relay cannot parse; the channel answers 400 and stops."""


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

    def __init__(self, upstream: str, api_key: str, log_path: Path | None = None, usage_path: Path | None = None):
        """
        Bind an upstream endpoint and the real API key.

        Args:
            `upstream` (str): Upstream base URL, which must use https.
            `api_key` (str): Real API key injected into forwarded requests.
            `log_path` (Path | None): File receiving one line per request; None keeps the log on stderr only.
            `usage_path` (Path | None): File receiving one token-usage record per response; None records none.

        Raises:
            RelayError: The upstream URL does not use https.
        """
        self._upstream = urlsplit(upstream)
        if self._upstream.scheme != 'https':
            raise RelayError(f'upstream must be https: {self._upstream.scheme}://...')
        self._base_path = self._upstream.path.rstrip('/')
        self._api_key = api_key
        self.log_path = log_path
        self.usage_path = usage_path
        self._lock = threading.Lock()

    def log(self, method: str, path: str, status: int) -> None:
        """Record one request; never log credentials or bodies."""
        line = f'{method} {path} -> {status}'
        with self._lock:
            if self.log_path is not None:
                # One append per request; guestfwd spawns a relay process per connection, and a single
                # O_APPEND write keeps their lines from interleaving.
                with self.log_path.open('a', encoding='utf-8') as handle:
                    handle.write(line + '\n')
            print(line, file=sys.stderr, flush=True)

    def forward(self, method: str, path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], http.client.HTTPResponse]:
        """
        Send one request upstream with the real key and return the live response.

        Args:
            `method` (str): HTTP method to send.
            `path` (str): Request path as sent by the guest; the guest base path is stripped from it and the
                credential's own path is put in its place.
            `headers` (dict[str, str]): Lowercased request headers.
            `body` (bytes): Request body.

        Returns:
            `status` (int): Upstream status code.
            `safe_headers` (dict[str, str]): Response headers with hop-by-hop and content-length removed.
            `response` (http.client.HTTPResponse): The open response to stream; the caller must close it.

        Raises:
            RelayError: The upstream endpoint could not be reached over IPv4.
            OSError: The upstream socket connection failed.
            http.client.HTTPException: The upstream response was malformed.
        """
        upstream_path = f'{self._base_path}{path.removeprefix(GUEST_PATH_PREFIX)}'
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
    """
    Parse one HTTP/1.1 request; return None on clean EOF.

    Raises:
        _MalformedRequestError: The request line is not `METHOD PATH VERSION`, or `Content-Length` is not a
            non-negative integer; the stream can no longer be framed, so the channel stops.
    """
    line = rfile.readline(65536)
    if not line:
        return None
    parts = line.decode('latin-1').strip().split(' ', 2)
    if len(parts) != 3:
        raise _MalformedRequestError(f'bad request line: {line[:200]!r}')
    method, path, _ = parts
    headers: dict[str, str] = {}
    while True:
        line = rfile.readline(65536)
        if line in (b'\r\n', b'\n', b''):
            break
        name, _, value = line.decode('latin-1').partition(':')
        headers[name.strip().lower()] = value.strip()
    length_text = headers.get('content-length', '0')
    try:
        length = int(length_text)
    except ValueError as error:
        raise _MalformedRequestError(f'bad content-length: {length_text!r}') from error
    if length < 0:
        raise _MalformedRequestError(f'bad content-length: {length_text!r}')
    body = rfile.read(length) if length else b''
    return method, path, headers, body


def _usage_counts(payload: bytes) -> tuple[int, int] | None:
    """
    Read the token counts of one JSON payload that carries a usage object.

    The Responses API nests usage under the response object, Chat Completions keeps it at the top
    level; both spell the pair input/output or prompt/completion.

    Args:
        `payload` (bytes): One complete JSON document, a response body or one SSE `data:` payload.

    Returns:
        `counts` (tuple[int, int] | None): (input, output) tokens, or None when the payload carries none.
    """
    if b'"usage"' not in payload:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    nested = data.get('response')
    for candidate in (data.get('usage'), nested.get('usage') if isinstance(nested, dict) else None):
        if not isinstance(candidate, dict):
            continue
        tokens_in = candidate.get('input_tokens', candidate.get('prompt_tokens'))
        tokens_out = candidate.get('output_tokens', candidate.get('completion_tokens'))
        if isinstance(tokens_in, int) and isinstance(tokens_out, int):
            return tokens_in, tokens_out
    return None


class _UsageTap:
    """
    Collect the token usage of one response while its body streams past to the guest.

    The relay only reads the payload; it never rewrites it. A streaming response is scanned line by
    line, a whole-body response is kept up to `USAGE_BODY_LIMIT`, and the last usage seen is written
    as one record, so a response is never counted twice.
    """

    def __init__(self, usage_path: Path | None, request_path: str, status: int, event_stream: bool):
        self._usage_path = usage_path
        self._request_path = request_path
        self._status = status
        self._event_stream = event_stream
        self._pending = b''
        self._counts: tuple[int, int] | None = None

    def feed(self, chunk: bytes) -> None:
        """Take one body chunk as it is forwarded."""
        if self._usage_path is None:
            return
        if self._event_stream:
            self._pending += chunk
            while b'\n' in self._pending:
                line, self._pending = self._pending.split(b'\n', 1)
                self._consider(line)
        elif len(self._pending) <= USAGE_BODY_LIMIT:
            self._pending += chunk

    def finish(self) -> None:
        """Write the record once the response is complete, cut short or not."""
        if self._usage_path is None:
            return
        if not self._event_stream:
            counts = _usage_counts(self._pending)
            if counts is not None:
                self._counts = counts
        if self._counts is None:
            return
        record = {
            'path': self._request_path,
            'status': self._status,
            'input_tokens': self._counts[0],
            'output_tokens': self._counts[1]
        }
        # One small append per response: guestfwd spawns a relay process per connection, and a single
        # O_APPEND write keeps their records from interleaving.
        with self._usage_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, separators=(',', ':')) + '\n')

    def _consider(self, line: bytes) -> None:
        """Read one SSE line; the last usage-bearing event of the response wins."""
        if not line.startswith(b'data:'):
            return
        counts = _usage_counts(line[len(b'data:'):].strip())
        if counts is not None:
            self._counts = counts


def _write_response(
    wfile: io.BufferedIOBase,
    status: int,
    headers: dict[str, str],
    response: http.client.HTTPResponse,
    keep_alive: bool,
    on_chunk: Callable[[bytes], None] | None = None
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
        if on_chunk is not None:
            on_chunk(chunk)
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
    """
    Serve sequential HTTP requests on one channel until the guest closes it.

    Args:
        `rfile` (io.BufferedIOBase): Request source.
        `wfile` (io.BufferedIOBase): Response sink.
        `core` (RelayCore): Upstream forwarder with key injection.
    """
    while True:
        try:
            request = _read_request(rfile)
        except _MalformedRequestError as error:
            _write_simple(wfile, 400, f'relay malformed request: {error}'.encode())
            return
        if request is None:
            return
        method, path, headers, body = request
        try:
            status, response_headers, response = core.forward(method, path, headers, body)
        except (RelayError, OSError, http.client.HTTPException) as error:
            _write_simple(wfile, 502, f'relay upstream error: {error}'.encode())
            return
        keep_alive = headers.get('connection', '').lower() != 'close'
        tap = _UsageTap(core.usage_path, path, status, event_stream='text/event-stream' in response_headers.get('content-type', ''))
        try:
            _write_response(wfile, status, response_headers, response, keep_alive, tap.feed)
        except BrokenPipeError:
            return
        finally:
            response.close()
            tap.finish()
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
    """
    CLI entry point.

    Raises:
        SystemExit: The relay configuration is unusable.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument('--stdio', action='store_true', help='serve stdin/stdout; used with guestfwd-cmd')
    transport.add_argument('--socket', type=Path, help='unix socket to listen on')
    parser.add_argument('--keys', type=Path, default=Path(KEYS_FILENAME), help='credential table with the endpoint and key')
    parser.add_argument('--provider', required=True, help='provider stem the run resolved for its model')
    parser.add_argument('--log', type=Path, default=None, help='append one line per request to this file')
    parser.add_argument('--usage', type=Path, default=None, help='append one token-usage record per response to this file')
    args = parser.parse_args()
    try:
        profile = find_profile(load_keys(args.keys.resolve()), args.provider)
        core = RelayCore(profile.base_url, profile.api_key, log_path=args.log, usage_path=args.usage)
        if args.stdio:
            serve_channel(sys.stdin.buffer, sys.stdout.buffer, core)
            return
        with _RelayServer(args.socket.resolve(), core) as server:
            print(f'relay listening on {args.socket.resolve()} for guestfwd port {GUESTFWD_PORT}', flush=True)
            server.serve_forever()
    except (CredentialsError, RelayError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
