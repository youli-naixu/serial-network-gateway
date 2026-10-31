from __future__ import annotations

import hashlib
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .models import GatewayProfile


def _wait_snapshot(service: Any, predicate: Callable[[dict], bool], timeout: float = 6.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = service.snapshot()
        if predicate(state):
            return state
        if state.get("state") == "error":
            raise RuntimeError(state.get("last_error") or state.get("message", "网关启动失败"))
        time.sleep(0.01)
    raise TimeoutError(f"等待状态超时：{service.snapshot().get('state')}")


def _read_serial_exact(client: Any, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = client.read(count - len(data))
        if not chunk:
            raise TimeoutError(f"模拟回读不足：{len(data)}/{count}")
        data.extend(chunk)
    return bytes(data)


def run_selftest(payload_size: int = 65536) -> dict[str, Any]:
    """Exercise loopback-only endpoints. Never load a device profile or open COM."""
    if not isinstance(payload_size, int) or not 256 <= payload_size <= 16 * 1024 * 1024:
        raise ValueError("自检数据量应在 256–16777216 字节之间。")
    from .engine import GatewayService

    started = time.monotonic()
    result: dict[str, Any] = {
        "ok": False,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "本机模拟；没有打开物理COM或访问真实ESP32/ASR",
        "checks": [],
    }
    service = GatewayService()
    client = None
    profile = GatewayProfile(name="离线自检", listen_port=0, buffer_bytes=4096, chunk_bytes=257)
    checks = result["checks"]

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(passed), "detail": detail})
        if not passed:
            raise AssertionError(name + (": " + detail if detail else ""))

    try:
        import serial

        service.start(profile)
        ready = _wait_snapshot(service, lambda s: s.get("state") == "ready")
        host, port = ready["listen_host"], ready["listen_port"]
        check("仅本机临时监听", host in ("127.0.0.1", "::1") and port > 0, f"{host}:{port}")
        url = f"rfc2217://{host}:{port}"
        client = serial.serial_for_url(url, baudrate=115200, timeout=4)
        _wait_snapshot(service, lambda s: s.get("state") == "active")
        client.baudrate = 1500000
        client.bytesize = 7
        client.parity = "E"
        client.stopbits = 2
        client.dtr = False
        client.rts = False
        client.break_condition = True
        state = _wait_snapshot(service, lambda s: s.get("serial_confirmed", {}).get("baudrate") == 1500000 and s.get("serial_confirmed", {}).get("break") is True)
        confirmed = state["serial_confirmed"]
        check("模拟远端接受动态串口与控制设置", confirmed.get("bytesize") == 7 and confirmed.get("parity") == "E" and confirmed.get("stopbits") == 2 and confirmed.get("dtr") is False and confirmed.get("rts") is False, str(confirmed))
        client.break_condition = False
        client.bytesize = 8
        client.parity = "N"
        client.stopbits = 1
        client.dtr = True
        client.rts = True
        payload = (bytes(range(256)) * ((payload_size + 255) // 256))[:payload_size]
        for offset in range(0, len(payload), 997):
            client.write(payload[offset:offset + 997])
        received = _read_serial_exact(client, len(payload))
        check("全二进制及0xFF转义往返一致", received == payload, f"{len(payload)}字节，SHA256 {hashlib.sha256(received).hexdigest()}")
        state = _wait_snapshot(service, lambda s: s.get("payload_from_remote", 0) >= len(payload))
        check("双向净数据计数", state.get("payload_from_client") == len(payload) and state.get("payload_from_remote") == len(payload), f"发送/接收 {state.get('payload_from_client')}/{state.get('payload_from_remote')}")
        check("应用队列峰值有界", state.get("peak_to_remote", 0) <= profile.buffer_bytes and state.get("peak_to_client", 0) <= profile.buffer_bytes)
        with socket.create_connection((host, port), timeout=2) as second:
            second.settimeout(2)
            try:
                rejected = second.recv(1) == b""
            except ConnectionResetError:
                rejected = True
            check("拒绝第二个并发客户端", rejected)
        client.close()
        client = None
        state = _wait_snapshot(service, lambda s: s.get("state") == "needs_reset")
        check("会话结束后禁止自动续传", state["state"] == "needs_reset")
        with socket.create_connection((host, port), timeout=2) as stale:
            stale.settimeout(2)
            try:
                rejected = stale.recv(1) == b""
            except ConnectionResetError:
                rejected = True
            check("重新就绪前拒绝重连", rejected)
        check("显式重新就绪", service.rearm())
        _wait_snapshot(service, lambda s: s.get("state") == "ready")
        client = serial.serial_for_url(url, baudrate=57600, timeout=3)
        marker = b"new-session\x00\xff\x11\x13"
        client.write(marker)
        check("新会话不携带旧数据", _read_serial_exact(client, len(marker)) == marker)
        client.close()
        client = None
        _wait_snapshot(service, lambda s: s.get("state") == "needs_reset")
        result["snapshot"] = service.snapshot()
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if not checks or checks[-1].get("ok"):
            checks.append({"name": "自检执行", "ok": False, "detail": result["error"]})
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
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result
