# Serial Network Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

A reusable serial-over-network toolkit for Windows applications and UART targets. The PC gateway provides a loopback TCP endpoint, transparent raw TCP or RFC2217 forwarding, profiles, session statistics, and a local echo simulator. A separate ESP32-S3 firmware implementation is being prepared with ESP-IDF and `igrr/rfc2217-server`.

**Release target: `0.2.0-alpha.1` — local acceptance pending.** The previous `0.1.0` PC prototype had 99 locally passing tests. That historical result does not verify this revision, a Windows virtual COM driver, firmware, or a physical target. See the [verification matrix](docs/verification.md) for the current evidence boundaries. No ready-to-use ASR flashing or hardware-tested claim is made.

## How it fits together

```text
Serial application
    |
    v
Separately installed virtual COM driver + redirector
    |  raw TCP or RFC2217
    v
PC gateway at 127.0.0.1:7000
    |
    +--> Built-in local echo simulator
    |
    +--> Network UART endpoint --> UART target
         (ESP32-S3 firmware under development)
```

Clients that already support TCP or RFC2217 can connect without a virtual COM driver. The PC gateway itself does not create a Windows COM device. It forwards the selected protocol; it does not convert raw TCP into RFC2217.

ASR PRO is one proposed target example. Other UART targets can use the same PC transport when their protocol, electrical interface, and control requirements match. The simulator is an echo endpoint, not a target bootloader.

## Quick start from source

Run these commands in the repository root. Windows with Python 3.11 or newer is the build target; the previous local prototype used Python 3.14. Version-by-version validation of this alpha is pending. GUI use requires Tk support.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m serial_gateway selftest --output artifacts/selftest.json
.\.venv\Scripts\python.exe -m serial_gateway gui
```

A successful self-test exits with code `0` and reports `"ok": true`. It creates temporary loopback endpoints and does not load a real-device profile or open a COM device. Follow the [reproducible PC simulation guide](docs/simulation.md) for raw TCP and RFC2217 client examples.

The previous GUI prototype is Chinese. English GUI support is in progress; English documentation does not imply that every UI label has already been translated or reviewed. Opening the GUI does not start a connection. Start with the simulator before connecting a driver or device.

No downloadable binary, signed installer, or verified portable build is promised by this source quick start. Packaging acceptance is tracked separately.

## Capabilities and limits

| Area | PC implementation / boundary |
| --- | --- |
| Transport | Raw TCP and RFC2217 forwarding; RFC2217 observation for protocol statistics, without rewriting the forwarded stream. |
| Local listener | Loopback only. Examples use `127.0.0.1`. One active client; concurrent clients are rejected. |
| Buffering | Bounded application queues in both directions, backpressure, configurable chunk size and timeouts. Queue limits do not bound every operating-system or driver buffer. |
| Sessions | Disconnect ends the session and discards pending application queues. No automatic replay or continuation. |
| Profiles | JSON profiles, import/export in the GUI, schema validation, atomic saving. |
| Diagnostics | Local simulation, byte/queue counters, observed serial requests and replies, read-only environment inspection. |
| Virtual COM | External product required. The optional hub4com adapter uses an existing com0com pair; it does not install a driver. |
| Firmware | Standalone ESP-IDF ESP32-S3 implementation in progress; build and hardware acceptance pending. |
| Target programming | Application and target dependent. TCP delivery and RFC2217 replies do not prove successful programming or physical signal timing. |

Raw TCP carries bytes only. Set UART parameters at the remote endpoint in advance. RFC2217 can negotiate serial settings only to the extent that the driver, remote implementation, and wiring support them. Simulator acceptance of DTR, RTS, BREAK, or a baud rate is not evidence that hardware can reproduce those settings.

## Command line

After activating the virtual environment, or using its Python executable explicitly:

```powershell
python -m serial_gateway --help
python -m serial_gateway --version
python -m serial_gateway profiles
python -m serial_gateway environment
python -m serial_gateway selftest --bytes 65536 --output artifacts/selftest.json
python -m serial_gateway serve --simulate --port 7000
python -m serial_gateway driver-help --protocol rfc2217 --port 7000
```

`--config` is a global option placed before the subcommand. For example, `python -m serial_gateway --config local-profiles.json profiles` reads the configuration described in the [simulation guide](docs/simulation.md). `serve --duration 30` sets a bounded run time; `--port 0` requests a temporary local port.

After a session ends, close the application's COM connection and clear the redirector's old session. In the GUI, explicitly rearm the gateway; in the CLI, start a new `serve` process. Check the final state and error fields as well as the process exit code.

`environment` reads device metadata and installed-program registrations without opening a COM port. Its output can contain device identifiers and local paths; redact it before sharing.

## Documentation

| Guide | Scope |
| --- | --- |
| [PC simulation](docs/simulation.md) | Source setup, deterministic profiles, binary echo checks, expected results. |
| [Windows virtual COM](docs/windows-virtual-com.md) | HW VSP3 and com0com/hub4com options; driver acceptance procedure. |
| [Remote endpoint contract](docs/remote-contract.md) | Raw/RFC2217 behavior, control settings, sessions, network boundaries. |
| [Firmware build](docs/firmware.md) | Standalone ESP-IDF build prerequisites and evidence gate. |
| [Hardware validation](docs/hardware-validation.md) | ESP32-S3 electrical checks, UART/control validation, ASR example gate. |
| [Verification matrix](docs/verification.md) | Previous-prototype evidence, current pending checks, publication gate. |

Every guide has a matching Simplified Chinese version linked at its top.

## Development and release

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m serial_gateway selftest --output artifacts/selftest.json
python -m serial_gateway gui
```

Review both GUI languages and scaling before packaging. Record the exact source revision, Windows/Python versions, test results, and artifact checksums. Release binaries and public uploads require local acceptance of the scope actually claimed; unresolved driver or hardware rows must remain explicitly pending. See [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), and the [changelog](CHANGELOG.md).

## License and third-party software

Original project code and documentation are licensed under [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution and dependency boundaries. pySerial is BSD-3-Clause; the `igrr/rfc2217-server` component is Apache-2.0. Virtual COM products are independent, separately obtained tools and are not bundled or relicensed by this project.
