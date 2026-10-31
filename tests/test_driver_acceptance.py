import configparser
from types import SimpleNamespace

import pytest

from serial_gateway.com_acceptance import verify_virtual_identity
from serial_gateway.hw_vsp import render_channel, write_preview


def port(hwid, device="COM10"):
    return SimpleNamespace(device=device, hwid=hwid, description="Test virtual port")


@pytest.mark.parametrize("hwid", [r"ROOT\COM0COM\0001", r"EVSERIAL\PORT\001", "COM0COM", r"ROOT\EVSERIAL\0000"])
def test_virtual_identity_allowlist(hwid):
    assert verify_virtual_identity("COM10", [port(hwid)])["device"] == "COM10"


@pytest.mark.parametrize("hwid", ["USB VID:PID=1A86:55D3", r"BTHENUM\ABC", "ACPI\\PNP0501", "USB COM0COM", "unknown", "FAKEEVSERIAL"])
def test_physical_unknown_or_spoofed_names_rejected(hwid):
    with pytest.raises(ValueError):
        verify_virtual_identity("COM10", [port(hwid)])


def test_missing_duplicate_and_injected_port_names_rejected():
    with pytest.raises(ValueError):
        verify_virtual_identity("COM10", [])
    with pytest.raises(ValueError):
        verify_virtual_identity("COM10", [port("COM0COM"), port("EVSERIAL")])
    with pytest.raises(ValueError):
        verify_virtual_identity("COM10\n", [port("COM0COM")])


def test_hw_vsp_preview_has_no_password_and_disables_autoreconnect(tmp_path):
    text = render_channel("COM10", 7000)
    config = configparser.ConfigParser()
    config.read_string(text)
    assert config.sections() == ["CHANNEL0"]
    channel = config["CHANNEL0"]
    assert channel["IP"] == "127.0.0.1:7000"
    assert channel["AutoRenew"] == "0"
    assert channel["ConnectIfPortClosed"] == "0"
    assert channel["NvtPortSetup"] == "1"
    assert not any(key.lower() == "password" for key in channel)
    path = tmp_path / "preview.ini"
    write_preview(path, "COM10", 7000)
    with pytest.raises(FileExistsError):
        write_preview(path, "COM11", 7000)


@pytest.mark.parametrize("name", ["COM0", "COM10000", "COM3;", r"\\.\COM3", "COM4\n"])
def test_hw_vsp_preview_rejects_invalid_names(name):
    with pytest.raises(ValueError):
        render_channel(name, 7000)
