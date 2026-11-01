from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .i18n import resolve_language, translate_error, translate_event
from .profiles import ProfileStore


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _emit(data: Any, output: str | None = None) -> None:
    text = _json(data)
    print(text)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


def build_parser(language: str = "zh-CN") -> argparse.ArgumentParser:
    def text(zh: str, en: str) -> str:
        return en if language == "en" else zh

    parser = argparse.ArgumentParser(description=text("串口网络网关：TCP/RFC2217、虚拟 COM 接入和离线测试。", "Serial Network Gateway: TCP/RFC2217, virtual COM integration and offline tests."))
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--language", choices=("zh-CN", "en"), default=language, help=text("界面和命令行语言", "Interface and command-line language"))
    parser.add_argument("--config", help=text("配置文件路径；默认使用用户本地应用目录", "Profile file; defaults to the user application directory"))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help=text("打开图形界面；不自动连接设备", "Open the desktop UI without connecting automatically"))
    sub.add_parser("profiles", help=text("列出配置档", "List saved profiles"))
    sub.add_parser("environment", help=text("只读检查 COM 和驱动，不打开串口", "Inspect COM/driver metadata without opening ports"))
    driver = sub.add_parser("driver-help", help=text("显示虚拟 COM 接入说明", "Show virtual COM integration instructions"))
    driver.add_argument("--port", type=int, default=7000)
    driver.add_argument("--protocol", choices=("raw", "rfc2217"), default="rfc2217")
    test = sub.add_parser("selftest", help=text("本机模拟自检，不访问真实设备", "Run loopback-only self-tests without target hardware"))
    test.add_argument("--bytes", type=int, default=65536, dest="payload_size")
    test.add_argument("--output", help=text("保存 JSON 报告", "Save a JSON report"))
    bridge = sub.add_parser("com-bridge", help=text("预览或运行已有 com0com/hub4com 桥接", "Preview or run an existing com0com/hub4com bridge"))
    bridge.add_argument("--setupc", required=True, help=text("已安装 setupc.exe 路径", "Installed setupc.exe path"))
    bridge.add_argument("--hub4com", required=True, help=text("已安装 hub4com.exe 路径", "Installed hub4com.exe path"))
    bridge.add_argument("--endpoint", required=True, help=text("只接受 CNCBn 虚拟端点", "Verified CNCBn virtual endpoint only"))
    bridge.add_argument("--port", type=int, default=7000)
    bridge.add_argument("--run", action="store_true", help=text("明确启动；缺省只预览", "Explicitly start; preview only by default"))
    preview = sub.add_parser("hwvsp-preview", help=text("生成 HW VSP3 单口配置片段，不创建 COM", "Generate a HW VSP3 channel preview; does not create COM"))
    preview.add_argument("--com", required=True, help=text("计划使用的空闲COM名", "Planned unused COM name"))
    preview.add_argument("--port", type=int, default=7000)
    preview.add_argument("--output", help=text("另存新文件，不覆盖已有文件", "Write a new file; never overwrite"))
    accept = sub.add_parser("com-test", help=text("显式验收已有虚拟COM到模拟器的真实链路", "Explicitly test an existing virtual COM against the simulator"))
    accept.add_argument("--com", required=True)
    accept.add_argument("--port", type=int, default=7000)
    accept.add_argument("--output")
    accept.add_argument("--run", action="store_true", help=text("明确打开已核验虚拟COM；缺省只检查身份", "Open the verified virtual COM; metadata-only by default"))
    serve = sub.add_parser("serve", help=text("运行配置档，断线不续传", "Run a profile without automatic session resumption"))
    serve.add_argument("--profile", help=text("配置名称", "Profile name"))
    serve.add_argument("--simulate", action="store_true", help=text("强制内置本机模拟", "Force the built-in loopback simulator"))
    serve.add_argument("--port", type=int, help=text("本机监听端口，0为临时端口", "Local listener port, 0 for an ephemeral port"))
    serve.add_argument("--duration", type=float, default=0, help=text("运行秒数；0直到Ctrl+C", "Run time in seconds; 0 until Ctrl+C"))
    serve.add_argument("--output", help=text("退出时保存最后状态", "Save the final snapshot on exit"))

    def com_name(value: str) -> str:
        from ._gui_runtime import normalize_com
        try:
            return normalize_com(value)
        except ValueError:
            raise argparse.ArgumentTypeError(text("COM 名称应类似 COM10（COM1–COM9999）", "COM name must look like COM10 (COM1-COM9999)")) from None

    def tcp_port(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            number = 0
        if not 1 <= number <= 65535:
            raise argparse.ArgumentTypeError(text("端口应在 1–65535 之间", "port must be in 1..65535"))
        return number

    yes_help = text("确认执行（会弹出一次 Windows 管理员确认）", "Confirm the change (shows one Windows administrator prompt)")
    vcom = sub.add_parser("vcom", help=text("虚拟 COM 驱动：检测、预览、应用、管理 com0com 端口对", "Virtual COM drivers: detect, preview, apply, manage com0com pairs"))
    vsub = vcom.add_subparsers(dest="vcom_command", required=True)
    vsub.add_parser("status", help=text("只读检测 HW VSP3 与 com0com", "Read-only detection of HW VSP3 and com0com"))
    for name, helptext in (("preview", text("生成 HW VSP 通道预览，不做修改", "Render the HW VSP channel; changes nothing")),
                           ("apply-hwvsp", text("把通道写入 HW VSP 并重启其服务", "Write the channel to HW VSP and restart its service"))):
        item = vsub.add_parser(name, help=helptext, allow_abbrev=False)
        item.add_argument("--com", required=True, type=com_name)
        item.add_argument("--port", required=True, type=tcp_port)
        item.add_argument("--host", default="127.0.0.1")
        item.add_argument("--raw", action="store_true", help=text("关闭 RFC2217/NVT", "Disable RFC2217/NVT"))
        if name == "apply-hwvsp":
            item.add_argument("--yes", action="store_true", help=yes_help)
    create = vsub.add_parser("create-pair", help=text("新建 com0com 端口对", "Create a com0com port pair"), allow_abbrev=False)
    create.add_argument("--com", required=True, type=com_name)
    create.add_argument("--yes", action="store_true", help=yes_help)
    remove = vsub.add_parser("remove-pair", help=text("删除 com0com 端口对", "Remove a com0com port pair"), allow_abbrev=False)
    remove.add_argument("--pair", required=True, type=int)
    remove.add_argument("--yes", action="store_true", help=yes_help)
    c0c = sub.add_parser("bridge", help=text("运行 com0com 桥接到 RFC2217 端点", "Run the com0com bridge to an RFC2217 endpoint"), allow_abbrev=False)
    c0c.add_argument("--endpoint", required=True, help=text("隐藏端点 CNCBn", "Hidden endpoint CNCBn"))
    c0c.add_argument("--host", required=True)
    c0c.add_argument("--port", required=True, type=tcp_port)
    c0c.add_argument("--duration", type=float, default=0, help=text("运行秒数；0直到Ctrl+C", "Run time in seconds; 0 until Ctrl+C"))
    device = sub.add_parser("device", help=text("只读检查 ESP32 网关设备", "Read-only checks of an ESP32 gateway device"))
    dsub = device.add_subparsers(dest="device_command", required=True)
    check = dsub.add_parser("check", help=text("检测 TCP、可选 RFC2217 探测、HTTP 状态", "Check TCP, optional RFC2217 probe, HTTP status"), allow_abbrev=False)
    check.add_argument("--host", required=True)
    check.add_argument("--port", type=tcp_port, default=3333)
    check.add_argument("--http-port", type=tcp_port, default=80)
    check.add_argument("--path", default="/api/maintenance/status", help=text("HTTP 状态路径；空字符串跳过", "HTTP status path; empty string skips HTTP"))
    check.add_argument("--token-env", metavar="VAR", help=text("从该环境变量读取令牌（不接受命令行明文）", "Read the token from this environment variable (never a command-line value)"))
    check.add_argument("--token-header", help=text("鉴权请求头名称", "Auth header name"))
    check.add_argument("--probe-baud", type=int, default=0, help=text("发送一次设置波特率探测（会改变远端 UART 速度）", "Send one set-baud probe (changes the remote UART speed)"))
    scan = dsub.add_parser("scan", help=text("扫描私有 /24 网段中开放端口的主机", "Scan a private /24 for hosts with the port open"), allow_abbrev=False)
    scan.add_argument("--network", help=text("例如 192.168.50.0/24；缺省使用本机网段", "e.g. 192.168.50.0/24; defaults to local networks"))
    scan.add_argument("--port", type=tcp_port, default=3333)
    return parser


def _require_yes(args: argparse.Namespace, language: str) -> bool:
    if args.yes:
        return True
    print("Refusing without --yes: this change shows a Windows administrator prompt." if language == "en"
          else "未加 --yes，已拒绝执行：该操作会弹出 Windows 管理员确认。", file=sys.stderr)
    return False


def _vcom(args: argparse.Namespace, language: str) -> int:
    from . import vcom
    action = args.vcom_command
    if action == "status":
        try:
            suggest = vcom.suggest_free_com()
        except RuntimeError:
            suggest = ""
        _emit({"backends": [b.to_dict() for b in vcom.detect_backends()], "capabilities": vcom.capability_matrix(),
               "suggested_com": suggest})
        return 0
    if action in ("preview", "apply-hwvsp"):
        plan = vcom.VcomPlan(com=args.com, host=args.host, port=args.port, rfc2217=not args.raw)
        plan.validate()
        if action == "preview":
            print(vcom.HwVspBackend().preview(plan), end="")
            return 0
        if not _require_yes(args, language):
            return 2
        result = vcom.HwVspBackend().apply(plan, timeout_s=120)
        _emit(result)
        return 0 if result.get("ok") else 1
    if action == "create-pair":
        if not _require_yes(args, language):
            return 2
        result = vcom.Com0comBackend().create_pair(args.com)
    else:
        if not 0 <= args.pair <= 255:
            raise ValueError("Unknown pair number.")
        if not _require_yes(args, language):
            return 2
        result = vcom.Com0comBackend().remove_pair(args.pair)
    _emit(result)
    return 0 if result.get("ok") else 1


def _bridge(args: argparse.Namespace, language: str) -> int:
    from .c0c_bridge import Com0comBridge
    if not 0 <= args.duration <= 86400:
        raise ValueError("Run duration must be between 0 and 86400 seconds.")
    bridge = Com0comBridge(args.endpoint.strip().upper(), args.host.strip(), args.port)
    end = time.monotonic() + args.duration if args.duration else None
    last = None
    result = 0
    try:
        bridge.start()
        while end is None or time.monotonic() < end:
            snap = bridge.snapshot()
            key = (snap["state"], snap["session_id"], snap["last_error"])
            if key != last:
                last = key
                _emit({k: snap.get(k) for k in ("state", "message", "endpoint", "remote", "session_id",
                                                "app_to_device", "device_to_app", "settings", "lines", "last_error")})
            if snap["state"] in ("needs_reset", "error"):
                print("Bridge session ended; run the command again for a new session." if language == "en"
                      else "桥接会话已结束；重新运行本命令开始新会话。", flush=True)
                result = 1 if snap["state"] == "error" or snap["last_error"] else 0
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        snap = bridge.snapshot()
        print(_json({k: snap.get(k) for k in ("state", "app_to_device", "device_to_app", "requests_sent")}), flush=True)
    return result


def _device(args: argparse.Namespace, language: str) -> int:
    from . import device_probe
    if args.device_command == "scan":
        networks = [args.network] if args.network else device_probe.local_networks()
        if not networks:
            raise ValueError("No private IPv4 network found; pass --network.")
        found = {network: device_probe.scan_subnet(network, args.port) for network in networks[:4]}
        _emit({"port": args.port, "networks": found})
        return 0
    import os
    import re
    token = ""
    if args.token_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", args.token_env):
            raise ValueError("--token-env takes an environment variable NAME, not a token value.")
        if args.token_env not in os.environ:
            raise ValueError(f"Environment variable {args.token_env} is not set.")
        token = os.environ[args.token_env]
    from ._gui_runtime import default_token_header, device_check_args
    kwargs = device_check_args(args.host, args.port, args.http_port, args.path, args.token_header or default_token_header(),
                               bool(args.probe_baud), args.probe_baud or 0)
    report = device_probe.check_device(token=token, **kwargs)
    _emit(report)
    return 0 if report.get("tcp", {}).get("ok") else 1


def _localized_report(data: Any, language: str) -> Any:
    if language != "en":
        return data
    if isinstance(data, list):
        return [_localized_report(item, language) for item in data]
    if isinstance(data, dict):
        return {key: (translate_event(value, language) if key in ("message", "scope", "detail", "error", "note", "name") and isinstance(value, str) else _localized_report(value, language)) for key, value in data.items()}
    return data


def _serve(args: argparse.Namespace) -> int:
    from .engine import GatewayService
    profile = ProfileStore(args.config).get(args.profile)
    if args.simulate:
        profile = replace(profile, mode="simulator")
    if args.port is not None:
        profile = replace(profile, listen_port=args.port)
    profile.validate()
    if not 0 <= args.duration <= 86400:
        raise ValueError("Run duration must be between 0 and 86400 seconds.")
    service = GatewayService()
    stopping = False

    def stop_signal(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    previous = signal.signal(signal.SIGINT, stop_signal)
    end = time.monotonic() + args.duration if args.duration else None
    last_state = None
    result = 0
    try:
        service.start(profile)
        while not stopping and (end is None or time.monotonic() < end):
            state = service.snapshot()
            key = (state.get("state"), state.get("session_id"), state.get("message"))
            if key != last_state:
                _emit(_localized_report({k: state.get(k) for k in ("state", "message", "listen_host", "listen_port", "mode", "protocol", "session_id", "last_error")}, args.language))
                last_state = key
            if state.get("state") == "error":
                result = 1
                break
            if state.get("state") == "needs_reset":
                print("Session ended. Close the client COM and restart serve for a new session." if args.language == "en" else "当前会话已结束。先关闭客户端 COM，再重新运行 serve。", flush=True)
                break
            time.sleep(0.1)
    finally:
        snapshot = service.snapshot()
        service.stop()
        signal.signal(signal.SIGINT, previous)
        if args.output:
            _emit(_localized_report(snapshot, args.language), args.output)
    return result


def main(argv: list[str] | None = None) -> int:
    values = list(argv) if argv is not None else sys.argv[1:]
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--language", choices=("zh-CN", "en"))
    known, _ = pre.parse_known_args(values)
    language = resolve_language(known.language)
    parser = build_parser(language)
    args = parser.parse_args(values)
    command = args.command or "gui"
    try:
        if command == "gui":
            from .gui import main as gui_main
            gui_main(args.config, language=args.language)
            return 0
        if command == "profiles":
            profiles, selected = ProfileStore(args.config).load()
            _emit({"selected": selected, "profiles": [p.to_dict() for p in profiles]})
            return 0
        if command == "environment":
            from .integrations import inspect_environment
            data = inspect_environment()
            if language == "en":
                data["messages"] = [translate_event(item, language) for item in data["messages"]]
            _emit(data)
            return 0
        if command == "driver-help":
            if language == "en":
                print(f"Create a COM port with a compatible external VCOM driver. Connect it to 127.0.0.1:{args.port} using {args.protocol}. For RFC2217 enable NVT/remote port setup. Start this gateway before opening COM. Close the old COM session before rearming after disconnect. No driver is installed automatically.")
            else:
                from .integrations import virtual_com_instructions
                print(virtual_com_instructions(port=args.port, protocol=args.protocol))
            return 0
        if command == "selftest":
            from .diagnostics import run_selftest
            result = run_selftest(args.payload_size)
            _emit(_localized_report(result, language), args.output)
            return 0 if result["ok"] else 1
        if command == "hwvsp-preview":
            from .hw_vsp import render_channel, write_preview
            if args.output:
                write_preview(args.output, args.com, args.port)
            print(render_channel(args.com, args.port))
            return 0
        if command == "com-test":
            from .com_acceptance import run_com_acceptance, verify_virtual_identity
            if not args.run:
                identity = verify_virtual_identity(args.com)
                _emit({"mode": "preview", "virtual_identity_verified": True, "description": identity["description"], "opened": False})
                return 0
            result = run_com_acceptance(args.com, args.port)
            _emit(result, args.output)
            return 0 if result["ok"] else 1
        if command == "com-bridge":
            from .virtual_com import Hub4ComBridge, hub4com_argv
            command_line = hub4com_argv(args.hub4com, args.endpoint, args.port)
            if not args.run:
                _emit({"mode": "preview", "argv": command_line, "opened": False})
                return 0
            bridge = Hub4ComBridge()
            try:
                bridge.start(args.setupc, args.hub4com, args.endpoint, args.port)
                print("Virtual COM bridge running; no automatic reconnect. Ctrl+C stops." if language == "en" else "虚拟端口桥已启动；断线不重连，Ctrl+C停止。", flush=True)
                while bridge.snapshot()["running"]:
                    time.sleep(0.1)
                state = bridge.snapshot()
                _emit(state)
                return 0 if state["exit_code"] == 0 else 1
            except KeyboardInterrupt:
                return 0
            finally:
                bridge.stop()
        if command == "serve":
            return _serve(args)
        if command == "vcom":
            return _vcom(args, language)
        if command == "bridge":
            return _bridge(args, language)
        if command == "device":
            return _device(args, language)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(("Error: " if language == "en" else "错误：") + translate_error(exc, language), file=sys.stderr)
        return 1
    return 1
