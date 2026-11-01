"""Read-only checks against an ESP32 gateway endpoint.

Nothing here writes to the device, changes its mode or opens a serial port.
Results describe what the network endpoint answered, not the electrical state
of the target board.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from .rfc2217 import COM_PORT_OPTION, TelnetParser, subnegotiation

_MAX_HTTP_BYTES = 64 * 1024


def _host_ok(host: str) -> str:
    host = host.strip()
    if not host or len(host) > 253 or any(c.isspace() for c in host):
        raise ValueError("Host must be an IP address or a host name.")
    return host


def tcp_reachable(host: str, port: int, timeout_s: float = 2.0) -> dict[str, Any]:
    host = _host_ok(host)
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return {"ok": True, "ms": round((time.monotonic() - start) * 1000, 1)}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__, "code": getattr(exc, "winerror", None) or exc.errno}


def rfc2217_probe(host: str, port: int, baudrate: int = 115200, timeout_s: float = 3.0) -> dict[str, Any]:
    """Send one SET-BAUDRATE request and report whether a reply arrives.

    The request changes the remote UART speed, so it is only used when the
    user explicitly runs a device check; the next real session sets its own
    speed. No data bytes are sent.
    """
    host = _host_ok(host)
    if not 300 <= baudrate <= 4_000_000:
        raise ValueError("Probe baud rate out of range.")
    result: dict[str, Any] = {"ok": False, "reply": None, "busy": False}
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            sock.sendall(subnegotiation(1, baudrate.to_bytes(4, "big")))
            parser = TelnetParser()
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                chunk = sock.recv(512)
                if not chunk:
                    result["busy"] = True  # server closed: another session or not RFC2217
                    break
                for kind, value in parser.feed(chunk):
                    if kind == "subnegotiation" and len(value) >= 6 and value[0] == COM_PORT_OPTION and value[1] == 101:
                        result.update(ok=True, reply=int.from_bytes(value[2:6], "big"))
                        return result
    except socket.timeout:
        result["error"] = "timeout"
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        result["busy"] = True  # accepted then dropped: another session holds the port
    except OSError as exc:
        result["error"] = type(exc).__name__
    return result


def http_status(host: str, path: str = "/api/maintenance/status", token: str = "", header: str = "X-Gateway-Token",
                http_port: int = 80, timeout_s: float = 3.0) -> dict[str, Any]:
    """GET a JSON status document. The token is sent only in a header and never logged."""
    host = _host_ok(host)
    if not path.startswith("/") or any(c in path for c in "\r\n "):
        raise ValueError("Path must be an absolute URL path.")
    if not header.replace("-", "").isalnum():
        raise ValueError("Invalid header name.")
    request = urllib.request.Request(f"http://{host}:{http_port}{path}", method="GET")
    if token:
        request.add_header(header, token)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read(_MAX_HTTP_BYTES + 1)
            if len(body) > _MAX_HTTP_BYTES:
                return {"ok": False, "error": "response too large"}
            return {"ok": True, "status": response.status, "json": json.loads(body.decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": "http"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


def scan_subnet(network: str, port: int = 3333, timeout_s: float = 0.4, limit: int = 256) -> list[str]:
    """Find hosts with an open TCP port in a small private subnet (at most /24)."""
    net = ipaddress.ip_network(network, strict=False)
    if net.version != 4 or not net.is_private or net.num_addresses > limit:
        raise ValueError("Scan only private IPv4 networks of /24 or smaller.")
    import concurrent.futures

    def check(address: str) -> str | None:
        try:
            with socket.create_connection((address, port), timeout=timeout_s):
                return address
        except OSError:
            return None

    hosts = [str(h) for h in net.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        return sorted((h for h in pool.map(check, hosts) if h), key=ipaddress.ip_address)


def local_networks() -> list[str]:
    """Private /24 networks of this computer's IPv4 addresses (no traffic sent)."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private and not address.is_loopback:
                found.add(str(ipaddress.ip_network(f"{address}/24", strict=False)))
    except OSError:
        pass
    return sorted(found)


def check_device(host: str, port: int = 3333, http_port: int = 80, status_path: str = "/api/maintenance/status",
                 token: str = "", probe_baud: int = 0, token_header: str = "X-Gateway-Token") -> dict[str, Any]:
    report: dict[str, Any] = {"host": host, "port": port, "tcp": tcp_reachable(host, port)}
    if report["tcp"]["ok"] and probe_baud:
        report["rfc2217"] = rfc2217_probe(host, port, probe_baud)
    if status_path:
        report["http"] = http_status(host, status_path, token, header=token_header, http_port=http_port)
    return report
