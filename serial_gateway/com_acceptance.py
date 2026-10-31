"""Explicit acceptance check for an already created Windows virtual COM port."""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from .diagnostics import _wait_snapshot
from .models import GatewayProfile


_VIRTUAL_HWID = re.compile(r"(?:^|[\\& _])(?:COM0COM|EVSERIAL|EVSBC)(?:$|[\\& _])", re.I)


def verify_virtual_identity(port: str, ports: list[Any] | None = None) -> dict[str, str]:
    if not re.fullmatch(r"COM[1-9][0-9]{0,3}", port):
        raise ValueError("A Windows COM name is required.")
    if ports is None:
        from serial.tools.list_ports import comports
        ports = list(comports())
    matches = [entry for entry in ports if entry.device.upper() == port]
    if len(matches) != 1:
        raise ValueError("The requested port is absent or ambiguous; no port was opened.")
    entry = matches[0]
    hwid = entry.hwid or ""
    if not _VIRTUAL_HWID.search(hwid) or "USB" in hwid.upper() or "BTH" in hwid.upper():
        raise ValueError("Port identity is not a supported virtual driver (com0com/Eltima); no port was opened.")
    return {"device": entry.device, "description": entry.description or "", "hwid": hwid}


def run_com_acceptance(port: str, listen_port: int = 7000, payload_size: int = 65536) -> dict[str, Any]:
    if os.name != "nt":
        raise ValueError("Virtual COM acceptance requires Windows.")
    if not 256 <= payload_size <= 1024 * 1024:
        raise ValueError("Payload size must be between 256 and 1048576 bytes.")
    identity = verify_virtual_identity(port)
    from .engine import GatewayService
    import serial

    profile = GatewayProfile(name="Virtual COM acceptance", mode="simulator", protocol="rfc2217", listen_port=listen_port)
    profile.validate()
    service = GatewayService()
    result: dict[str, Any] = {
        "ok": False, "scope": "Virtual COM driver to loopback RFC2217 simulator; no target hardware",
        "started_utc": datetime.now(timezone.utc).isoformat(), "checks": [],
        "driver": {"description": identity["description"], "type": "supported virtual COM"},
    }
    started = time.monotonic()
    client = None

    def check(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            raise AssertionError(name)

    try:
        service.start(profile)
        _wait_snapshot(service, lambda s: s["state"] == "ready")
        client = serial.Serial(port=port, baudrate=115200, timeout=5, write_timeout=5)
        _wait_snapshot(service, lambda s: s["state"] == "active", timeout=10)
        check("Ordinary Windows COM opened and connected", True)
        client.baudrate = 1500000
        client.bytesize = 7
        client.parity = "E"
        client.stopbits = 2
        client.dtr = False
        client.rts = False
        client.break_condition = True
        expected = {"baudrate": 1500000, "bytesize": 7, "parity": "E", "stopbits": 2, "dtr": False, "rts": False, "break": True}
        state = _wait_snapshot(service, lambda s: all(s.get("serial_requested", {}).get(k) == v for k, v in expected.items()), timeout=8)
        check("COM settings and DTR/RTS/BREAK reached the network", all(state["serial_requested"].get(k) == v for k, v in expected.items()))
        client.break_condition = False
        client.bytesize = 8
        client.parity = "N"
        client.stopbits = 1
        client.dtr = True
        client.rts = True
        payload = (bytes(range(256)) * ((payload_size + 255) // 256))[:payload_size]
        received = bytearray()
        for offset in range(0, len(payload), 997):
            block = payload[offset:offset + 997]
            count = client.write(block)
            check_length = len(block)
            if count != check_length:
                raise IOError("Partial virtual COM write")
            while check_length:
                part = client.read(check_length)
                if not part:
                    raise TimeoutError("Virtual COM loopback read timed out")
                received.extend(part)
                check_length -= len(part)
        check("All binary bytes round-trip unchanged", bytes(received) == payload, hashlib.sha256(received).hexdigest())
        check("Application queues stayed bounded", service.snapshot()["peak_to_remote"] <= profile.buffer_bytes and service.snapshot()["peak_to_client"] <= profile.buffer_bytes)
        client.close()
        client = None
        state = _wait_snapshot(service, lambda s: s["state"] == "needs_reset", timeout=8)
        check("Closing COM ended the network session", state["state"] == "needs_reset")
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            service.stop()
        except Exception as exc:
            result["ok"] = False
            result["cleanup_error"] = type(exc).__name__
        result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result
