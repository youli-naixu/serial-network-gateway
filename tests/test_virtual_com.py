from pathlib import Path
from unittest.mock import Mock

import pytest

from serial_gateway.virtual_com import Hub4ComBridge, hub4com_argv, list_com0com_ports, parse_com0com_ports


def fake_exe(tmp_path, name):
    path = tmp_path / "安装目录 with space" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"not-executed")
    return path


def test_parse_only_virtual_com_pair_names():
    ports = parse_com0com_ports("  CNCA2 PortName=COM8,EmuBR=yes\n  CNCB2 PortName=CNCB2\nCOM3 Physical\nCNCB9oops bad\n")
    assert ports == {"CNCA2": "PortName=COM8,EmuBR=yes", "CNCB2": "PortName=CNCB2"}


def test_argv_no_shell_and_no_reconnect(tmp_path):
    argv = hub4com_argv(fake_exe(tmp_path, "hub4com.exe"), "CNCB2", 7000)
    assert argv[-1] == "*127.0.0.1:7000"
    assert "--reconnect=no" in argv and "--reconnect=0" not in argv
    assert "--share-mode=on" in argv
    assert r"\\.\CNCB2" in argv
    assert '--create-filter=pinmap,com,pinmap:--rts=cts --dtr=dsr' in argv
    assert not argv[0].startswith('"')


@pytest.mark.parametrize("endpoint", ["COM3", "COM4", r"\\.\COM3", "CNCB0 & bad", "CNCB-1", "CNCA0", "CNCB0\n", "CNCB0;exit"])
def test_physical_and_injected_endpoints_rejected(tmp_path, endpoint):
    with pytest.raises(ValueError):
        hub4com_argv(fake_exe(tmp_path, "hub4com.exe"), endpoint, 7000)


def test_wrong_executable_rejected(tmp_path):
    with pytest.raises(ValueError):
        hub4com_argv(fake_exe(tmp_path, "cmd.exe"), "CNCB0", 7000)


def test_listing_is_readonly_argv(monkeypatch, tmp_path):
    executable = fake_exe(tmp_path, "setupc.exe")
    run = Mock(return_value=Mock(stdout="CNCA0 PortName=COM8\nCNCB0 PortName=CNCB0\n"))
    monkeypatch.setattr("serial_gateway.virtual_com.subprocess.run", run)
    ports = list_com0com_ports(executable)
    assert "CNCB0" in ports
    args, kwargs = run.call_args
    assert args[0][1:] == ["--no-update-fnames", "--detail-prms", "list"]
    assert kwargs["shell"] is False


def test_adapter_refuses_unenumerated_pair(monkeypatch, tmp_path):
    monkeypatch.setattr("serial_gateway.virtual_com.list_com0com_ports", lambda path: {"CNCB0": "PortName=CNCB0"})
    popen = Mock()
    monkeypatch.setattr("serial_gateway.virtual_com.subprocess.Popen", popen)
    with pytest.raises(ValueError, match="完整端口对"):
        Hub4ComBridge().start("not-needed", fake_exe(tmp_path, "hub4com.exe"), "CNCB0", 7000)
    popen.assert_not_called()
