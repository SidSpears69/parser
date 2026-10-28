"""Loopback SOCKS5 relay for authenticated upstream SOCKS proxies.

Playwright can use SOCKS5 proxies, but its browser proxy credentials are only
supported for HTTP proxies. This small relay authenticates to an upstream
SOCKS5 proxy and exposes an unauthenticated SOCKS5 endpoint on loopback.
"""

from __future__ import annotations

import select
import socket
import socketserver
from threading import Thread
from typing import Any, cast
from urllib.parse import urlsplit


_SOCKS_VERSION = 5
_METHOD_NO_AUTH = 0
_METHOD_USERNAME_PASSWORD = 2
_METHOD_UNACCEPTABLE = 255
_AUTH_VERSION = 1
_CMD_CONNECT = 1
_REP_SUCCEEDED = 0
_REP_GENERAL_FAILURE = 1
_REP_COMMAND_UNSUPPORTED = 7
_REP_ADDRESS_UNSUPPORTED = 8
_ATYP_IPV4 = 1
_ATYP_DOMAIN = 3
_ATYP_IPV6 = 4


def _recv_exact(stream: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ConnectionError("SOCKS connection closed during negotiation")
        data.extend(chunk)
    return bytes(data)


def _address_tail(stream: socket.socket, address_type: int) -> bytes:
    if address_type == _ATYP_IPV4:
        return _recv_exact(stream, 4 + 2)
    if address_type == _ATYP_DOMAIN:
        length = _recv_exact(stream, 1)
        return length + _recv_exact(stream, length[0] + 2)
    if address_type == _ATYP_IPV6:
        return _recv_exact(stream, 16 + 2)
    raise ValueError("Unsupported SOCKS5 address type")


def _send_reply(stream: socket.socket, code: int) -> None:
    stream.sendall(bytes((_SOCKS_VERSION, code, 0, _ATYP_IPV4, 0, 0, 0, 0, 0, 0)))


def _read_reply(stream: socket.socket) -> bytes:
    header = _recv_exact(stream, 4)
    if header[0] != _SOCKS_VERSION or header[2] != 0:
        raise ConnectionError("Invalid upstream SOCKS5 response")
    return header + _address_tail(stream, header[3])


def _tunnel(left: socket.socket, right: socket.socket) -> None:
    open_sockets = {left, right}
    other = {left: right, right: left}
    while open_sockets:
        readable, _, exceptional = select.select(open_sockets, (), open_sockets, 60)
        if exceptional:
            return
        if not readable:
            continue
        for source in readable:
            destination = other[source]
            try:
                data = source.recv(65536)
                if not data:
                    open_sockets.remove(source)
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                else:
                    destination.sendall(data)
            except OSError:
                return


class _RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    upstream_host: str
    upstream_port: int
    username: bytes
    password: bytes


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client: socket.socket = self.request
        relay_server = cast(_RelayServer, self.server)
        upstream: socket.socket | None = None
        local_ready = False
        reply_sent = False
        try:
            client.settimeout(10)
            version, methods_count = _recv_exact(client, 2)
            methods = _recv_exact(client, methods_count)
            if version != _SOCKS_VERSION or _METHOD_NO_AUTH not in methods:
                client.sendall(bytes((_SOCKS_VERSION, _METHOD_UNACCEPTABLE)))
                return
            client.sendall(bytes((_SOCKS_VERSION, _METHOD_NO_AUTH)))
            local_ready = True

            request_header = _recv_exact(client, 4)
            if request_header[0] != _SOCKS_VERSION or request_header[2] != 0:
                _send_reply(client, _REP_GENERAL_FAILURE)
                return
            if request_header[1] != _CMD_CONNECT:
                _send_reply(client, _REP_COMMAND_UNSUPPORTED)
                return
            try:
                request = request_header + _address_tail(client, request_header[3])
            except ValueError:
                _send_reply(client, _REP_ADDRESS_UNSUPPORTED)
                return

            upstream = socket.create_connection(
                (relay_server.upstream_host, relay_server.upstream_port), timeout=10,
            )
            upstream.settimeout(10)
            upstream.sendall(bytes((_SOCKS_VERSION, 1, _METHOD_USERNAME_PASSWORD)))
            method_version, method = _recv_exact(upstream, 2)
            if method_version != _SOCKS_VERSION or method != _METHOD_USERNAME_PASSWORD:
                _send_reply(client, _REP_GENERAL_FAILURE)
                return
            username = relay_server.username
            password = relay_server.password
            auth = bytes((_AUTH_VERSION, len(username))) + username
            auth += bytes((len(password),)) + password
            upstream.sendall(auth)
            auth_version, auth_status = _recv_exact(upstream, 2)
            if auth_version != _AUTH_VERSION or auth_status != 0:
                _send_reply(client, _REP_GENERAL_FAILURE)
                return

            upstream.sendall(request)
            reply = _read_reply(upstream)
            client.sendall(reply)
            reply_sent = True
            if reply[1] != _REP_SUCCEEDED:
                return

            client.settimeout(None)
            upstream.settimeout(None)
            _tunnel(client, upstream)
        except (ConnectionError, OSError, ValueError):
            if local_ready and not reply_sent:
                try:
                    _send_reply(client, _REP_GENERAL_FAILURE)
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()


def _create_relay_handler(
    request: Any, client_address: Any, server: _RelayServer,
) -> socketserver.BaseRequestHandler:
    return _RelayHandler(request, client_address, server)


class Socks5AuthRelay:
    """Expose an authenticated upstream SOCKS5 proxy on a loopback port."""

    def __init__(self, server: str, username: str, password: str) -> None:
        parsed = urlsplit(server)
        if parsed.scheme != "socks5" or not parsed.hostname or parsed.port is None:
            raise ValueError("Expected a SOCKS5 proxy address with a port")
        username_bytes = username.encode("utf-8")
        password_bytes = password.encode("utf-8")
        if not username_bytes or not password_bytes:
            raise ValueError("SOCKS5 authentication requires a username and password")
        if len(username_bytes) > 255 or len(password_bytes) > 255:
            raise ValueError("SOCKS5 credentials must be at most 255 bytes each")

        self._server = _RelayServer(("127.0.0.1", 0), _create_relay_handler)
        self._server.upstream_host = parsed.hostname
        self._server.upstream_port = parsed.port
        self._server.username = username_bytes
        self._server.password = password_bytes
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._started = False
        self._closed = False

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> int:
        if self._closed:
            raise RuntimeError("SOCKS5 relay is closed")
        if not self._started:
            self._thread.start()
            self._started = True
        return self.port

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            self._server.shutdown()
        self._server.server_close()
        if self._started:
            self._thread.join(timeout=2)
