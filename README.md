# Serial Network Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

Use a serial port over the network. A Windows application opens an ordinary `COMx` port; the bytes, serial settings and control lines travel over TCP/RFC2217 to an ESP32, which drives a real UART. The project is generic: it was started to flash a voice module wirelessly, but nothing in the data path is target specific.

**Status: `0.3.0-alpha`. The PC side has been tested end to end with real virtual COM drivers on Windows 11. The ESP32 library compiles but has not yet run on hardware.** See the [verification matrix](docs/verification.md).

```text
Application (flashing tool, terminal, ...)
   │ opens COM11
   ▼
com0com pair  COM11 ↔ CNCB0        (or HW VSP3: COM10 → TCP)
   ▼
PC bridge: data, settings, DTR/RTS, BREAK → RFC2217
   ▼ TCP
ESP32 gateway library ── UART ──▶ target board
```

## What it does

**Desktop app (Windows, Python/Tk, Chinese and English).** One window with six tabs:

| Tab | Purpose |
| --- | --- |
| Connection / Advanced | Profiles with import and export, raw TCP or RFC2217, timeouts and buffer sizes. |
| Status & traffic | Byte counters per direction, queue peaks, requested and confirmed serial settings, event log. |
| Virtual COM | Detects HW VSP3 Single and com0com and shows what each can forward. Configures the HW VSP channel; creates and removes com0com pairs. Each change asks for one Windows administrator approval. |
| com0com bridge | Connects the hidden end of a pair to an RFC2217 endpoint. Forwards baud and format changes made **after** the port is opened, plus DTR, RTS and BREAK. Ends the session when the application closes its port. |
| ESP32 device | TCP reachability, an optional RFC2217 baud probe, the device's HTTP status, and a LAN scan for gateways. The token stays in memory only. |

A local echo simulator and a self-test cover the whole path without hardware. Every feature is also available from the command line.

**ESP32 library (`arduino/`, Arduino-ESP32 2.x).** `SerialNetworkGateway` adds a maintenance mode to an existing firmware. While enabled it takes over one UART and forwards it over RFC2217; afterwards it gives the UART back. It uses bounded queues and reports faults instead of dropping data, accepts one client at a time, never carries data across sessions, supports a source IP allow-list and exits automatically after a timeout. Optional RESET and BOOT pins can be pulsed from your own authenticated code.

A standalone ESP-IDF firmware in `firmware/` is kept as an alternative build.

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

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m serial_gateway selftest
.\.venv\Scripts\python.exe -m serial_gateway gui
```

Loop test with com0com and no hardware:

1. In **Virtual COM**, click *Create pair…* (for example `COM11`) and approve the administrator prompt.
2. In **Connection**, keep *Local simulator* and click *Start gateway*.
3. In **com0com bridge**, choose the pair and click *Start bridge*. The target defaults to `127.0.0.1:7000`.
4. Open `COM11` in any terminal. What you send comes back, and the gateway shows your serial settings and DTR/RTS.

For a real device, point the bridge at the ESP32's address and port (default `3333`).

## Command line

```powershell
python -m serial_gateway --help
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
