"""Broker checks and real-loopback desktop transport contracts."""
import http.client
import json
import queue
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest

from atlas_toolkit.app.events import EventBroker
from atlas_toolkit.app.files import DesktopFiles
from atlas_toolkit.app.rpc_frame import decode_frame, encode_frame
from atlas_toolkit.app.server import DesktopServer


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
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
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
    for method in ("POST", "PUT", "DELETE", "HEAD", "OPTIONS", "CUSTOM"):
        for path in ("index.html", "api/events", "asset/missing"):
            assert request(method, path)[0] == 405
    assert request("GET", "api/rpc")[0] == 405
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
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
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
            assert host.calls == ["enter"]
        finally:
            host.release.set()
        slow.result(timeout=2)
        fast.result(timeout=2)
    assert host.calls == ["enter", "exit", "echo"]
    assert not server.unsaved


def test_socket_events_and_assets(live):
    _, _, _, files, events, request, _, tmp_path, _ = live
    events.publish("ready", value="雪")
    conn, response = request(path="api/events")
    try:
        assert response.getheader("Content-Type") == "text/event-stream"
        assert response.readline() == 'data: {"kind":"ready","value":"雪"}\n'.encode()
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
