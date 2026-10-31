"""Non-invasive HW VSP3 Single configuration preview.

Uses only keys verified in the vendor's 3.1.2 installation template.
Does not redistribute the template, carry its default password, install drivers,
edit a running installation, create ports, or restart services.
"""
from __future__ import annotations

import configparser
import io
import re
from pathlib import Path


def render_channel(com_port: str, tcp_port: int, protocol: str = "rfc2217") -> str:
    if not re.fullmatch(r"COM(?:[1-9][0-9]{0,3})", com_port):
        raise ValueError("Use a COM name such as COM10, with an index from 1 to 9999.")
    if isinstance(tcp_port, bool) or not isinstance(tcp_port, int) or not 1 <= tcp_port <= 65535:
        raise ValueError("TCP port must be in the range 1..65535.")
    if protocol not in ("rfc2217", "raw"):
        raise ValueError("Protocol must be rfc2217 or raw.")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser["CHANNEL0"] = {
        "NAME": "Serial Network Gateway", "COM": com_port,
        "CreateAutomatically": "0", "LogEnabled": "0", "ConnectIfPortClosed": "0",
        "ExtControlEnabled": "0", "ExtControlPort": "2003", "StrictBaudrate": "0",
        "PurgeBuffers": "1", "IP": f"127.0.0.1:{tcp_port}",
        "NvtFilter": "1" if protocol == "rfc2217" else "0",
        "NvtPortSetup": "1" if protocol == "rfc2217" else "0",
        "KeepConnection": "0", "NopToKeep": "0", "AutoRenew": "0", "Server": "0",
        "InactiveEnable": "0", "InactiveTimeout": "10",
    }
    output = io.StringIO()
    output.write("; Preview only. Verify the selected COM is unused and configure in the vendor UI.\n")
    output.write("; No GENERAL/password section is included. Never replace an existing full INI blindly.\n")
    parser.write(output, space_around_delimiters=False)
    return output.getvalue()


def write_preview(path: Path | str, com_port: str, tcp_port: int, protocol: str = "rfc2217") -> None:
    target = Path(path)
    text = render_channel(com_port, tcp_port, protocol)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
