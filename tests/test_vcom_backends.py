import configparser
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from serial_gateway import vcom
from serial_gateway.c0c_port import (C0CE_INSERT_RBR, C0CE_INSERT_RLC, SERIAL_LSRMST_ESCAPE,
                                     SERIAL_LSRMST_MST, InsertParser, LineSettings)
from serial_gateway.vcom import HwVspBackend, VcomPlan


ESC = 0xF7


def test_insert_parser_settings_data_and_escaped_bytes():
    parser = InsertParser(ESC)
    stream = (bytes((ESC, C0CE_INSERT_RBR)) + (1500000).to_bytes(4, "little")
              + b"AB" + bytes((ESC, SERIAL_LSRMST_ESCAPE)) + b"C"
              + bytes((ESC, C0CE_INSERT_RLC, 7, 2, 2)) + b"\x00\xff")
    events = []
    for split in range(1, len(stream)):  # every split point must give the same result
        parser = InsertParser(ESC)
        events = parser.feed(stream[:split]) + parser.feed(stream[split:])
        data = b"".join(v for k, v in events if k == "data")
        settings = [v for k, v in events if k == "settings"]
        assert data == b"AB\xf7C\x00\xff", split
        assert settings == [LineSettings(baudrate=1500000), LineSettings(bytesize=7, parity="E", stopbits=2)], split


def test_insert_parser_modem_record_and_unknown_kind_kept_as_data():
    parser = InsertParser(ESC)
    events = parser.feed(bytes((ESC, SERIAL_LSRMST_MST, 0x30, ESC, 0x99)) + b"Z")
    assert ("modem", 0x30) in events
    assert b"".join(v for k, v in events if k == "data") == bytes((ESC, 0x99)) + b"Z"


def test_insert_parser_rejects_zero_escape():
    with pytest.raises(ValueError):
        InsertParser(0)


@pytest.fixture
def fake_hwvsp(tmp_path):
    directory = tmp_path / "HW group" / "HW VSP3s"
    directory.mkdir(parents=True)
    (directory / "HW_VSP3s_srv.exe").write_bytes(b"")
    (directory / "HW_VSP3s.ini").write_text(
        "[GENERAL]\nLogFileEnabled=1\nControlPort=1231\nPassword=keep-me\n[CHANNEL0]\nNAME=Demo\nCOM=COM3\nIP=192.0.2.1:23\n",
        encoding="utf-8")
    return HwVspBackend(directory)


def test_hwvsp_channel_values_and_preview_never_copy_general(fake_hwvsp):
    text = fake_hwvsp.preview(VcomPlan("COM10", "127.0.0.1", 7000))
    parser = configparser.ConfigParser()
    parser.read_string(text)
    assert parser.sections() == ["CHANNEL0"]
    channel = parser["CHANNEL0"]
    assert channel["COM"] == "COM10" and channel["IP"] == "127.0.0.1:7000"
    assert channel["AutoRenew"] == "0" and channel["ConnectIfPortClosed"] == "0"
    assert channel["NvtPortSetup"] == "1" and "keep-me" not in text


@pytest.mark.parametrize("plan", [VcomPlan("COM0"), VcomPlan("COM3;x"), VcomPlan("COM10", host="a b"),
                                  VcomPlan("COM10", port=0), VcomPlan("COM10", port=70000)])
def test_plan_validation(plan):
    with pytest.raises(ValueError):
        plan.validate()


def test_elevated_job_rewrites_only_channel_and_keeps_general(fake_hwvsp, tmp_path, monkeypatch):
    monkeypatch.setattr(vcom.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(vcom, "_service_running", lambda name: False)
    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(json.dumps({"backend": "hwvsp", "ini": str(fake_hwvsp.ini_path), "service": vcom.HWVSP_SERVICE,
                               "channel": HwVspBackend.channel_values(VcomPlan("COM10"))}), encoding="utf-8")
    assert vcom._apply_job(str(job), str(result)) == 0
    assert json.loads(result.read_text())["ok"] is True
    written = configparser.ConfigParser(interpolation=None)
    written.optionxform = str
    written.read_string(fake_hwvsp.ini_path.read_text(encoding="utf-8"))
    assert written["GENERAL"]["Password"] == "keep-me"
    assert written["CHANNEL0"]["COM"] == "COM10"
    assert list(fake_hwvsp.directory.glob("HW_VSP3s.ini.bak-*"))


@pytest.mark.parametrize("mutation", [
    {"backend": "other"},
    {"service": "Spooler"},
    {"channel": {"COM": "COM10"}},
    {"channel_extra": True},
    {"ini": "C:/Windows/win.ini"},
])
def test_elevated_job_rejects_unexpected_input(fake_hwvsp, tmp_path, monkeypatch, mutation):
    monkeypatch.setattr(vcom.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    channel = HwVspBackend.channel_values(VcomPlan("COM10"))
    job_data = {"backend": "hwvsp", "ini": str(fake_hwvsp.ini_path), "service": vcom.HWVSP_SERVICE, "channel": channel}
    if mutation.pop("channel_extra", False):
        job_data["channel"] = dict(channel, Server="1; del")
    job_data.update(mutation)
    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(json.dumps(job_data), encoding="utf-8")
    original = fake_hwvsp.ini_path.read_text(encoding="utf-8")
    assert vcom._apply_job(str(job), str(result)) == 1
    assert json.loads(result.read_text())["ok"] is False
    assert fake_hwvsp.ini_path.read_text(encoding="utf-8") == original


def test_elevated_com0com_job_validates_paths_and_actions(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vcom.subprocess, "run", lambda argv, **k: calls.append(argv) or type("R", (), {"returncode": 0})())
    directory = tmp_path / "com0com"
    directory.mkdir()
    (directory / "setupc.exe").write_bytes(b"")
    (directory / "com0com.inf").write_text("", encoding="utf-8")
    result = tmp_path / "result.json"

    def run(job):
        path = tmp_path / "job.json"
        path.write_text(json.dumps(job), encoding="utf-8")
        return vcom._apply_job(str(path), str(result))

    assert run({"backend": "com0com", "action": "install", "setupc": str(directory / "setupc.exe"), "com": "COM12"}) == 0
    assert calls[-1][1:] == ["install", "PortName=COM12,EmuBR=no", "ri=ropen"]
    assert run({"backend": "com0com", "action": "wire-open", "setupc": str(directory / "setupc.exe"), "pair": 2}) == 0
    assert calls[-1][1:] == ["change", "CNCB2", "ri=ropen"]
    assert run({"backend": "com0com", "action": "remove", "setupc": str(directory / "setupc.exe"), "pair": 3}) == 0
    assert calls[-1][1:] == ["remove", "3"]
    before = len(calls)
    assert run({"backend": "com0com", "action": "install", "setupc": str(directory / "setupc.exe"), "com": "COM12 & x"}) == 1
    assert run({"backend": "com0com", "action": "shell", "setupc": str(directory / "setupc.exe")}) == 1
    assert run({"backend": "com0com", "action": "wire-open", "setupc": str(directory / "setupc.exe"), "pair": 999}) == 1
    assert run({"backend": "com0com", "action": "install", "setupc": "C:/Windows/System32/cmd.exe", "com": "COM12"}) == 1
    assert len(calls) == before


def test_capability_matrix_reports_measured_limits():
    rows = {row["feature"]: row for row in vcom.capability_matrix()}
    assert rows["DTR / RTS"]["hwvsp"] == "no"
    assert rows["DTR / RTS"]["com0com"] == "yes"
