"""GUI 私有后台任务与诊断脱敏；本模块不访问 Tk。"""
from __future__ import annotations

import json
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


def run_background(task: str, work: Any, replies: queue.Queue[Reply]) -> threading.Thread:
    """Run ``work()`` off the Tk thread and post exactly one Reply."""
    def run() -> None:
        try:
            replies.put(Reply(task, result=work()))
        except Exception as exc:
            replies.put(Reply(task, error=clean_text(f"{type(exc).__name__}: {exc}")))

    thread = threading.Thread(target=run, name=f"gateway-gui-{task}", daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------ new-tab helpers (pure)

_COM_NAME = re.compile(r"COM[1-9][0-9]{0,3}")
_ENDPOINT = re.compile(r"CNCB\d{1,3}")
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,64}")
_SECRET_KEY = re.compile(r"(?i)token|passw|secret|key|auth|cookie|credential|session_?key")


def default_token_header() -> str:
    """Header name default comes from device_probe so it is not duplicated here."""
    import inspect
    from . import device_probe
    for function, name in ((device_probe.check_device, "token_header"), (device_probe.http_status, "header")):
        try:
            parameter = inspect.signature(function).parameters.get(name)
        except (TypeError, ValueError):
            parameter = None
        if parameter is not None and isinstance(parameter.default, str):
            return parameter.default
    return ""


