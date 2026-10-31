import ast
import json
import re
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from serial_gateway import i18n
from serial_gateway._gui_runtime import clean_text, diagnostic_document
from serial_gateway.i18n import Translator, load_language, save_language, translate_error, translate_event
from serial_gateway.locales import EN, HELP_ZH
from serial_gateway.models import GatewayProfile
from serial_gateway.profiles import ProfileStore


def test_preferences_are_separate_and_default_chinese(tmp_path, monkeypatch):
    preferences = tmp_path / "preferences.json"
    monkeypatch.setattr(i18n, "preferences_path", lambda: preferences)
    store = ProfileStore(tmp_path / "profiles.json")
    profile = GatewayProfile(name="用户自己的名字", notes="保留")
    store.save([profile], profile.name)
    before = store.path.read_bytes()
    assert i18n.resolve_language() == "zh-CN"
    preferences.write_text('{"other_preference": true}', encoding="utf-8")
    save_language("en-US")
    assert load_language() == "en"
    assert Translator().language == "en"
    assert Translator("zh-CN").language == "zh-CN"
    assert json.loads(preferences.read_text()) == {"language": "en", "other_preference": True}
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("content", ["{bad", "[]", '{"language": "unknown"}', "x" * 17000])
def test_invalid_preferences_fall_back_without_write(tmp_path, content):
    path = tmp_path / "preferences.json"
    path.write_text(content)
    assert load_language(path) == "zh-CN"
    assert path.read_text() == content


def test_failed_preferences_write_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    save_language("zh-CN", path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("read only")
    monkeypatch.setattr(i18n.os, "replace", fail)
    with pytest.raises(OSError):
        save_language("en", path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_catalog_placeholders_and_help():
    def fields(text):
        return {field for _, field, _, _ in Formatter().parse(text) if field is not None}
    for source, target in EN.items():
        assert fields(source) == fields(target), source
        assert not re.search(r"[\u3400-\u9fff]", target), source
    assert "Getting started" in Translator("en")(HELP_ZH, path="profiles.json")


@pytest.mark.parametrize("source,expected", [
    ("网络错误（ConnectionRefusedError，错误码 10061）。", "Network error (ConnectionRefusedError, code 10061)."),
    ("代理错误（RuntimeError）。 请手动重新待命。", "Gateway error (RuntimeError). Rearm manually."),
    ("ValueError: 设备模式必须填写远端 IP 或主机名。", "ValueError: Device mode requires a remote IP or hostname."),
    ("当前 123 B  /  峰值 456 B", "Current 123 B  /  Peak 456 B"),
    ("启动配置“自定义名字”。", "Starting profile “自定义名字”."),
    ("运行配置：自定义名字", "Running profile: 自定义名字"),
    ("结果：通过", "Result: Passed"),
    ("未知设备报错 CustomError 错误码 42", "Unrecognized event (CustomError, code 42)"),
])
def test_legacy_messages_are_translated_without_mixing(source, expected):
    assert translate_event(source, "en") == expected
    assert translate_event(source, "zh-CN") == source


def test_unknown_error_keeps_class_and_code():
    text = translate_error(OSError(13, "未知错误"), "en")
    assert "PermissionError" in text
    assert "code 13" in text
    assert "未知" not in text


def test_diagnostics_omit_names_paths_payload_and_notes():
    private_path = "C:" + r"\Users\Alice Smith\private.json"
    snapshot = {"state": "error", "message": "Alice Smith failed", "last_error": "PermissionError: " + private_path, "payload": "SECRET", "events": [{"message": "Alice Smith payload=SECRET", "time": 1, "level": "error"}], "serial_requested": {"baudrate": 115200}}
    profile = GatewayProfile(name="Alice Smith", notes="SECRET", remote_host="alice-laptop")
    output = json.dumps(diagnostic_document(snapshot, profile, "en"))
    assert "Alice" not in output and "SECRET" not in output and "alice-laptop" not in output
    assert "private.json" not in output and "PermissionError" in output
    assert "115200" in output
    assert "Alice" not in clean_text("PermissionError: '" + private_path + "'")


def test_serial_protocol_labels():
    from serial_gateway.gui import _serial_text
    text = _serial_text({"baudrate": 115200, "parity": "E", "rts": True}, "en")
    assert "Baud rate 115200" in text and "Parity Even" in text and "RTS On" in text
    assert _serial_text({}, "en") == "Waiting for client settings"


def test_switch_preserves_unsaved_settings_worker_and_run_state():
    from serial_gateway.gui import _GatewayApp
    class Var:
        def __init__(self, value): self.value = value
        def get(self): return self.value
        def set(self, value): self.value = value
    app = _GatewayApp.__new__(_GatewayApp)
    app._notebook = Mock()
    app._notebook.index.return_value = 2
    app._modes = {"本机模拟": "simulator", "远端设备": "device"}
    app._protocols = {"RFC2217": "rfc2217", "原始 TCP": "raw"}
    app.vars = {"mode": Var("远端设备"), "protocol": Var("原始 TCP"), "name": Var("我自己的配置"), "listen_port": Var("4321")}
    app._form_baseline = {"mode": "本机模拟", "protocol": "RFC2217", "name": "我自己的配置", "listen_port": "7000"}
    app._language_var = Var("简体中文")
    app.root = Mock()
    app.root.winfo_children.return_value = []
    app._outer = Mock()
    app._editable, app._profile_controls, app._wrap_labels = [], [], []
    app._build_window = Mock()
    app._profile_combo = Mock()
    app.profiles = [GatewayProfile(name="我自己的配置")]
    app._worker = object()
    app._running = True
    worker = app._worker
    for name in ("_form_changed", "_render_snapshot", "_render_events", "_render_checks"):
        setattr(app, name, Mock())
    app._set_language("en")
    assert app.vars["mode"].get() == "Remote device"
    assert app.vars["protocol"].get() == "Raw TCP"
    assert app.vars["name"].get() == "我自己的配置"
    assert app.vars["listen_port"].get() == "4321"
    assert app._form_baseline["listen_port"] == "7000"
    assert app._is_dirty()
    assert app._worker is worker and app._running
    app._set_language("zh-CN")
    assert app.vars["mode"].get() == "远端设备"
    assert app._form_baseline["mode"] == "本机模拟"
    assert app._worker is worker and app._is_dirty()
