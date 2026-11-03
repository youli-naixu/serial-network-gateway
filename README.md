# ASR Wireless Flash Assistant · Serial Network Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

Flash an ASR PRO voice module over Wi-Fi through an ESP32, without a USB cable: open the vendor's tool (TianwenBlock) on your PC and click Download.

```text
TianwenBlock ── choose "ASR 无线烧录 (COMn)" ──▶ this program ── Wi-Fi ──▶ ESP32 ──▶ ASR PRO download port
```

**Status: `0.4.0`.** Tested on Windows 11 + ESP32-S3 + ASRPRO-4M: a full 1.7 MB firmware downloads wirelessly in about 50 seconds, repeatedly. See the [verification matrix](docs/verification.md).

## Three steps

1. **Wire it**: ESP32 GPIO9→ASR PB6, GPIO10←PB5 (download port); GPIO2→PA6, GPIO1←PA5 (application UART); common ground. Unplug the ASR's USB while flashing. See [Wiring](docs/wiring.md).
2. **Install**:
   - Flash [`arduino/examples/AsrWirelessFlash`](arduino/examples/AsrWirelessFlash/AsrWirelessFlash.ino) to the ESP32 (set your Wi-Fi name and password).
   - Over USB, put a program with the restart command on the ASR once: the [minimal project](examples/asrpro/asr_isp_minimal.hd), or add the [snippet](examples/asrpro/asr_isp_snippet.cpp) to your own.
   - Install the [signed com0com](https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/) (`com0com-3.0.0.0-i386-and-x64-signed.zip`) on the PC.
