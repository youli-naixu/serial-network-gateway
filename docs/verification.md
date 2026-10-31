# Verification matrix

[English](verification.md) | [简体中文](verification.zh-CN.md)

Snapshot of `0.2.0-alpha.1` local work. Each row lists the evidence actually collected. Missing evidence remains pending.

| Area | Status | Evidence / boundary |
| --- | --- | --- |
| Python tests | Passed locally | `135 passed` on Windows/Python 3.14; ResourceWarning as error. Includes profiles, RFC2217 parsing, simulator, gateway lifecycle, i18n and virtual COM safety checks. |
| PC simulator | Passed locally | Chinese and English self-tests; full `00..FF` 64 KiB loopback, dynamic serial settings, no stale data after reconnect. |
| Firmware build | Passed locally | ESP32-S3, ESP-IDF 5.4.0. Default safe image and compile-check image built. The compile-check image cannot run hardware I/O. |
| Virtual COM driver | Pending | HW VSP3 Single is installed locally, but no gateway COM was created or tested. Its default sample channel is not used. |
| Windows COM to network | Pending | Required before claiming virtual COM support. Use the explicit `com-test --run` command against a verified virtual port. |
| ESP32 runtime | Pending | No firmware has been flashed or connected to Wi-Fi. |
| Electrical and target control | Pending | Pin map, logic levels, transmitter isolation, reset and boot timing are unconfirmed. |
| ASR programming | Pending | No official programming session has been run through the gateway. |
| GitHub release | Blocked | Local acceptance is incomplete and GitHub authentication is unavailable in the current environment. |

A successful TCP or RFC2217 acknowledgement only shows the exchange reached the protocol layer. It does not prove that a pin moved or that target memory was written.
