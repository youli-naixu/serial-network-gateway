# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
data = [(str(root / "README.zh-CN.md"), "."), (str(root / "docs"), "docs"), (str(root / "examples"), "examples"), (str(root / "serial_gateway" / "assets"), "serial_gateway/assets"), (str(root / "arduino" / "examples" / "AsrWirelessFlash"), "examples/esp32/AsrWirelessFlash")]
hidden = ["serial.rfc2217", "serial.urlhandler.protocol_rfc2217", "serial.tools.list_ports_windows", "serial_gateway.gui", "serial_gateway.cli", "serial_gateway.engine", "serial_gateway.diagnostics", "serial_gateway.integrations", "serial_gateway.asr_station", "serial_gateway.vcom", "serial_gateway.c0c_bridge", "serial_gateway.assistant", "serial_gateway.desktop", "serial_gateway.tray", "serial_gateway.guides", "serial_gateway.locales_assistant", "pystray._win32"]
a = Analysis(
    [str(root / "tools" / "launcher_gui.py"), str(root / "tools" / "launcher_cli.py")],
    pathex=[str(root)], binaries=[], datas=data, hiddenimports=hidden,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts[:-1], [], exclude_binaries=True, name="SerialGateway", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, icon=str(root / "serial_gateway" / "assets" / "app.ico"))
cli = EXE(pyz, a.scripts[:-2] + a.scripts[-1:], [], exclude_binaries=True, name="SerialGateway-cli", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="SerialGateway")
