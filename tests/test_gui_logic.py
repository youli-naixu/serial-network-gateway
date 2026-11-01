import json
import os

import pytest

from serial_gateway import _gui_runtime as rt
from serial_gateway.i18n import Translator
from serial_gateway.locales import EN
from serial_gateway.models import GatewayProfile

SECRET = "tok-" + "9f3a1c7e5b"


def test_build_vcom_plan_normalizes_and_validates():
    plan = rt.build_vcom_plan(" com12 ", "127.0.0.1", "7000", False)
    assert (plan.com, plan.host, plan.port, plan.rfc2217) == ("COM12", "127.0.0.1", 7000, False)
    assert rt.build_vcom_plan("COM3", "", 1).host == "127.0.0.1"
    for com in ("COM0", "LPT1", "COM12345", "COM1;x", ""):
        with pytest.raises(ValueError):
            rt.build_vcom_plan(com, "127.0.0.1", 7000)
    with pytest.raises(ValueError):
        rt.build_vcom_plan("COM10", "127.0.0.1", "70000")
    with pytest.raises(ValueError):
        rt.build_vcom_plan("COM10", "bad host;rm", 7000)


def test_endpoint_and_bridge_args():
    assert rt.parse_endpoint("CNCB3 ↔ COM20 (#3)") == "CNCB3"
    assert rt.pair_label({"pair": 0, "com": "COM20", "endpoint": "CNCB0"}).startswith("CNCB0")
    assert rt.bridge_args("cncb1", " 127.0.0.1 ", "7000") == ("CNCB1", "127.0.0.1", 7000)
    with pytest.raises(ValueError):
        rt.bridge_args("COM20", "127.0.0.1", 7000)
    with pytest.raises(ValueError):
        rt.bridge_args("CNCB0", "", 7000)


def test_device_check_args():
    args = rt.device_check_args("192.168.50.20", "3333", "80", "/api/x", "X-Auth", False, "115200")
    assert args == {"host": "192.168.50.20", "port": 3333, "http_port": 80, "status_path": "/api/x",
                    "token_header": "X-Auth", "probe_baud": 0}
    assert rt.device_check_args("h", 1, 1, "", "A", True, "9600")["probe_baud"] == 9600
    assert "token" not in args
    for bad in (("", 3333, 80, "/", "A"), ("h", 0, 80, "/", "A"), ("h", 1, 80, "no-slash", "A"), ("h", 1, 80, "/", "Bad Header:")):
        with pytest.raises(ValueError):
            rt.device_check_args(*bad)
    with pytest.raises(ValueError):
        rt.device_check_args("h", 1, 1, "/", "A", True, "10")


def test_default_header_comes_from_device_probe():
    import inspect
    from serial_gateway.device_probe import check_device
    assert rt.default_token_header() == inspect.signature(check_device).parameters["token_header"].default


def test_flatten_status_hides_secret_keys():
    rows = dict(rt.flatten_status({"mode": "bridge", "uptime": 5, "auth_token": SECRET,
                                   "uart": {"baud": 115200, "password": SECRET}, "ok": True}))
    assert rows["mode"] == "bridge" and rows["uart.baud"] == "115200" and rows["ok"] == "true"
    assert rows["auth_token"] == "[redacted]" and rows["uart.password"] == "[redacted]"
    assert SECRET not in json.dumps(rows)


def test_format_device_report_bilingual():
    report = {"host": "192.168.50.20", "port": 3333, "tcp": {"ok": True, "ms": 3.2},
              "http": {"ok": True, "status": 200, "json": {"mode": "bridge"}}}
    zh = rt.format_device_report(report, Translator("zh-CN"))
    en = rt.format_device_report(report, Translator("en"))
    assert "TCP 连接：成功" in zh and "mode = bridge" in zh
    assert "TCP connection: OK" in en and "skipped" in en and "HTTP 200" in en
    assert "mode = bridge" in en
    assert rt.format_device_report(None, Translator("en")) == "No device checked yet."


def test_bridge_settings_text():
    tr = Translator("en")
    assert rt.bridge_settings_text({}, tr) == "Waiting for application settings"
    text = rt.bridge_settings_text({"baudrate": 1500000, "bytesize": 8, "parity": "E", "stopbits": 1}, tr)
    assert "1500000" in text and "Even" in text