def _int_in(value: Any, title: str, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{title}应为整数。") from None
    if not low <= number <= high:
        raise ValueError(f"{title}应在 {low}–{high} 之间。")
    return number


def normalize_com(value: Any) -> str:
    com = str(value or "").strip().upper()
    if not _COM_NAME.fullmatch(com):
        raise ValueError("COM 名称应类似 COM10（COM1–COM9999）。")
    return com


def build_vcom_plan(com: Any, host: Any, port: Any, rfc2217: Any = True) -> Any:
    from .vcom import VcomPlan
    plan = VcomPlan(com=normalize_com(com), host=str(host or "").strip() or "127.0.0.1",
                    port=_int_in(port, "目标端口", 1, 65535), rfc2217=bool(rfc2217))
    try:
        plan.validate()
    except ValueError:
        raise ValueError("目标主机应为 IPv4 地址或普通主机名。") from None
    return plan


def parse_endpoint(text: Any) -> str:
    match = _ENDPOINT.search(str(text or "").upper())
    if not match:
        raise ValueError("请选择或填写 com0com 隐藏端点（CNCBn）。")
    return match.group(0)


def pair_label(pair: dict[str, Any]) -> str:
    com = pair.get("com") or "—"
    return f"{pair.get('endpoint', '')} ↔ {com} (#{pair.get('pair', '?')})"


def bridge_args(endpoint_text: Any, host: Any, port: Any) -> tuple[str, str, int]:
    host_text = str(host or "").strip()
    if not host_text or len(host_text) > 253 or any(c.isspace() for c in host_text):
        raise ValueError("请填写远端主机或 IP。")
    return parse_endpoint(endpoint_text), host_text, _int_in(port, "远端端口", 1, 65535)


def device_check_args(host: Any, port: Any, http_port: Any, path: Any, header: Any,
                      probe: Any = False, probe_baud: Any = 0) -> dict[str, Any]:
    """Validated keyword arguments for check_device, excluding the token."""
    host_text = str(host or "").strip()
    if not host_text or len(host_text) > 253 or any(c.isspace() for c in host_text):
        raise ValueError("请填写设备主机或 IP。")
    status_path = str(path or "").strip()
    if status_path and (not status_path.startswith("/") or any(c in status_path for c in "\r\n \t")):
        raise ValueError("状态路径应以 / 开头且不含空白。")
    header_text = str(header or "").strip()
    if not _HEADER_NAME.fullmatch(header_text):
        raise ValueError("鉴权请求头名称只能包含字母、数字和连字符。")
    return {
        "host": host_text, "port": _int_in(port, "RFC2217 端口", 1, 65535),
        "http_port": _int_in(http_port, "HTTP 端口", 1, 65535), "status_path": status_path,
        "token_header": header_text,
        "probe_baud": _int_in(probe_baud, "探测波特率", 300, 4_000_000) if probe else 0,
    }


def _safe_host(value: Any) -> str:
    text = str(value or "")
    host, sep, port = text.rpartition(":") if text.count(":") == 1 else (text, "", "")
    if _IPV4.fullmatch(host):
        return text if sep and port.isdigit() else host
    return "[host]" if text else ""


def flatten_status(data: Any, prefix: str = "", depth: int = 0, limit: int = 60) -> list[tuple[str, str]]:
    """Readable key/value pairs from a status JSON document, hiding secret-looking keys."""
    rows: list[tuple[str, str]] = []
    if isinstance(data, dict):
        items = list(data.items())
    elif isinstance(data, list):
        items = [(str(i), v) for i, v in enumerate(data[:20])]
    else:
        return [(prefix or "value", _scalar_text(data))]
    for key, value in items:
        name = f"{prefix}.{key}" if prefix else str(key)
        if _SECRET_KEY.search(str(key)):
            rows.append((clean_text(name, 80), "[redacted]"))
        elif isinstance(value, (dict, list)) and depth < 2:
            rows.extend(flatten_status(value, name, depth + 1, limit))
        else:
            rows.append((clean_text(name, 80), _scalar_text(value)))
        if len(rows) >= limit:
            break
    return rows[:limit]


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or isinstance(value, (int, float)):
        return "null" if value is None else str(value)
    return clean_text(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value, 200)


def sanitize_backends(backends: Any) -> list[dict[str, Any]]:
    safe = []
    for item in backends if isinstance(backends, list) else []:
        if not isinstance(item, dict):
            continue
        channel = item.get("channel") if isinstance(item.get("channel"), dict) else {}
        safe_channel = {}
        for key, value in channel.items():
            if key == "IP":
                safe_channel[key] = _safe_host(value)
            elif _ENDPOINT.fullmatch(str(key)) or key in ("COM", "NvtPortSetup", "NvtFilter", "CreateAutomatically", "AutoRenew", "ConnectIfPortClosed", "PurgeBuffers"):
                safe_channel[str(key)] = clean_text(value, 40)
        safe.append({
            "name": clean_text(item.get("name", ""), 40),
            "installed": bool(item.get("installed")), "running": bool(item.get("running")),
            "location": "<install dir>" if item.get("location") else "",
            "channel": safe_channel,
            "ports": [p for p in item.get("ports", []) if isinstance(p, str) and _COM_NAME.fullmatch(p)],
            "notes": [clean_text(n, 300) for n in item.get("notes", []) if isinstance(n, str)][:10],
        })
    return safe


def sanitize_bridge(snapshot: Any) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
    lines = snapshot.get("lines") if isinstance(snapshot.get("lines"), dict) else {}
    endpoint = str(snapshot.get("endpoint", ""))
    events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
    return {
        "state": clean_text(snapshot.get("state", ""), 20),
        "endpoint": endpoint if _ENDPOINT.fullmatch(endpoint) else "",
        "remote": _safe_host(snapshot.get("remote")),
        "session_id": snapshot.get("session_id") if isinstance(snapshot.get("session_id"), int) else None,
        "app_to_device": snapshot.get("app_to_device") if isinstance(snapshot.get("app_to_device"), int) else 0,
        "device_to_app": snapshot.get("device_to_app") if isinstance(snapshot.get("device_to_app"), int) else 0,
        "requests_sent": snapshot.get("requests_sent") if isinstance(snapshot.get("requests_sent"), int) else 0,
        "settings": {k: settings[k] for k in ("baudrate", "bytesize", "parity", "stopbits")
                     if k in settings and isinstance(settings[k], (int, float, str)) and not isinstance(settings[k], bool)},
        "lines": {k: bool(lines[k]) for k in ("dtr", "rts", "break") if k in lines},
        "last_error": clean_text(snapshot.get("last_error", ""), 200),
        "events": [{"time": clean_text(e.get("time", ""), 40), "level": clean_text(e.get("level", ""), 10),
                    "message": clean_text(e.get("message", ""), 300)} for e in events[-50:] if isinstance(e, dict)],
    }


def sanitize_device_report(report: Any) -> dict[str, Any] | None:
    """Whitelist of a check_device() result. The token is never part of it."""
    if not isinstance(report, dict):
        return None
    out: dict[str, Any] = {"host": _safe_host(report.get("host")), "port": report.get("port") if isinstance(report.get("port"), int) else None}
    for section, keys in (("tcp", ("ok", "ms", "error", "code")), ("rfc2217", ("ok", "reply", "busy", "error")),
                          ("http", ("ok", "status", "error"))):
        part = report.get(section)
        if isinstance(part, dict):
            out[section] = {k: (part[k] if part[k] is None or isinstance(part[k], (bool, int, float)) else clean_text(part[k], 80))
                            for k in keys if k in part}
            if section == "http" and "json" in part:
                out[section]["fields"] = dict(flatten_status(part["json"]))
    return out


def format_device_report(report: Any, tr: Any) -> str:
    """Human-readable lines; ``tr`` is a Translator (or any callable text -> text)."""
    if not isinstance(report, dict):
        return tr("尚未检测设备。")
    def verdict(part: Any) -> str:
        return tr("成功") if isinstance(part, dict) and part.get("ok") else tr("失败")
    lines = [tr("设备：{address}", address=clean_text(f"{report.get('host')}:{report.get('port')}", 120))]
    tcp = report.get("tcp")
    if isinstance(tcp, dict):
        detail = f"{tcp.get('ms')} ms" if tcp.get("ok") else clean_text(f"{tcp.get('error')} {tcp.get('code') or ''}", 80).strip()
        lines.append(tr("TCP 连接：{verdict}（{detail}）", verdict=verdict(tcp), detail=detail))
    rfc = report.get("rfc2217")
    if isinstance(rfc, dict):
        if rfc.get("ok"):
            detail = tr("设备确认波特率 {baud}", baud=rfc.get("reply"))
        elif rfc.get("busy"):
            detail = tr("连接被关闭，可能已有其他会话或不是 RFC2217")
        else:
            detail = clean_text(rfc.get("error") or "—", 80)
        lines.append(tr("RFC2217 探测：{verdict}（{detail}）", verdict=verdict(rfc), detail=detail))
    else:
        lines.append(tr("RFC2217 探测：未执行（未勾选探测波特率）"))
    http = report.get("http")
    if isinstance(http, dict):
        detail = f"HTTP {http.get('status')}" if http.get("status") is not None else clean_text(http.get("error") or "—", 80)
        lines.append(tr("HTTP 状态：{verdict}（{detail}）", verdict=verdict(http), detail=detail))
        if "json" in http:
            lines.append(tr("设备状态字段："))
            lines.extend(f"  {key} = {value}" for key, value in flatten_status(http["json"]))
    return "\n".join(lines)


def bridge_settings_text(settings: Any, tr: Any) -> str:
    if not isinstance(settings, dict) or not settings:
        return tr("等待应用设置")
    parity = {"N": "无", "E": "偶", "O": "奇", "M": "标记", "S": "空格"}
    parts = []
    if "baudrate" in settings:
        parts.append(f"{tr('波特率')} {settings['baudrate']}")
    if "bytesize" in settings:
        parts.append(f"{tr('数据位')} {settings['bytesize']}")
    if "parity" in settings:
        parts.append(f"{tr('校验')} {tr(parity.get(str(settings['parity']), str(settings['parity'])))}")
    if "stopbits" in settings:
        parts.append(f"{tr('停止位')} {settings['stopbits']}")
    return "  ·  ".join(parts)


def extend_diagnostics(document: dict[str, Any], backends: Any = None, bridge: Any = None, device: Any = None) -> dict[str, Any]:
    document["vcom_backends"] = sanitize_backends(backends) if backends is not None else None
    document["com0com_bridge"] = sanitize_bridge(bridge)
    document["device_check"] = sanitize_device_report(device)
    return document


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
