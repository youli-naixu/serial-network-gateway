# Verification matrix

[English](verification.md) | [简体中文](verification.zh-CN.md)

Snapshot of `0.3.0-alpha` on Windows 11 23H2 x64, Python 3.14. Each row lists evidence actually collected; anything without evidence stays pending.

| Area | Status | Evidence |
| --- | --- | --- |
| Python tests | Passed | 207 passed, 1 opt-in GUI smoke test skipped; ResourceWarning treated as error. |
| HW VSP3 Single 3.1.2 | Passed | Channel configured by the app (one admin prompt). Real `COM10` → gateway: 64 KiB of `00..FF` round-trip identical; closing the port ends the session. |
| HW VSP3 limits | Measured | No DTR/RTS/BREAK/PURGE. After open, only baud ≤ 57600 is forwarded; data bits/parity/stop only with a baud change. Server-initiated Telnet options appear as serial data. |
| com0com 3.0.0.0 signed driver | Passed | Installed with a valid signature under default Windows security settings; pair created by the app. |
| com0com bridge | Passed | `COM11` → bridge → gateway: 1.5 Mbaud and 7E2 set after open, DTR/RTS toggles and BREAK on/off all reached the gateway; 64 KiB and 16 KiB binary round-trips identical. |
| Close detection | Passed | With `ri=ropen`, closing `COM11` ends the bridge session cleanly; a DTR pulse does not. |
| GUI | Previewed | All six tabs rendered at 960×800 in Chinese and English; gateway + bridge started from the GUI and a COM11 client round-trip passed. |
| ESP32 library | Compiled | Arduino-ESP32 2.0.17 (espressif32 6.10.0): both examples build, about 356 KB flash and 23 KB static RAM each. |
| ESP-IDF firmware | Compiled | ESP-IDF 5.4.0: default and compile-check images build. |
| ESP32 on hardware | Pending | Not flashed; no Wi-Fi or UART run yet. |
| Electrical, reset/boot control | Pending | Pin map, logic levels, transmitter isolation and reset timing are unconfirmed. |
| Target programming | Pending | No real flashing session through the gateway yet. |

A TCP or RFC2217 acknowledgement shows the request reached the protocol layer. It does not prove that a pin moved or that target memory was written.
