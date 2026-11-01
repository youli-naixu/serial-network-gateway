import json

import pytest

from serial_gateway import cli, device_probe, vcom

_REAL_HWVSP = vcom.HwVspBackend


class _Backend:
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    def apply(self, plan, timeout_s=0):
        _Backend.calls.append(("apply", plan))
        return {"ok": True, "port_present": True}

    def preview(self, plan):
        return _REAL_HWVSP.preview(_REAL_HWVSP.__new__(_REAL_HWVSP), plan)

    def create_pair(self, com):
        _Backend.calls.append(("create", com))
        return {"ok": True, "port_present": True}

    def remove_pair(self, number):
        _Backend.calls.append(("remove", number))
        return {"ok": True}


@pytest.fixture(autouse=True)
def stub_backends(monkeypatch):
    _Backend.calls = []
    monkeypatch.setattr(vcom, "HwVspBackend", type("HwStub", (_Backend,), {"preview": _Backend.preview}))
    monkeypatch.setattr(vcom, "Com0comBackend", _Backend)
    monkeypatch.setattr(vcom, "detect_backends", lambda: [vcom.BackendStatus("HW VSP3 Single", installed=False)])
    monkeypatch.setattr(vcom, "suggest_free_com", lambda: "COM11")


def test_vcom_status(capsys):
    assert cli.main(["--language", "en", "vcom", "status"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["suggested_com"] == "COM11" and data["backends"][0]["installed"] is False
    assert data["capabilities"]


def test_vcom_preview_needs_no_confirmation(capsys):
    assert cli.main(["vcom", "preview", "--com", "com10", "--port", "7000"]) == 0
    out = capsys.readouterr().out
    assert "COM=COM10" in out and "IP=127.0.0.1:7000" in out and "NvtPortSetup=1" in out
    assert not _Backend.calls


@pytest.mark.parametrize("argv", [
    ["vcom", "apply-hwvsp", "--com", "COM10", "--port", "7000"],
    ["vcom", "create-pair", "--com", "COM20"],
    ["vcom", "remove-pair", "--pair", "0"],
])
def test_changes_refused_without_yes(argv, capsys):
    assert cli.main(["--language", "en", *argv]) == 2
    assert "--yes" in capsys.readouterr().err
    assert not _Backend.calls


def test_changes_run_with_yes(capsys):
    assert cli.main(["vcom", "apply-hwvsp", "--com", "COM10", "--port", "7001", "--host", "127.0.0.1", "--yes"]) == 0
    assert cli.main(["vcom", "create-pair", "--com", "COM20", "--yes"]) == 0
    assert cli.main(["vcom", "remove-pair", "--pair", "3", "--yes"]) == 0
    kinds = [call[0] for call in _Backend.calls]
    assert kinds == ["apply", "create", "remove"]
    assert _Backend.calls[0][1].port == 7001 and _Backend.calls[1][1] == "COM20"


@pytest.mark.parametrize("com", ["COM0", "LPT1", "COM10;x", "COM99999", "\\\\.\\COM3"])
def test_bad_com_names_rejected(com):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["vcom", "create-pair", "--com", com, "--yes"])
    assert exit_info.value.code == 2
    assert not _Backend.calls


def test_bad_port_rejected():
    with pytest.raises(SystemExit):
        cli.main(["vcom", "preview", "--com", "COM10", "--port", "70000"])


def test_bad_pair_number_rejected(capsys):
    assert cli.main(["vcom", "remove-pair", "--pair", "999", "--yes"]) == 1
    assert not _Backend.calls


def test_device_token_not_accepted_as_value(monkeypatch):
    called = []
    monkeypatch.setattr(device_probe, "check_device", lambda **kw: called.append(kw) or {"tcp": {"ok": True}})
    for argv in (["device", "check", "--host", "127.0.0.1", "--token", "abc"],
                 ["device", "check", "--host", "127.0.0.1", "--tok", "abc"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert cli.main(["device", "check", "--host", "127.0.0.1", "--token-env", "not a name"]) == 1
    assert not called


def test_device_token_from_environment(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(device_probe, "check_device", lambda **kw: called.append(kw) or {"host": kw["host"], "tcp": {"ok": True}})
    monkeypatch.setenv("SNG_TEST_TOKEN", "s3cr3t-value")
    assert cli.main(["device", "check", "--host", "127.0.0.1", "--token-env", "SNG_TEST_TOKEN",
                     "--token-header", "X-Other", "--probe-baud", "9600"]) == 0
    out = capsys.readouterr().out
    assert called[0]["token"] == "s3cr3t-value" and called[0]["token_header"] == "X-Other"
    assert called[0]["probe_baud"] == 9600 and called[0]["port"] == 3333
    assert "s3cr3t-value" not in out
    monkeypatch.delenv("SNG_TEST_TOKEN")
    assert cli.main(["device", "check", "--host", "127.0.0.1", "--token-env", "SNG_TEST_TOKEN"]) == 1
    assert len(called) == 1


def test_device_check_defaults(monkeypatch):
    called = []
    monkeypatch.setattr(device_probe, "check_device", lambda **kw: called.append(kw) or {"tcp": {"ok": False}})
    assert cli.main(["device", "check", "--host", "127.0.0.1"]) == 1
    assert called[0]["token"] == "" and called[0]["probe_baud"] == 0 and called[0]["http_port"] == 80
    assert called[0]["status_path"] == "/api/maintenance/status"


def test_device_scan(monkeypatch, capsys):
    monkeypatch.setattr(device_probe, "local_networks", lambda: ["192.168.50.0/24"])
    monkeypatch.setattr(device_probe, "scan_subnet", lambda network, port: ["192.168.50.9"])
    assert cli.main(["device", "scan", "--port", "3333"]) == 0
    assert json.loads(capsys.readouterr().out)["networks"] == {"192.168.50.0/24": ["192.168.50.9"]}


def test_bridge_rejects_bad_endpoint(capsys):
    assert cli.main(["--language", "en", "bridge", "--endpoint", "COM5", "--host", "127.0.0.1", "--port", "7000"]) == 1
    assert "Endpoint" in capsys.readouterr().err


def test_bilingual_help(capsys):
    for language, needle in (("en", "Virtual COM drivers"), ("zh-CN", "虚拟 COM 驱动")):
        with pytest.raises(SystemExit):
            cli.main(["--language", language, "--help"])
        assert needle in capsys.readouterr().out
