from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


_DRIVER_NAMES = ("com0com", "hub4com", "hw virtual serial", "hw vsp", "hwvsp")


def _registered_programs() -> list[dict[str, str]]:
    if os.name != "nt":
        return []
    import winreg

    programs = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as app:
                                name = str(winreg.QueryValueEx(app, "DisplayName")[0])
                                if not any(word in name.casefold() for word in _DRIVER_NAMES):
                                    continue
                                try:
                                    path = str(winreg.QueryValueEx(app, "InstallLocation")[0])
                                except OSError:
                                    path = ""
                                programs.append({"name": name, "path": path, "source": "卸载注册表；未验证驱动加载"})
                        except OSError:
                            continue
            except OSError:
                continue
    return programs


def inspect_environment() -> dict[str, Any]:
    """Inspect registrations and device metadata without opening serial devices."""
    result: dict[str, Any] = {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "ports": [],
        "drivers": [],
        "messages": [],
    }
    try:
        from serial.tools import list_ports
        result["ports"] = [
            {"device": port.device, "description": port.description, "hwid": port.hwid}
            for port in list_ports.comports()
        ]
    except (ImportError, OSError) as exc:
        result["messages"].append(f"无法枚举串口：{type(exc).__name__}")
    found = _registered_programs()
    for executable in ("setupc.exe", "hub4com.exe", "hw_vsp3.exe"):
        path = shutil.which(executable)
        if path:
            found.append({"name": executable, "path": path, "source": "PATH；未验证驱动加载"})
    if os.name == "nt":
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if not base:
                continue
            for relative in ("com0com/setupc.exe", "hub4com/hub4com.exe", "HW group/HW VSP3/hw_vsp3.exe", "HW group/HW VSP3s/hw_vsp3.exe"):
                path = Path(base) / relative
                if path.is_file():
                    found.append({"name": path.name, "path": str(path), "source": "常见安装路径；未验证驱动加载"})
    seen = set()
    for entry in found:
        key = (entry["name"].casefold(), entry["path"].casefold())
        if key not in seen:
            result["drivers"].append(entry)
            seen.add(key)
    if not found:
        result["messages"].append("未在卸载注册表、PATH和常见安装路径中发现支持的虚拟COM工具；仍可使用本机模拟。")
    result["messages"].append("以上检查不打开COM、不创建串口；软件登记或文件存在不能证明内核驱动已加载或兼容当前安全策略。")
    result["messages"].append("网关TCP入口不是COM；请先用已安装且正常工作的VCOM驱动连接该入口。")
    return result


def virtual_com_instructions(host: str = "127.0.0.1", port: int = 7000, protocol: str = "rfc2217") -> str:
    mode = "RFC2217，开启 NVT / Remote port setup" if protocol == "rfc2217" else "原始 TCP，远端 UART 参数需预先匹配"
    return (
        f"1. 使用已经通过本机驱动签名检查的虚拟COM软件创建一个空闲COM。\n"
        f"2. 将重定向目标设为 {host}:{port}，模式：{mode}。\n"
        "3. 点击本工具的启动，再让官方软件打开该虚拟COM。\n"
        "4. 先用模拟设备检查枚举、参数与二进制传输，再切到真实网关。\n"
        "5. 断线后先关闭官方软件的COM，清理虚拟驱动会话，再点击重新就绪。\n\n"
        "可核查候选：HW VSP3 Single；com0com + hub4com。是否兼容当前Windows、安全策略和下载器必须实测。\n"
        "程序不安装内核驱动、不关闭Secure Boot/内存完整性，也不把普通Socket伪装成COM。"
    )