3. **Flash**: download `ASR 无线烧录助手` from [Releases](https://github.com/youli-naixu/serial-network-gateway/releases) and open it. The first time, enter the ESP32 address and click "Prepare virtual COM" (one administrator prompt). Then choose **ASR 无线烧录 (COMn)** in TianwenBlock and click Download.

The window is a single page: a status light, the port to choose, a checklist (anything not ready turns red with a fix button) and a download history. Closing the window keeps it waiting in the tray; run-at-sign-in is off by default and can be turned on in Settings.

## How it works

For about 850 ms after power-up the ASR PRO enters download mode only if its download port receives sync bytes and a handshake. The official USB board creates that moment by power-cycling the chip through transistors driven by DTR/RTS. Wi-Fi has no such lines, so:

1. When the flashing tool opens the virtual COM, this program asks the ESP32 to prepare the target.
2. The ESP32 sends `<asr_isp>` on the application UART; the ASR program replies and soft-resets, and the ESP32 catches the bootloader on the download port (about 135 ms measured).
3. Once caught the bootloader waits; this program relays the tool's data and baud changes (115200→750000→1500000) to the ESP32 over RFC2217.

On the PC, [com0com](docs/wiring.md) provides a pair of virtual ports: one end for TianwenBlock, the other read by this program. Protocol details (frames, CRC, the two stages) are in [docs/asrpro-download-protocol.md](docs/asrpro-download-protocol.md).

## Advanced tools

"Advanced tools" in the main window opens the general-purpose features; the project also works for other "serial over the network" uses:

| Tab | Purpose |
| --- | --- |
| Gateway | Profiles, raw TCP or RFC2217, timeouts and buffers; byte counters and event log. |
| Virtual COM | Detect HW VSP3 Single and com0com, create and remove com0com pairs; bridge com0com to any RFC2217 endpoint. |
| ESP32 device | TCP reachability, optional baud probe, device HTTP status, LAN scan. |
| Diagnostics | Local environment check, offline self-test, sanitised diagnostics export. |

**ESP32 library (`arduino/`, Arduino-ESP32 2.x)**: `SerialNetworkGateway` adds a "maintenance mode" to existing firmware: while active it owns one UART and relays it over RFC2217, then hands it back. `firmware/` keeps a standalone ESP-IDF firmware as an alternative.

## Which virtual COM driver

This project ships no kernel driver. It manages drivers you install yourself:

| Measured on Windows 11 23H2 | HW VSP3 Single 3.1.2 | com0com 3.0.0.0 (signed) + this bridge |
| --- | --- | --- |
| Binary data, settings at open | yes | yes |
| Baud change after open | 57600 and below only | yes, tested at 1.5 Mbaud |
| Data bits / parity / stop change | only together with a baud change | yes |
| DTR / RTS / BREAK | no | yes |
| Session ends when the app closes the port | yes | yes, with close detection |

Use **com0com** when the target needs reset lines or a baud switch during the session, as most bootloaders do. HW VSP is simpler when plain data at one fixed speed is enough.

HW VSP also passes any Telnet option bytes the server sends unprompted to the application as serial data. The gateway therefore runs in quiet mode by default and only answers what the client asks.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m serial_gateway            # ASR Wireless Flash Assistant
.\.venv\Scripts\python.exe -m serial_gateway gui        # Advanced tools
.\.venv\Scripts\python.exe -m serial_gateway selftest
```

Loop test with com0com and no hardware:

1. In Advanced tools, **Virtual COM** tab, click *Create pair…* (for example `COM11`) and approve the administrator prompt.
2. In **Gateway**, keep *Local simulator* and click *Start gateway*.
3. Further down the **Virtual COM** tab, in the com0com bridge, choose the pair and click *Start bridge*. The target defaults to `127.0.0.1:7000`.
4. Open `COM11` in any terminal. What you send comes back, and the gateway shows your serial settings and DTR/RTS.

For a real device, point the bridge at the ESP32's address and port (default `3333`).

## Command line

```powershell
python -m serial_gateway --help
python -m serial_gateway station config --host 192.168.50.20
python -m serial_gateway station prepare --yes
python -m serial_gateway station run
python -m serial_gateway vcom status
python -m serial_gateway vcom create-pair --com COM11 --yes
python -m serial_gateway bridge --endpoint CNCB0 --host 127.0.0.1 --port 7000
python -m serial_gateway device check --host 192.168.50.20 --token-env GATEWAY_TOKEN
python -m serial_gateway device scan
python -m serial_gateway serve --simulate --port 7000
```

Commands that change the system require `--yes`. A token is read only from the environment variable named by `--token-env`.

## Safety

- 3.3 V logic only. Only one transmitter may drive a target's RX line, so disconnect or isolate an onboard USB-UART before wiring the ESP32 TX.
- RFC2217 has no authentication. Keep the ESP32 on a trusted network and set its IP allow-list.
- Diagnostics exports leave out tokens, payload bytes and installation paths.

## Documentation

| Guide | Scope |
| --- | --- |
| [Wiring](docs/wiring.md) | How to wire the ESP32 to the ASR PRO and what wrong wiring looks like. |
| [Examples](examples/README.md) | ESP32 sketch, ASR snippet and minimal project. |
| [ASR download protocol](docs/asrpro-download-protocol.md) | Frame format and sequence of the official tool (from captures). |
| [Windows virtual COM](docs/windows-virtual-com.md) | Driver setup, measured behaviour, close detection. |
| [Remote endpoint contract](docs/remote-contract.md) | What the network side must implement. |
| [PC simulation](docs/simulation.md) | Reproducible tests without hardware. |
| [ESP32 library](arduino/README.md) | Wiring, API, embedding in an existing firmware. |
| [ESP-IDF firmware](docs/firmware.md) | Alternative standalone build. |
| [Hardware validation](docs/hardware-validation.md) | Steps before connecting a real target. |
| [Verification matrix](docs/verification.md) | What has and has not been tested. |

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q -W error::ResourceWarning
python tools/check_public_tree.py
```

See [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md) and the [changelog](CHANGELOG.md).

## License

Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). The RFC2217 parser in `arduino/src/rfc2217/` and `firmware/` comes from [igrr/rfc2217-server](https://github.com/igrr/rfc2217-server) (Apache-2.0), with local changes listed in the source. pySerial is BSD-3-Clause. HW VSP3 and com0com are separate products and are not bundled.
