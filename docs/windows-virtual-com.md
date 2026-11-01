# Windows virtual COM

[English](windows-virtual-com.md) | [简体中文](windows-virtual-com.zh-CN.md) · [README](../README.md)

A program that only knows COM ports needs a kernel driver to create one. This project ships no driver; it manages two products you install yourself. Both were tested on Windows 11 23H2 with Secure Boot, driver signature enforcement and Memory Integrity left at their defaults. Never disable those protections for this project.

## Pick a driver

| | HW VSP3 Single 3.1.2 | com0com 3.0.0.0 signed + bridge |
| --- | --- | --- |
| Data path | COM → driver's own TCP client → endpoint | COM ↔ hidden pair end → this app's bridge → endpoint |
| Binary data, settings at open | yes | yes |
| Baud change after open | 57600 and below only | yes (1.5 Mbaud tested) |
| Data bits / parity / stop change | only together with a baud change | yes |
| DTR / RTS / BREAK | not forwarded | forwarded |
| PURGE | not forwarded | not forwarded |
| End of session | when the app closes the port | when the app closes the port (close detection) |

Bootloaders usually toggle DTR/RTS to reset the chip and switch baud after a handshake. Choose com0com for them. HW VSP is enough for fixed-speed data.

## HW VSP3 Single

1. Download HW VSP3 **Single** from the vendor and install it. Review its licence; this project does not redistribute it.
2. In the app, open **Virtual COM**, click *Refresh*, set the COM name (default: first free number from COM10), host `127.0.0.1`, port = the gateway's listen port, keep *RFC2217* ticked.
3. *Preview* shows only the `CHANNEL0` section that will be written. The vendor's `[GENERAL]` section, including its password, is never copied or changed.
4. *Apply* asks for one administrator approval. The app backs up the vendor INI, stops the service, writes the channel and restarts it. Automatic reconnect and *connect if port closed* are turned off.

The vendor template's sample channel (`Demo`, `COM3`, `:23`) is flagged on the page because its COM name often collides with a physical adapter.

HW VSP only parses replies to the RFC2217 requests it sent. Telnet option negotiation or modem notifications sent unprompted by the server are delivered to your application as data bytes. The gateway and the ESP32 library therefore default to quiet mode.

## com0com

1. Install the **signed** build `com0com-3.0.0.0-i386-and-x64-signed` from the project's SourceForge downloads. The installer's Authenticode signature should be valid (signer *CyberCircuits*).
2. In **Virtual COM**, click *Create pair…* and pick a free name such as `COM11`. One administrator approval creates `COM11 ↔ CNCBn`, where your application uses `COM11` and the bridge opens the hidden `CNCBn`.
3. Pairs created by the app have **close detection**: the hidden end's RI line follows "application port open" (`ri=ropen`). For a pair created elsewhere, select it and click *Enable close detection*.
4. In **com0com bridge**, choose the pair, set the target (the ESP32, or `127.0.0.1` and the gateway port for a local test) and click *Start bridge*.

What the bridge does:

- reads the application's baud rate and line settings through the driver's in-band insertion records (extended `IOCTL_SERIAL_LSRMST_INSERT`), including changes made after the port is open;
- sees DTR/RTS as DSR/CTS on the hidden end and BREAK as a line-status record;
- turns each change into one RFC2217 request; sends no unsolicited Telnet negotiation;
- answers server negotiation, so standard RFC2217 servers also work;
- opens the hidden end at 8N1 binary. A freshly opened COM defaults to 7 data bits, and com0com masks bytes to the receiver's width, which would silently clear bit 7.

Without close detection the bridge cannot tell "port closed" from "DTR pulsed", so it keeps the session open. DTR alone is never treated as a close because flashing tools pulse it on purpose.

## Reconnecting

After a session ends, the gateway refuses new clients until you click *Rearm*, and the bridge shows *Restart*. Close the application's COM port first. Old buffered data is never replayed into a new session.

## Checks without hardware

```powershell
python -m serial_gateway vcom status
python -m serial_gateway selftest
```

`vcom status` is read-only: it lists drivers, pairs and warnings and never opens a COM port.
