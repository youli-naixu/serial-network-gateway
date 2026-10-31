# Windows virtual COM integration

[English](windows-virtual-com.md) | [简体中文](windows-virtual-com.zh-CN.md) · [README](../README.md)

## Scope and current status

Serial Network Gateway exposes a loopback TCP endpoint. A serial application that requires a Windows COM device also needs an independent virtual COM driver and a redirector. Driver installation and end-to-end virtual COM acceptance for `0.2.0-alpha.1` are pending. Finding an executable or an uninstall registration does not prove that its kernel driver loads.

First complete [PC simulation](simulation.md). Use only simulated endpoints during driver acceptance; connect real hardware after the separate [hardware gate](hardware-validation.md).

## Choose a route

| Route | Structure | What must be verified |
| --- | --- | --- |
| HW VSP3 | Application → virtual COM → HW VSP3 TCP client → gateway | Exact product version, licensing, driver signature/load status, Windows compatibility, NVT behavior, serial/control forwarding. |
| com0com + hub4com | Application → one virtual pair endpoint → paired endpoint owned by hub4com → gateway | Matching pair, exposed COM name, signed driver compatibility, hub4com filters, serial/control forwarding. |
| Native RFC2217 client | Application → RFC2217 URL → gateway | No Windows virtual COM device is created; this only covers applications that already support the protocol. |

Obtain products from their official sources and review their terms. They are not redistributed with this project. Installation can require an administrator and a restart; perform it as a separate, explicit setup step. Keep Secure Boot, driver-signature enforcement, and Memory Integrity/HVCI at their normal settings. If an available driver cannot load under those settings, mark that route blocked and choose a supported product. Disabling protections is not a project prerequisite.

## Read-only discovery

```powershell
python -m serial_gateway environment
python -m serial_gateway driver-help --protocol rfc2217 --port 7000
```

Discovery does not open a COM device or create a pair. Inspect Windows Device Manager and the vendor tool to establish actual driver state. Keep hardware IDs, device serial numbers, installation paths, and raw discovery output out of public reports.

## HW VSP3 setup

1. Install an appropriate official edition under its license. Confirm the vendor's current compatibility information for the exact Windows build and security settings. Documentation for older releases is not proof of current compatibility.
2. Start the RFC2217 simulation profile from the [simulation guide](simulation.md), using local port `7000`.
3. In HW VSP3, choose a free virtual COM number and set the TCP destination to `127.0.0.1`, port `7000`.
4. For RFC2217, use the vendor options **NVT Enable** and **Remote port setup**. Check the manual for the installed version; generic Telnet/NVT alone does not guarantee remote UART configuration.
5. Create/open the virtual port and run the COM echo check below. Inspect the gateway's requested and confirmed serial settings while changing baud rate and line settings in the application.
6. Review the redirector's connection and retry behavior. If automatic reconnect cannot be disabled, the gateway still rejects connections after a session ends until explicitly rearmed. Close the application and clear the redirector session before rearming.

For raw TCP, disable NVT/Telnet/RFC2217 processing and use the raw simulation profile on port `7001`. A real raw endpoint must already have matching UART settings. Raw mode cannot prove remote baud-rate or control-line forwarding.

## com0com and hub4com setup

Use the vendor setup utility to create a new virtual pair with a free COM name for the application. Do not rename or attach an existing physical port. The hub4com side must be an enumerated `CNCBn` endpoint with the matching `CNCAn` partner. For example, `CNCB0` is valid only if the actual pair is number `0`; it is not a universal default. Follow the upstream RFC2217 pair configuration, including baud emulation where required by that version.

The project's external adapter builds hub4com's `escparse`, `pinmap`, `linectl`, and Telnet COM-port filters. It runs an existing executable, checks the pair before opening it, restricts the target to `127.0.0.1`, and requests `--reconnect=no`. This integration still requires real driver acceptance. A plain pySerial byte bridge does not expose all Windows DCB/IOCTL changes from the other end of a virtual pair.

In PowerShell, provide the actual installed paths and enumerated endpoint when prompted:

```powershell
$Setupc = Read-Host "Absolute path to installed setupc.exe"
$Hub4com = Read-Host "Absolute path to installed hub4com.exe"
$VirtualEndpoint = Read-Host "Enumerated virtual CNCBn endpoint"
python -m serial_gateway com-bridge --setupc "$Setupc" --hub4com "$Hub4com" --endpoint "$VirtualEndpoint" --port 7000
```

Without `--run`, this command previews arguments and does not open a port. A successful preview is not pair enumeration or driver acceptance. Start the RFC2217 simulator in another terminal, then explicitly run the bridge:

```powershell
python -m serial_gateway com-bridge --setupc "$Setupc" --hub4com "$Hub4com" --endpoint "$VirtualEndpoint" --port 7000 --run
```

Leave the bridge running while the client uses the application's COM endpoint. Stop it with Ctrl+C after the check. Closing only the application may leave hub4com's network connection alive; use a deliberate bridge stop/disconnect when testing session isolation. This adapter is for RFC2217; it is not a raw-mode adapter or a driver installer.

## COM echo acceptance

Use only the newly created virtual application endpoint. Confirm its identity in the vendor utility first; opening a physical serial port can change control lines. In a separate terminal, run:

```powershell
$TestCom = Read-Host "Confirmed virtual application COM name"
@'
import sys
import serial

payload = bytes(range(256)) * 16
with serial.Serial(sys.argv[1], baudrate=115200, timeout=5, write_timeout=5) as port:
    port.write(payload)
    received = bytearray()
    while len(received) < len(payload):
        block = port.read(len(payload) - len(received))
        if not block:
            raise TimeoutError("Incomplete virtual COM echo")
        received.extend(block)
    assert bytes(received) == payload, "Virtual COM payload mismatch"
print("Virtual COM echo passed: 4096 bytes")
'@ | python - "$TestCom"
```

This establishes binary echo for one configuration only. Complete the following acceptance cases and record each independently:

| Case | Required evidence |
| --- | --- |
| Driver load and enumeration | Driver version, publisher/signature state, Windows build, normal security settings, and successful COM enumeration/open. |
| Binary integrity | Exact echo equality, including `0x00`, `0xFF`, `0x11`, and `0x13`, with no implicit text conversion. |
| Serial configuration | Change baud, data bits, parity, and stop bits; compare application requests and simulator replies. Repeat for the target's required settings. |
| Control signals | Change DTR, RTS, and BREAK separately; check which requests reach the simulator. Report unsupported or remapped signals. |
| One-session behavior | Reject a second gateway client; deliberately terminate the redirector connection; ensure the gateway requires rearm. |
| Fresh session | Close application and redirector, rearm/restart, reconnect, and exchange a different marker without old bytes. |
| Target application | Confirm the actual serial application can enumerate/open the COM port; programming acceptance requires real hardware separately. |

A driver may buffer data, emulate baud rate, remap handshake signals, or hold a socket open after COM close. Observe these behaviors instead of assuming that COM close is identical to network disconnect. Recheck line-control behavior on physical pins before relying on it for reset or boot entry.

## Evidence and recovery

Record sanitized product/version information and per-case results in the [verification matrix](verification.md). Do not publish a compatibility badge based only on an installed program or a successful preview.

If a session fails, stop the application, stop the redirector, and end the gateway session. Resolve the reported error, then start a fresh session. Do not replay a partially transmitted programming stream. Use the vendor's normal uninstall/remove-pair procedure for cleanup; the gateway does not manage kernel-driver removal.

## Official references

- [HW group: HW VSP3](https://www.hw-group.com/software/hw-vsp3-virtual-serial-port) — obtain the current product and its manual; availability and supported Windows versions can change.
- [com0com project](https://com0com.sourceforge.net/) — virtual serial pairs and related utilities.
- [hub4com README](https://com0com.sourceforge.net/hub4com/ReadMe.txt) — upstream RFC2217 client/pair examples.
- [pySerial RFC2217 documentation](https://pyserial.readthedocs.io/en/latest/url_handlers.html#rfc2217) — direct protocol clients and limitations.