def test_extended_diagnostics_whitelist_and_no_token():
    private = "C:" + r"\Program Files (x86)\HW group\HW VSP3s"
    backends = [{"name": "HW VSP3 Single", "installed": True, "running": True, "location": private,
                 "channel": {"COM": "COM10", "IP": "192.168.50.42:2101", "GENERAL_PASSWORD": SECRET},
                 "ports": ["COM10", "weird"], "notes": ["note " + private]},
                {"name": "com0com", "installed": True, "running": True, "location": private,
                 "channel": {"CNCB0": "COM20"}, "ports": ["COM20"], "notes": []}]
    bridge = {"state": "active", "endpoint": "CNCB0", "remote": "alice-laptop:7000", "session_id": 2,
              "app_to_device": 10, "device_to_app": 20, "settings": {"baudrate": 115200, "parity": "N"},
              "lines": {"dtr": True, "rts": False}, "acknowledged": {"1": "0001c200"}, "requests_sent": 3,
              "last_error": "", "payload": SECRET,
              "events": [{"time": "t", "level": "info", "message": "token=" + SECRET}]}
    device = {"host": "192.168.50.20", "port": 3333, "token": SECRET, "tcp": {"ok": True, "ms": 1.0},
              "http": {"ok": True, "status": 200, "json": {"mode": "x", "api_key": SECRET}, "headers": {"X": SECRET}}}
    document = rt.diagnostic_document({"state": "stopped", "events": []}, GatewayProfile(), "en")
    rt.extend_diagnostics(document, backends, bridge, device)
    output = json.dumps(document, ensure_ascii=False)
    assert SECRET not in output
    assert "Program Files" not in output and "HW group" not in output
    assert "alice-laptop" not in output
    assert document["vcom_backends"][0]["location"] == "<install dir>"
    assert document["vcom_backends"][0]["channel"] == {"COM": "COM10", "IP": "192.168.50.42:2101"}
    assert document["vcom_backends"][0]["ports"] == ["COM10"]
    assert document["vcom_backends"][1]["channel"] == {"CNCB0": "COM20"}
    assert document["com0com_bridge"]["settings"] == {"baudrate": 115200, "parity": "N"}
    assert document["com0com_bridge"]["remote"] == "[host]"
    assert "acknowledged" not in document["com0com_bridge"]
    assert document["device_check"]["http"]["fields"] == {"mode": "x", "api_key": "[redacted]"}
    assert set(document["device_check"]) == {"host", "port", "tcp", "http"}
    empty = rt.extend_diagnostics({}, None, None, None)
    assert empty == {"vcom_backends": None, "com0com_bridge": None, "device_check": None}


def test_run_background_posts_one_reply():
    import queue
    replies = queue.Queue()
    rt.run_background("ok", lambda: 5, replies).join(2)
    rt.run_background("bad", lambda: (_ for _ in ()).throw(ValueError("bad value")), replies).join(2)
    first, second = replies.get_nowait(), replies.get_nowait()
    assert (first.task, first.result, first.error) == ("ok", 5, None)
    assert second.task == "bad" and "ValueError" in second.error


def test_new_gui_strings_have_translations():
    import ast
    import re
    for name in ("gui.py", "_gui_runtime.py"):
        path = os.path.join(os.path.dirname(rt.__file__), name)
        from pathlib import Path
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("_t", "tr"):
                for arg in node.args[:1]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and re.search(r"[\u3400-\u9fff]", arg.value):
                        assert arg.value in EN, arg.value
    from serial_gateway import gui
    for table in (gui._EXT_TASKS, gui._BACKEND_NOTES):
        for value in table.values():
            assert value in EN, value
    for value in gui._MATRIX_ZH.values():
        assert value in EN or not re.search(r"[\u3400-\u9fff]", value), value
    assert gui._HWVSP_LIMIT in EN and gui._BRIDGE_RESET_HELP in EN


def test_translated_event_patterns():
    tr = Translator("en")
    assert tr.event("com0com 桥接状态：已连接。") == "com0com bridge state: Connected."
    assert tr.event("新建端口对已完成。") == "Create port pair completed."
    assert tr.event("扫描完成：2 台主机开放端口（192.168.50.0/24）。") == "Scan finished: 2 host(s) with the port open (192.168.50.0/24)."


def _display_available():
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


@pytest.mark.skipif(os.environ.get("SNG_GUI_SMOKE") != "1" or not _display_available(),
                    reason="GUI smoke test is opt-in (SNG_GUI_SMOKE=1) and needs a display")
def test_gui_constructs_and_switches_language(tmp_path):
    import tkinter
    from serial_gateway.gui import _GatewayApp
    root = tkinter.Tk()
    root.withdraw()
    try:
        app = _GatewayApp(root, str(tmp_path / "profiles.json"), "zh-CN")
        assert app._notebook.index("end") == 6
        app._token_var.set(SECRET)
        bridge, worker = object(), app._worker
        app._bridge = None
        app._set_language("en")
        assert app._worker is worker and app._token_var.get() == SECRET
        assert app._notebook.tab(2, "text") == "Virtual COM"
        app._worker.submit("shutdown")
        del bridge
    finally:
        root.destroy()
