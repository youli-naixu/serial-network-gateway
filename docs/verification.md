# Verification matrix

[English](verification.md) | [简体中文](verification.zh-CN.md)

Snapshot of `0.4.0` on Windows 11 23H2 x64, Python 3.14, ESP32-S3 (N16R8) and ASR PRO (ASRPRO-4M). Each row lists evidence actually collected; anything without evidence stays pending.

| Area | Status | Evidence |
| --- | --- | --- |
| Python tests | Passed | 237 passed including the opt-in GUI smoke test; ResourceWarning treated as error. |
| HW VSP3 Single 3.1.2 | Passed | Channel configured by the app (one admin prompt). Real `COM10` → gateway: 64 KiB of `00..FF` round-trip identical; closing the port ends the session. |
| HW VSP3 limits | Measured | No DTR/RTS/BREAK/PURGE. After open, only baud ≤ 57600 is forwarded; data bits/parity/stop only with a baud change. Server-initiated Telnet options appear as serial data. |
| com0com 3.0.0.0 signed driver | Passed | Installed with a valid signature under default Windows security settings; pair created by the app. |
| com0com bridge | Passed | `COM11` → bridge → gateway: 1.5 Mbaud and 7E2 set after open, DTR/RTS toggles and BREAK on/off all reached the gateway; 64 KiB and 16 KiB binary round-trips identical. |
| Close detection | Passed | With `ri=ropen`, closing `COM11` ends the bridge session cleanly; a DTR pulse does not. |
| Flashing assistant | Passed | Single-page window in Chinese and English; labelled port `ASR 无线烧录 (COM8)` in the Windows Ports class is listed by TianwenBlock's Qt port picker; checklist, history, tray, single instance and run-at-sign-in toggle exercised. |
| Advanced tools window | Previewed | Four tabs (Gateway, Virtual COM, ESP32 device, Diagnostics) rendered in Chinese and English. |
| ESP32 library | Compiled | Arduino-ESP32 2.0.17 (espressif32 6.10.0): all three examples build, including `AsrWirelessFlash`. |
| ESP-IDF firmware | Compiled | ESP-IDF 5.4.0: default and compile-check images build. |
| ESP32 on hardware | Passed | The same reset-and-catch code embedded in an ESP32-S3 production firmware: `<asr_isp>` answered in 22–282 ms, bootloader caught in about 135 ms. The standalone `AsrWirelessFlash` sketch is compile-tested only. |
| Electrical | Passed | 3.3 V direct wiring per docs/wiring.md. With the ASR's USB plugged in, its CH340K drives PB6 as well and the download port stays silent; unplugged it works. |
| Target programming | Passed | TianwenBlock's makebin.exe through the labelled port, Wi-Fi and the ESP32: full 1,699,840-byte ASRPRO-4M image written and verified in 42–55 s, five times including back-to-back sessions; ASR application traffic normal afterwards. |
| ASR examples | Partly | `asr_isp_minimal.hd` C++ compiles with the TianwenBlock 0.2.8 SDK. The spoken prompt needs TianwenBlock's online "generate model" step and has not been flashed or listened to. |

A TCP or RFC2217 acknowledgement shows the request reached the protocol layer. It does not prove that a pin moved or that target memory was written.
