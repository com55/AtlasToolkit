# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Broker checks and real-loopback desktop transport contracts."""
import http.client
import json
import queue
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from urllib.parse import urlsplit
from unittest.mock import Mock

import pytest

from atlas_toolkit.app.events import EventBroker
from atlas_toolkit.app.files import DesktopFiles
from atlas_toolkit.app.rpc_frame import decode_frame, encode_frame
from atlas_toolkit.app.server import DesktopServer, _HTTPServer


def test_broker_backlog_and_live():
    broker = EventBroker()
    for i in range(105):
        broker.publish("queued", index=i)
    first = broker.subscribe()
    assert broker.has_subscriber
    assert [next(first)["index"] for _ in range(100)] == list(range(5, 105))
    second = broker.subscribe()
    broker.publish("live", value="雪")
    assert next(first) == next(second) == {"kind": "live", "value": "雪"}
    first.close()
    second.close()
    assert not broker.has_subscriber
    third = broker.subscribe()
    with pytest.raises(queue.Empty):
        third.get(timeout=0.01)
    broker.close()
    assert list(third) == []


def test_broker_close_wakes_all():
    broker = EventBroker()
    subscriptions = [broker.subscribe(), broker.subscribe()]
    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(list, sub) for sub in subscriptions]
        broker.close()
        assert [f.result(timeout=1) for f in results] == [[], []]
    assert list(broker.subscribe()) == []
    broker.publish("ignored")
    broker.close()


@pytest.mark.parametrize("value, error", [(object(), TypeError), ([], ValueError)])
def test_broker_rejects_unserializable_event(value, error):
    if isinstance(value, list):
        value.append(value)
    broker = EventBroker()
    with pytest.raises(error):
        broker.publish("invalid", value=value)
    subscription = broker.subscribe()
    with pytest.raises(queue.Empty):
        subscription.get(timeout=0.01)
    subscription.close()


def test_server_disallows_address_reuse():
    assert _HTTPServer.allow_reuse_address is False


@pytest.mark.parametrize("alive", [False, True])
def test_stop_lifecycle_without_socket(alive):
    server = DesktopServer.__new__(DesktopServer)
    server._stopped = False
    server._http = Mock()
    server._thread = Mock()
    server._thread.is_alive.return_value = alive
    server.events = Mock()
    calls = Mock()
    calls.attach_mock(server.events.close, "close_events")
    calls.attach_mock(server._http.shutdown, "shutdown")
    calls.attach_mock(server._thread.join, "join")
    calls.attach_mock(server._http.server_close, "server_close")
    server.stop()
    server.stop()
    expected = ["close_events", "shutdown", "join", "server_close"] if alive else ["server_close"]
    assert [call[0] for call in calls.mock_calls] == expected


def test_socket_stop_before_start(tmp_path):
    server = DesktopServer(tmp_path, Host(), Commands(), DesktopFiles(), EventBroker())
    done = threading.Event()

    def stop():
        try:
            server.stop()
            server.stop()
        finally:
            done.set()

    thread = threading.Thread(target=stop, daemon=True)
    thread.start()
    assert done.wait(2)
    thread.join()
    assert server._http.socket.fileno() == -1


def test_socket_stop_twice(live):
    server = live[0]
    server.stop()
    server.stop()
    assert not server._thread.is_alive()
    assert server._http.socket.fileno() == -1


class Host:
    def __init__(self):
        self.calls = []
        self.pending = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def has_pending(self):
        return self.pending

    def echo(self, value):
        self.calls.append("echo")
        return value

    def modify(self):
        self.calls.append("enter")
        self.entered.set()
        assert self.release.wait(2)
        self.calls.append("exit")
        return None

    def fail(self):
        raise ValueError("failure")

    def surrogate(self):
        return "\ud800"


class Commands:
    RPC_METHODS = frozenset({"ping"})

    def __init__(self):
        self.secret_called = False

    def ping(self):
        return "pong"

    def secret(self):
        self.secret_called = True


