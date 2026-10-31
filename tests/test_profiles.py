import json
from dataclasses import replace

import pytest

from serial_gateway.models import GatewayProfile
from serial_gateway.profiles import ProfileStore


def test_new_store_defaults_to_simulator_without_creating_file(tmp_path):
    path = tmp_path / "profiles.json"
    profiles, selected = ProfileStore(path).load()
    assert profiles == [GatewayProfile()]
    assert selected == profiles[0].name
    assert not path.exists()


def test_profile_round_trip_preserves_advanced_values(tmp_path):
    store = ProfileStore(tmp_path / "设置" / "profiles.json")
    a = GatewayProfile()
    b = replace(a, name="实验板", mode="device", remote_host="192.0.2.8", buffer_bytes=8192, chunk_bytes=777, stall_timeout_s=22.0, nodelay=False)
    store.save([a, b], b.name)
    assert store.load() == ([a, b], b.name)
    assert store.get() == b
    assert list(store.path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("text", ["{bad", '{"schema_version":99}', '{"schema_version":1,"profiles":[]}'])
def test_invalid_config_is_preserved(tmp_path, text):
    path = tmp_path / "profiles.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        ProfileStore(path).load()
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("change", [
    {"mode": "unknown"}, {"listen_host": "0.0.0.0"}, {"listen_port": -1}, {"listen_port": True},
    {"remote_port": 0}, {"mode": "device", "remote_host": ""}, {"remote_host": "x\ny"},
    {"buffer_bytes": 511}, {"chunk_bytes": 4097}, {"stall_timeout_s": float("nan")},
    {"connect_timeout_s": float("inf")}, {"nodelay": 1}, {"keepalive_s": 0},
])
def test_invalid_profiles_fail_before_start(change):
    with pytest.raises(ValueError):
        replace(GatewayProfile(), **change).validate()


def test_unknown_fields_and_duplicate_names_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="未知"):
        GatewayProfile.from_dict({"password": "not-accepted"})
    store = ProfileStore(tmp_path / "profiles.json")
    with pytest.raises(ValueError):
        store.save([GatewayProfile(), GatewayProfile()], "本机模拟测试")
    assert not store.path.exists()


def test_missing_selected_profile_is_not_silently_replaced(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"schema_version": 1, "selected": "absent", "profiles": [GatewayProfile().to_dict()]}), encoding="utf-8")
    with pytest.raises(ValueError, match="不存在"):
        ProfileStore(path).load()
