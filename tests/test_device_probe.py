import http.server
import json
import socket
import threading

import pytest

from serial_gateway import device_probe
from serial_gateway.rfc2217 import TelnetParser, subnegotiation


def _rfc2217_server(reply=True, close=False):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = []

    def run():
        try:
            conn, _ = srv.accept()
            with conn:
                if close:
                    return
                parser = TelnetParser()
                data = conn.recv(256)
                for kind, value in parser.feed(data):
                    if kind == "subnegotiation":
                        seen.append(value)
                        if reply:
                            conn.sendall(subnegotiation(value[1] + 100, value[2:]))
                try:
                    conn.recv(16)
                except OSError:
                    pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], seen


def test_tcp_reachable_open_and_closed():
    port, _ = _rfc2217_server(close=True)
    assert device_probe.tcp_reachable("127.0.0.1", port)["ok"] is True
    free = socket.socket(); free.bind(("127.0.0.1", 0)); closed = free.getsockname()[1]; free.close()
    assert device_probe.tcp_reachable("127.0.0.1", closed, timeout_s=0.5)["ok"] is False


def test_rfc2217_probe_reads_baud_reply_and_sends_no_data():
    port, seen = _rfc2217_server()
    result = device_probe.rfc2217_probe("127.0.0.1", port, 921600)
    assert result == {"ok": True, "reply": 921600, "busy": False}
    assert seen and seen[0][1] == 1


def test_rfc2217_probe_reports_busy_when_server_closes():
    port, _ = _rfc2217_server(close=True)
    result = device_probe.rfc2217_probe("127.0.0.1", port, 115200, timeout_s=2)
    assert result["ok"] is False and result["busy"] is True


class _Handler(http.server.BaseHTTPRequestHandler):
    received = {}

    def do_GET(self):  # noqa: N802
        _Handler.received = dict(self.headers)
        body = json.dumps({"state": "listening", "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_http_status_sends_token_only_as_header():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    result = device_probe.http_status("127.0.0.1", token="t0k", http_port=server.server_address[1])
    server.server_close()
    assert result["ok"] and result["json"]["state"] == "listening"
    assert _Handler.received.get("X-Gateway-Token") == "t0k"
    assert "t0k" not in json.dumps(result)


@pytest.mark.parametrize("bad", ["", "a b", "x" * 300])
def test_host_validation(bad):
    with pytest.raises(ValueError):
        device_probe.tcp_reachable(bad, 1)


@pytest.mark.parametrize("network", ["8.8.8.0/24", "10.0.0.0/16", "::1/128"])
def test_scan_refuses_public_or_large_networks(network):
    with pytest.raises(ValueError):
        device_probe.scan_subnet(network)


def test_scan_finds_listener_on_loopback_range():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4)
    port = srv.getsockname()[1]
    try:
        # 127.0.0.0/30 is loopback, which ip_network marks private.
        assert device_probe.scan_subnet("127.0.0.0/30", port=port, timeout_s=0.3) == ["127.0.0.1"]
    finally:
        srv.close()
