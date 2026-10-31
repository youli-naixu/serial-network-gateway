"""GUI 私有后台任务与诊断脱敏；本模块不访问 Tk。"""
from __future__ import annotations

import queue
import re
import threading
from dataclasses import dataclass
from typing import Any

from .models import GatewayProfile


@dataclass(frozen=True)
class Reply:
    task: str
    result: Any = None
    error: str | None = None


def clean_text(value: Any, limit: int = 1500) -> str:
    """用于事件及诊断的文本，隐藏常见凭据写法与 URL 中的认证信息。"""
    text = str(value).replace("\x00", "")
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)(\b(?:password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie)\b"
        r"\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]", text,
    )
    text = re.sub(r"(?is)(\bpayload(?:_from_client|_from_remote)?\s*[:=]\s*).*$", r"\1[omitted]", text)
    # Paths can contain account names. Redact quoted paths including spaces first.
    text = re.sub(r'''["'](?:[A-Za-z]:[\\/]|/|\\\\)[^"'\r\n]*["']''', "[path]", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\r\n]*|\\\\[^\r\n]*|(?<![:/\w])/(?:home|Users|tmp|var|opt|etc)/[^\r\n]*", "[path]", text)
    return text if len(text) <= limit else text[:limit] + "…"


SERIAL_FIELDS = (
    ("baudrate", "波特率"), ("baud", "波特率"), ("baud_rate", "波特率"),
    ("data_bits", "数据位"), ("databits", "数据位"), ("bytesize", "数据位"),
    ("data_size", "数据位"), ("parity", "校验"), ("stopbits", "停止位"),
    ("stop_bits", "停止位"), ("stop_size", "停止位"),
    ("flow_control", "流控"), ("flowcontrol", "流控"),
    ("xonxoff", "软件流控"), ("rtscts", "RTS/CTS"), ("dsrdtr", "DSR/DTR"),
    ("dtr", "DTR"), ("rts", "RTS"), ("break", "BREAK"),
)


def diagnostic_document(snapshot: dict[str, Any], profile: GatewayProfile, language: str = "zh-CN") -> dict[str, Any]:
    """Export only allowlisted metadata; omit names, paths and arbitrary event text."""
    from datetime import datetime, timezone
    from .i18n import translate, translate_event
    from .locales import EN

    def safe_message(value: Any) -> str:
        source = str(value or "")
        if source in EN:
            return translate(source, language)
        if re.fullmatch(r"网络错误（[A-Za-z_][A-Za-z0-9_]*，错误码 (?:-?\d+|None)）。(?: 请手动重新待命。)?", source) or re.fullmatch(r"代理错误（[A-Za-z_][A-Za-z0-9_]*）。(?: 请手动重新待命。)?", source):
            return translate_event(source, language)
        kinds = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b|\bE[A-Z_]{2,}\b", source)
        return "[redacted]" + (" (" + ", ".join(dict.fromkeys(kinds)) + ")" if kinds else "")

    profile_fields = (
        "mode", "protocol", "listen_host", "listen_port", "remote_port",
        "nodelay", "buffer_bytes", "chunk_bytes", "connect_timeout_s", "stall_timeout_s", "keepalive_s",
    )
    snapshot_fields = (
        "state", "message", "listen_host", "listen_port", "remote_port", "mode", "protocol",
        "session_id", "connections", "disconnects", "rejected_connections", "bytes_from_client",
        "bytes_to_remote", "bytes_from_remote", "bytes_to_client", "queue_to_remote", "queue_to_client",
        "peak_to_remote", "peak_to_client", "last_error",
    )

    def scalar(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return clean_text(value) if isinstance(value, str) else None

    safe_snapshot = {key: (safe_message(snapshot[key]) if key in ("message", "last_error") else scalar(snapshot[key])) for key in snapshot_fields if key in snapshot}
    for key in ("serial_requested", "serial_confirmed"):
        params = snapshot.get(key)
        safe_snapshot[key] = {
            field: scalar(params[field]) for field, _ in SERIAL_FIELDS if field in params
        } if isinstance(params, dict) else None
    events = snapshot.get("events", [])
    safe_snapshot["events"] = [
        {key: safe_message(event.get(key)) if key == "message" else scalar(event.get(key)) for key in ("time", "level", "message")}
        for event in events[-100:] if isinstance(event, dict)
    ] if isinstance(events, list) else []
    raw_profile = profile.to_dict()
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "scope": ("Gateway software diagnostics; hardware unverified; serial settings reflect protocol exchange only" if language == "en" else "网关软件诊断；真实硬件未验证；串口参数仅为请求与协议确认"),
        "language": language,
        "profile": {key: scalar(raw_profile[key]) for key in profile_fields if key in raw_profile},
        "snapshot": safe_snapshot,
    }


class GatewayWorker:
    """串行执行服务操作，保证关闭请求不会被尚未执行的启动覆盖。"""

    def __init__(self, replies: queue.Queue[Reply]) -> None:
        self._replies = replies
        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gateway-gui-service", daemon=True)
        self._thread.start()

    def submit(self, task: str, value: Any = None) -> None:
        if task == "shutdown":
            self._closing.set()
        self._commands.put((task, value))

    def cancel_shutdown(self) -> None:
        self._closing.clear()

    def _run(self) -> None:
        service = None
        while True:
            task, value = self._commands.get()
            try:
                if task == "start":
                    if self._closing.is_set():
                        self._replies.put(Reply(task, error="窗口正在关闭，已取消启动。"))
                        continue
                    if service is None:
                        from .engine import GatewayService
                        service = GatewayService()
                    service.start(value)
                    result = service.snapshot()
                elif task in ("stop", "shutdown"):
                    if service is not None:
                        service.stop(timeout=3)
                        result = service.snapshot()
                        if result.get("state") != "stopped":
                            raise RuntimeError("服务尚未确认停止；窗口将保留，请稍后重试停止。")
                    else:
                        result = {"state": "stopped", "events": []}
                elif task == "rearm":
                    result = {"rearmed": bool(service and service.rearm())}
                    result["snapshot"] = service.snapshot() if service else {"state": "stopped"}
                elif task == "snapshot":
                    result = service.snapshot() if service else {"state": "stopped", "events": []}
                else:
                    raise ValueError("未知后台操作。")
                self._replies.put(Reply(task, result=result))
                if task == "shutdown":
                    return
            except Exception as exc:
                self._replies.put(Reply(task, error=clean_text(f"{type(exc).__name__}: {exc}")))


def start_auxiliary(task: str, replies: queue.Queue[Reply]) -> None:
    """只在用户点击后导入并执行既有检查，不打开任何真实串口。"""
    def work() -> None:
        try:
            if task == "environment":
                from .integrations import inspect_environment
                result = inspect_environment()
            elif task == "selftest":
                from .diagnostics import run_selftest
                result = run_selftest()
            else:
                raise ValueError("未知检查任务。")
            replies.put(Reply(task, result=result))
        except Exception as exc:
            replies.put(Reply(task, error=clean_text(f"{type(exc).__name__}: {exc}")))

    threading.Thread(target=work, name=f"gateway-gui-{task}", daemon=True).start()
