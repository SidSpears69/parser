"""Local protocol checks for the authenticated SOCKS5 relay."""

import socket
import socketserver
from threading import Event, Thread
import unittest

from playwright.sync_api import sync_playwright

from web_parser.socks5_relay import Socks5AuthRelay


def _read_exact(stream: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def _read_address_tail(stream: socket.socket, address_type: int) -> bytes:
    if address_type == 1:
        return _read_exact(stream, 6)
    if address_type == 3:
        size = _read_exact(stream, 1)
        return size + _read_exact(stream, size[0] + 2)
    if address_type == 4:
        return _read_exact(stream, 18)
    raise ValueError("Unsupported address type")


class _FakeSocksProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.username = b""
        self.password = b""
        self.request = b""
        self.payload = b""
        self.respond_http = False
        self.finished = Event()
        self.error: Exception | None = None


class _FakeSocksHandler(socketserver.BaseRequestHandler):
    server: _FakeSocksProxy

    def handle(self) -> None:
        stream: socket.socket = self.request
        try:
            version, method_count = _read_exact(stream, 2)
            methods = _read_exact(stream, method_count)
            if version != 5 or 2 not in methods:
                raise AssertionError("Relay did not offer upstream username/password auth")
            stream.sendall(bytes((5, 2)))

            auth_version, username_length = _read_exact(stream, 2)
            self.server.username = _read_exact(stream, username_length)
            password_length = _read_exact(stream, 1)[0]
            self.server.password = _read_exact(stream, password_length)
            if auth_version != 1:
                raise AssertionError("Unexpected upstream auth version")
            stream.sendall(bytes((1, 0)))

            request_header = _read_exact(stream, 4)
            self.server.request = request_header + _read_address_tail(stream, request_header[3])
            stream.sendall(bytes((5, 0, 0, 1, 127, 0, 0, 1, 0, 0)))
            if self.server.respond_http:
                self.server.payload = stream.recv(4096)
                stream.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                )
            else:
                payload = _read_exact(stream, 4)
                stream.sendall(payload[::-1])
        except (AssertionError, ConnectionError, OSError, ValueError) as error:
            self.server.error = error
        finally:
            self.server.finished.set()


class Socks5AuthRelayTests(unittest.TestCase):
    def test_authenticates_upstream_and_tunnels_domain_connect(self):
        upstream = _FakeSocksProxy(("127.0.0.1", 0), _FakeSocksHandler)
        upstream_thread = Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        relay = Socks5AuthRelay(
            f"socks5://127.0.0.1:{upstream.server_address[1]}",
            "relay-user", "relay-password",
        )
        relay_port = relay.start()
        client = socket.create_connection(("127.0.0.1", relay_port), timeout=2)
        client.settimeout(2)
        try:
            client.sendall(bytes((5, 1, 0)))
            self.assertEqual(_read_exact(client, 2), bytes((5, 0)))

            host = b"example.com"
            request = bytes((5, 1, 0, 3, len(host))) + host + bytes((1, 187))
            client.sendall(request)
            reply_header = _read_exact(client, 4)
            reply_tail = _read_address_tail(client, reply_header[3])
            self.assertEqual(reply_header, bytes((5, 0, 0, 1)))
            self.assertEqual(len(reply_tail), 6)

            client.sendall(b"ping")
            self.assertEqual(_read_exact(client, 4), b"gnip")
            self.assertTrue(upstream.finished.wait(2))
            self.assertIsNone(upstream.error)
            self.assertEqual(upstream.username, b"relay-user")
            self.assertEqual(upstream.password, b"relay-password")
            self.assertEqual(upstream.request, request)
        finally:
            client.close()
            relay.close()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)

    def test_playwright_request_uses_authenticated_relay(self):
        upstream = _FakeSocksProxy(("127.0.0.1", 0), _FakeSocksHandler)
        upstream.respond_http = True
        upstream_thread = Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        relay = Socks5AuthRelay(
            f"socks5://127.0.0.1:{upstream.server_address[1]}",
            "relay-user", "relay-password",
        )
        relay.start()
        try:
            with sync_playwright() as playwright:
                request = playwright.request.new_context(
                    proxy={"server": f"socks5://127.0.0.1:{relay.port}"},
                    timeout=5_000,
                )
                try:
                    # The fake SOCKS server returns a plain HTTP response without TLS.
                    # noinspection HttpUrlsUsage
                    response = request.get("http://example.com/")
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.text(), "ok")
                finally:
                    request.dispose()
            self.assertTrue(upstream.finished.wait(2))
            self.assertIsNone(upstream.error)
            self.assertTrue(upstream.payload.startswith(b"GET "))
            self.assertEqual(upstream.username, b"relay-user")
            self.assertEqual(upstream.password, b"relay-password")
        finally:
            relay.close()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
