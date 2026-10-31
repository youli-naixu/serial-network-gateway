# Reproducible PC simulation

[English](simulation.md) | [简体中文](simulation.zh-CN.md) · [README](../README.md)

## Scope and prerequisites

This procedure uses loopback sockets and the built-in echo simulator. It does not install a driver, create a COM device, contact a UART gateway, or emulate a target bootloader. Results from this procedure belong to the PC simulation rows in the [verification matrix](verification.md).

Use a fresh source checkout, Windows, Python 3.11+ with Tk for the GUI, and two terminals in the repository root. The current alpha's Python version matrix is pending; Python 3.14 was used for the previous local prototype. Install dependencies once, then simulation needs no external network endpoint:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

In each terminal, use the virtual environment's interpreter explicitly or activate the environment with your normal shell procedure. The commands below use `python` to mean that interpreter.

## Automated self-test

```powershell
python -m serial_gateway --version
python -m pytest -q
python -m serial_gateway selftest --bytes 65536 --output artifacts/selftest.json
```

Expected acceptance is a passing test run, self-test exit code `0`, `"ok": true`, and every entry in `checks` passing. Record the actual count; do not copy the previous prototype's count into a new result. The self-test uses a temporary loopback port, independent of saved real-device profiles.

The implemented self-test exercises RFC2217 negotiation, simulated serial/control changes, binary echo including `0xFF`, payload counters, application queue bounds, rejection of a second client, disconnect isolation, explicit rearm, and a new-session marker. Simulated line control does not validate any electrical output or baud-rate accuracy. The test suite supplies additional transport and configuration coverage; inspect the current suite when describing its scope.

Keep the full JSON locally. Public evidence should contain only the sanitized result, revision, tool versions, scope, and pass/fail summary.

## Deterministic profiles

Save the following as `local-profiles.json` in your working copy. Keep it local if you later add real addresses or notes. These names avoid dependence on the default GUI language.

```json
{
  "schema_version": 1,
  "selected": "simulation-rfc2217",
  "profiles": [
    {
      "name": "simulation-rfc2217",
      "mode": "simulator",
      "protocol": "rfc2217",
      "listen_host": "127.0.0.1",
      "listen_port": 7000
    },
    {
      "name": "simulation-raw",
      "mode": "simulator",
      "protocol": "raw",
      "listen_host": "127.0.0.1",
      "listen_port": 7001
    }
  ]
}
```

Omitted fields use the model defaults. `--config` goes before the subcommand:

```powershell
python -m serial_gateway --config local-profiles.json profiles
```

## RFC2217 manual echo

In terminal A, start the simulator and wait for state `ready`:

```powershell
python -m serial_gateway --config local-profiles.json serve --profile simulation-rfc2217 --simulate --port 7000 --duration 120 --output artifacts/rfc2217-session.json
```

In terminal B, run this PowerShell here-string. It opens an RFC2217 URL, not a Windows COM device:

```powershell
@'
import serial

payload = bytes(range(256)) * 16
with serial.serial_for_url("rfc2217://127.0.0.1:7000", baudrate=115200, timeout=5) as port:
    port.baudrate = 57600
    port.bytesize = 8
    port.parity = "N"
    port.stopbits = 1
    port.write(payload)
    received = bytearray()
    while len(received) < len(payload):
        block = port.read(len(payload) - len(received))
        if not block:
            raise TimeoutError("Incomplete RFC2217 echo")
        received.extend(block)
    assert bytes(received) == payload, "RFC2217 payload mismatch"
print("RFC2217 echo passed: 4096 bytes")
'@ | python -
```

Accept exact byte equality and the client's success message. On client close, terminal A should end the session instead of accepting another connection automatically. The saved final snapshot should describe `needs_reset`; `serial_confirmed` reports protocol replies, not measured hardware settings.

## Raw TCP manual echo

In terminal A, start a separate raw-mode session:

```powershell
python -m serial_gateway --config local-profiles.json serve --profile simulation-raw --simulate --port 7001 --duration 120 --output artifacts/raw-session.json
```

In terminal B:

```powershell
@'
import socket

payload = bytes(range(256)) * 16
with socket.create_connection(("127.0.0.1", 7001), timeout=5) as connection:
    connection.settimeout(5)
    connection.sendall(payload)
    received = bytearray()
    while len(received) < len(payload):
        block = connection.recv(len(payload) - len(received))
        if not block:
            raise ConnectionError("Incomplete raw TCP echo")
        received.extend(block)
    assert bytes(received) == payload, "Raw TCP payload mismatch"
print("Raw TCP echo passed: 4096 bytes")
'@ | python -
```

Raw mode has no serial-parameter negotiation. Do not interpret an empty serial-settings display as a transmission failure.

## GUI and session checks

```powershell
python -m serial_gateway --config local-profiles.json gui
```

Select a simulation profile and start it, then run the matching client above. Check that counters advance, the UI stays responsive, and stopping or disconnecting does not silently resume a stream. Close the client, explicitly rearm, then run a fresh client to check a new session. The CLI exits after a session ends; restart `serve` instead of looking for a CLI rearm command.

Check both UI languages once English support is integrated, including profile editing, diagnostics, errors, scaling, and shutdown. Until then, record English GUI review as pending. The self-test action always uses its own simulation and does not follow the GUI's real-device target.

## Failure interpretation

| Observation | Next check |
| --- | --- |
| Import error | Confirm the terminal uses the interpreter where the editable package and pySerial were installed. |
| Listener cannot start | Check the port is free; choose another port in both server and client. `--port 0` is useful for automated tests but requires reading the assigned port. |
| RFC2217 negotiation timeout | Ensure both endpoints use RFC2217, the server is ready, and no prior client owns the session. |
| Connection refused after a client closes | Start a new CLI session, or close the driver's old COM session and explicitly rearm in the GUI. |
| Echo or queue check fails | Preserve sanitized failure details and exact revision; do not bypass the check by dropping bytes or enabling replay. |
| TCP tests pass, serial application finds no COM port | Install and validate an independent virtual COM product using the [driver guide](windows-virtual-com.md). |

A clean simulation result is a prerequisite for driver and hardware work, not a substitute for those acceptance gates.
