"""Socket lifecycle regressions; these are not service performance measurements."""
import http.client
import os
import socket

import pytest

from performance.ctx import ConnectionRegistry, _cancel_socket


@pytest.mark.skipif(os.name == "nt", reason="POSIX plaintext cancellation contract")
def test_cancel_wakes_peer_without_closing_reader_owned_fd():
    reader, peer = socket.socketpair()
    try:
        fd = reader.fileno()
        peer.settimeout(1)
        _cancel_socket(reader)
        assert reader.fileno() == fd
        assert peer.recv(1) == b""
    finally:
        reader.close()
        peer.close()


def test_normal_close_releases_detached_response_file():
    reader, peer = socket.socketpair()
    try:
        peer.settimeout(1)
        peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nConnection: close\r\n\r\n")
        response = http.client.HTTPResponse(reader)
        response.begin()
        reader.close()
        registry = ConnectionRegistry()
        registry.register_response(response)
        registry.close_all()
        assert response.isclosed()
        assert len(registry) == 0
        assert peer.recv(1) == b""
    finally:
        reader.close()
        peer.close()