@pytest.fixture
def live(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>test</html>")
    (site / "app.js").write_text("// test")
    for suffix in ("mjs", "css", "json", "png", "ico", "svg", "webmanifest", "bin"):
        (site / ("sample." + suffix)).write_bytes(b"sample")
    (tmp_path / "outside").write_bytes(b"private")
    (site / "escape").symlink_to(tmp_path / "outside")
    host, commands, files, events = Host(), Commands(), DesktopFiles(), EventBroker()
    server = DesktopServer(site, host, commands, files, events, ping_interval=0.02)
    url = urlsplit(server.start())
    prefix = url.path.removesuffix("index.html")

    def request(method="GET", path="index.html", body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        conn.request(method, path if raw else prefix + path, body, headers or {})
        response = conn.getresponse()
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert not any(k.lower().startswith("access-control-") for k, _ in response.getheaders())
        if path == "api/events" and response.status == 200:
            return conn, response
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def rpc(method, target="engine", args=None, buffers=None):
        body = encode_frame({"target": target, "method": method,
                             "argsJson": json.dumps(args or [])}, buffers or [])
        status, _, data = request("POST", "api/rpc", body, {"Origin": f"http://{url.netloc}"})
        assert status == 200
        header, output = decode_frame(data)
        return json.loads(header["json"]), output

    try:
        yield server, host, commands, files, events, request, rpc, tmp_path, url
    finally:
        host.release.set()
        server.stop()


def test_socket_security_and_static(live):
    _, _, _, _, _, request, _, _, url = live
    assert request(path="/index.html", raw=True)[0] == 404
    assert request(path="/s/wrong/index.html", raw=True)[0] == 404
    assert request(path="/missing", raw=True, headers={"Host": "bad"})[0] == 403
    for origin in (None, "http://evil.test"):
        assert request("POST", "api/rpc", b"", {} if origin is None else {"Origin": origin})[0] == 403
    for path, mime in [("", "text/html"), ("index.html?x=1", "text/html"), ("app.js", "text/javascript")]:
        status, headers, _ = request(path=path)
        assert status == 200 and headers["Content-Type"] == mime
    for suffix, mime in [("mjs", "text/javascript"), ("css", "text/css"),
                         ("json", "application/json"), ("png", "image/png"),
                         ("ico", "image/x-icon"), ("svg", "image/svg+xml"),
                         ("webmanifest", "application/manifest+json"),
                         ("bin", "application/octet-stream")]:
        status, headers, _ = request(path="sample." + suffix)
        assert status == 200 and headers["Content-Type"] == mime
    for path in ("../outside", "%2e%2e/outside", "escape", "missing", "."):
        assert request(path=path)[0] == 404
    for method in ("POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
        for path in ("index.html", "api/events", "asset/missing"):
            assert request(method, path)[0] == 405
    assert request("GET", "api/rpc")[0] == 405
    assert request("CUSTOM")[0] == 501
    assert request("POST", "api/rpc", b"bad", {"Origin": f"http://{url.netloc}"})[0] == 400


def test_socket_rpc_validation(live):
    *_, request, rpc, tmp_path, url = live
    origin = {"Origin": f"http://{url.netloc}"}
    for header in ({}, {"target": "unknown", "method": "x", "argsJson": "[]"},
                   {"target": "engine", "method": 2, "argsJson": "[]"}):
        assert request("POST", "api/rpc", encode_frame(header, []), origin)[0] == 400
    raw = b'[' * 2000 + b'0' + b']' * 2000
    assert request("POST", "api/rpc", struct.pack("<I", len(raw)) + raw, origin)[0] == 400
    assert request("POST", "api/rpc", b"", {**origin, "Content-Length": str(2**31 + 1)})[0] == 413
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
    try:
        conn.putrequest("POST", url.path.removesuffix("index.html") + "api/rpc")
        conn.putheader("Origin", f"http://{url.netloc}")
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 411
        response.read()
    finally:
        conn.close()


def test_socket_rpc_roundtrip_and_errors(live):
    server, host, commands, _, _, _, rpc, _, _ = live
    value, buffers = rpc("echo", args=[{"$buf": 0}], buffers=[b"\x00\xff"])
    assert value == {"ok": True, "value": {"$buf": 0}} and buffers == [b"\x00\xff"]
    assert rpc("ping", "desktop")[0] == {"ok": True, "value": "pong"}
    assert rpc("secret", "desktop")[0]["error"]["type"] == "ValueError"
    assert not commands.secret_called
    host.pending = True
    assert rpc("fail")[0]["error"]["type"] == "ValueError"
    assert server.unsaved
    host.pending = False
    assert rpc("surrogate")[0]["error"]["type"] == "EncodeError"
    assert not server.unsaved


def test_socket_engine_lock_and_unsaved(live):
    server, host, _, _, _, _, rpc, _, _ = live
    with ThreadPoolExecutor(2) as pool:
        slow = pool.submit(rpc, "modify")
        try:
            assert host.entered.wait(1)
            assert server.unsaved
            started = threading.Event()

            def echo():
                started.set()
                return rpc("echo", args=[1])

            fast = pool.submit(echo)
            assert started.wait(1)
            with pytest.raises(TimeoutError):
                fast.result(timeout=0.3)
            assert host.calls == ["enter"]
        finally:
            host.release.set()
        assert slow.result(timeout=2) == ({"ok": True, "value": None}, [])
        assert fast.result(timeout=2) == ({"ok": True, "value": 1}, [])
    assert host.calls == ["enter", "exit", "echo"]
    assert not server.unsaved


def test_socket_events_and_assets(live):
    _, _, _, files, events, request, _, tmp_path, _ = live
    events.publish("ready", value="雪")
    conn, response = request(path="api/events")
    try:
        assert response.getheader("Content-Type") == "text/event-stream"
        assert response.getheader("Connection") == "close"
        assert response.readline() == b'data: {"kind":"ready","value":"\\u96ea"}\n'
        assert response.readline() == b"\n"
        assert response.readline() == b": ping\n"
        assert response.readline() == b"\n"
    finally:
        response.close()
        conn.close()
    assert request(path="asset/missing")[0] == 404
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"asset")
    registered = files.register(asset)["url"]
    status, headers, data = request(path=registered)
    assert status == 200 and data == b"asset"
    assert headers["Content-Type"] == "application/octet-stream"
    asset.unlink()
    assert request(path=registered)[0] == 404


def test_socket_events_surrogate_keeps_stream_alive(live):
    _, _, _, _, events, request, _, _, _ = live
    events.publish("surrogate", value="\udcff")
    conn, response = request(path="api/events")
    try:
        assert response.readline() == b'data: {"kind":"surrogate","value":"\\udcff"}\n'
        assert response.readline() == b"\n"
        events.publish("next", value="alive")
        while True:
            line = response.readline()
            assert line, "event stream closed after surrogate"
            if line.startswith(b"data: "):
                break
        assert line == b'data: {"kind":"next","value":"alive"}\n'
        assert response.readline() == b"\n"
    finally:
        response.close()
        conn.close()


@pytest.mark.parametrize("case, expected", [
    ("missing_host", 403), ("duplicate_host", 403),
    ("encoded_rpc", 405), ("head_rpc", 405),
])
def test_socket_raw_requests(live, case, expected):
    url = live[-1]
    prefix = url.path.removesuffix("index.html")
    method = "HEAD" if case == "head_rpc" else "GET"
    path = prefix + ("api%2Frpc" if case == "encoded_rpc" else "api/rpc")
    hosts = "" if case == "missing_host" else f"Host: {url.netloc}\r\n"
    if case == "duplicate_host":
        hosts *= 2
    request = f"{method} {path} HTTP/1.0\r\n{hosts}\r\n".encode("ascii")
    with socket.create_connection((url.hostname, url.port), timeout=30) as client:
        client.sendall(request)
        with client.makefile("rb") as response:
            assert int(response.readline().split()[1]) == expected


def test_socket_dead_sse_unsubscribes_with_default_ping(tmp_path):
    events = EventBroker()
    server = DesktopServer(tmp_path, Host(), Commands(), DesktopFiles(), events)
    assert server.ping_interval == 15.0
    url = urlsplit(server.start())
    prefix = url.path.removesuffix("index.html")
    try:
        with socket.create_connection((url.hostname, url.port), timeout=30) as client:
            client.sendall(f"GET {prefix}api/events HTTP/1.0\r\nHost: {url.netloc}\r\n\r\n".encode())
            with client.makefile("rb") as response:
                assert int(response.readline().split()[1]) == 200
                while True:
                    line = response.readline()
                    assert line, "EOF before SSE headers ended"
                    if line == b"\r\n":
                        break
                assert events.has_subscriber
        deadline = time.monotonic() + 3
        while events.has_subscriber and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not events.has_subscriber
    finally:
        server.stop()


def test_static_path_containment(tmp_path):
    from atlas_toolkit.app.server import _static_path

    site = tmp_path / "site"
    site.mkdir()
    index = site / "index.html"
    index.write_bytes(b"index")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (site / "link").symlink_to(outside)
    assert _static_path(site, "") == index
    for path in ("../outside", str(outside), "link", ".", "missing", "\x00"):
        assert _static_path(site, path) is None


@pytest.fixture
def short_timeout_live(live):
    live[0]._http.RequestHandlerClass.timeout = 0.2
    return live


@pytest.mark.parametrize("partial", [b"", b"GET / HTTP/1.0\r\nHost:"])
def test_socket_idle_and_partial_headers_timeout(short_timeout_live, caplog, partial):
    server, *_, url = short_timeout_live
    caplog.set_level("DEBUG", logger="atlas_toolkit.app.server")
    started = time.monotonic()
    with socket.create_connection((url.hostname, url.port), timeout=2) as client:
        if partial:
            client.sendall(partial)
        assert client.recv(1) == b""
    assert time.monotonic() - started < 2
    assert "connection opened " in caplog.text
    if not partial:
        deadline = time.monotonic() + 1
        while "connection idle timeout" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "connection idle timeout" in caplog.text
    assert server._token not in caplog.text


def test_socket_quiet_sse_survives_connection_timeout(short_timeout_live):
    server, _, _, _, events, request, _, _, _ = short_timeout_live
    server.ping_interval = 15
    conn, response = request(path="api/events")
    try:
        time.sleep(0.6)
        events.publish("after-idle", value="alive")
        assert response.readline() == b'data: {"kind":"after-idle","value":"alive"}\n'
        assert response.readline() == b"\n"
    finally:
        response.close()
        conn.close()


def test_socket_slow_rpc_body_survives_connection_timeout(short_timeout_live, caplog):
    server, *_, url = short_timeout_live
    caplog.set_level("DEBUG", logger="atlas_toolkit.app.server")
    body = encode_frame({"target": "desktop", "method": "ping", "argsJson": "[]"}, [])
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
    try:
        conn.putrequest("POST", url.path.removesuffix("index.html") + "api/rpc")
        conn.putheader("Origin", f"http://{url.netloc}")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        time.sleep(0.6)
        conn.send(body)
        response = conn.getresponse()
        assert response.status == 200
        header, buffers = decode_frame(response.read())
        assert json.loads(header["json"]) == {"ok": True, "value": "pong"}
        assert buffers == []
        assert "request POST /s/<token>/api/rpc" in caplog.text
        assert server._token not in caplog.text
    finally:
        conn.close()


def test_connection_timeout_defaults_to_ten_seconds(live):
    assert live[0]._http.RequestHandlerClass.timeout == 10.0


def test_server_startup_backlog():
    assert _HTTPServer.request_queue_size == 128


def test_socket_startup_burst(live):
    url = live[-1]
    barrier = threading.Barrier(60)

    def get():
        barrier.wait(timeout=10)
        with socket.create_connection((url.hostname, url.port), timeout=10) as client:
            client.sendall(f"GET {url.path} HTTP/1.1\r\nHost: {url.netloc}\r\nConnection: close\r\n\r\n".encode())
            response = http.client.HTTPResponse(client)
            response.begin()
            assert response.status == 200
            assert response.read() == b"<html>test</html>"
            response.close()

    with ThreadPoolExecutor(60) as pool:
        results = [pool.submit(get) for _ in range(60)]
        for result in results:
            result.result(timeout=15)


def test_socket_keep_alive_two_gets(short_timeout_live):
    url = short_timeout_live[-1]
    with socket.create_connection((url.hostname, url.port), timeout=2) as client:
        for _ in range(2):
            client.sendall(f"GET {url.path} HTTP/1.1\r\nHost: {url.netloc}\r\n\r\n".encode())
            response = http.client.HTTPResponse(client)
            response.begin()
            assert response.version == 11
            assert response.status == 200
            assert response.read() == b"<html>test</html>"
            response.close()
        assert client.recv(1) == b""  # Idle keep-alive still has a deadline.


@pytest.mark.parametrize("path, headers, expected", [
    ("api/rpc", "Content-Length: 4\r\n", 403),
    ("api/boot-error", "Content-Length: 4\r\n", 403),
    ("missing", "Content-Length: 4\r\n", 405),
    ("api/rpc", "Origin: {origin}\r\n", 411),
    ("api/rpc", "Origin: {origin}\r\nContent-Length: bad\r\n", 400),
    ("api/rpc", "Origin: {origin}\r\nContent-Length: 2147483649\r\n", 413),
])
def test_socket_early_rejection_closes(live, path, headers, expected):
    url = live[-1]
    prefix = url.path.removesuffix("index.html")
    headers = headers.format(origin=f"http://{url.netloc}")
    with socket.create_connection((url.hostname, url.port), timeout=2) as client:
        client.sendall((f"POST {prefix}{path} HTTP/1.1\r\nHost: {url.netloc}\r\n" +
                        headers + "\r\nBODY" +
                        f"GET {url.path} HTTP/1.1\r\nHost: {url.netloc}\r\n\r\n").encode())
        response = http.client.HTTPResponse(client)
        response.begin()
        assert response.status == expected
        assert response.getheader("Connection") == "close"
        response.read()
        response.close()
        assert client.recv(1) == b""


def test_socket_boot_error(live, caplog):
    server, *_, url = live
    request = live[5]
    origin = {"Origin": f"http://{url.netloc}"}
    assert request("POST", "api/boot-error", b"failed import")[0] == 403
    assert request("POST", "api/boot-error", b"x" * 4097, origin)[0] == 413
    body = f"failed import /s/{server._token}/script.js".encode()
    status, headers, data = request("POST", "api/boot-error", body, origin)
    assert status == 204 and data == b"" and headers["Content-Length"] == "0"
    assert "boot error from page: failed import /s/<token>/script.js" in caplog.text
    assert server._token not in caplog.text
