# ASR PRO official download protocol (from captures)

[简体中文](asrpro-download-protocol.zh-CN.md)

Notes from listening, through a com0com pair and a relay script, to one complete download of an ASRPRO-4M by the `makebin.exe` shipped with TianwenBlock 0.2.8. The frame format and CRC were checked against all 1700+ device-to-host frames. Fields marked "guess" have no official documentation behind them. This project does not depend on any of this; it relays bytes transparently. The notes help with troubleshooting.

## Invocation

```text
makebin.exe "&serial=COMx&chip=ASRPRO-4M&file=fw.bin&erase=false&lib=false&"
```

Run from the TianwenBlock folder; the firmware is `asrpro\fw.bin`. Use `ASRPRO-4M` for the 4 MB chip. Success ends with "复位设备成功 / 更新完毕".

## Frame format

```text
A5 0F | LEN (2 B, little-endian) | TYPE | CMD | FLAG | PAYLOAD (LEN bytes) | CRC16 (2 B, little-endian) | FF
```

- `TYPE`: `A0` host command, `A1` device request or notice (the host handshake also uses `A1`), `A2` reply, `A3` device log/progress text.
- `FLAG`: in host commands `01` mostly marks queries or settings that expect a reply; `00` in replies (guess).
- `CRC16`: CRC-16/XMODEM (poly 0x1021, init 0, no reflection) over `TYPE` through the end of `PAYLOAD`, excluding `A5 0F` and the length.
- Example: handshake request `A5 0F 00 00 A1 83 00 67 C4 FF`, reply `A5 0F 00 00 A2 83 00 37 9D FF`.

## Sequence

### 0. Entering the bootloader

- The official board power-cycles the chip with transistors driven by the CH340K's DTR/RTS, then sends `0x55` ×10 for baud sync and the handshake.
- The chip enters the bootloader only if the handshake arrives within about 850 ms of power-up; otherwise it runs the user program. Handshakes 0–900 ms after power-up all succeeded.
- **Once caught, the bootloader keeps waiting.** Catching it first and letting makebin connect 10 s later still flashes fine; makebin also retries its handshake.
- A soft reset from the user program (`dpmu_software_reset_system_config()`) opens the same window. The wireless approach in this project relies on that.

### 1. ROM: 115200 → 750000, download a 16 KB loader

| Direction | Frame | Meaning |
|---|---|---|
| host→dev | `A1 83` | Handshake |
| dev→host | `A2 83` | Handshake reply |
| host→dev | `A0 85 01` | Query; reply payload `4E 00 00 00` (version, guess) |
| host→dev | `A0 0B 01` + `u32 baud` | Change baud, `B0 71 0B 00` = 750000 |
| host→dev | `A0 06 00` + `addr u32, size u32, u16` | Target region: `0x1FF58000` (RAM), `0x4000` |
| dev→host | `A1 08` + `offset u32, 0x1000 u32` | Device pulls the next block |
| host→dev | `A2 08` + `offset u32` + 1024 bytes | One 1 KB block |
| dev→host | `A1 09` | Region received |
| host→dev | `A0 8A 00` | Verify and start the loader; reply payload `01` = OK |

The host closes and reopens the port before every baud change.

### 2. Loader: 115200 → 1500000, write flash

| Direction | Frame | Meaning |
|---|---|---|
| dev→host | `A1 03` | Loader ready |
| host→dev | `A0 05 01` | Handshake |
| host→dev | `A0 0B 01` + `60 E3 16 00` | Switch to 1,500,000 |
| host→dev | `A0 06 00` + `0, 0x19F000, 0000` | Target region: flash 0, 0x19F000 |
| host→dev | `A0 07 00` + `0, 0x400000, 0x1000` | Erase (address, length, sector size; guess) |
| dev→host / host→dev | `A1 08` / `A2 08` | As above, 1 KB blocks |
| dev→host | `A3 11` + text | Progress, e.g. `0:65536/1699840` |
| host→dev | `A0 0A 00` + `0, 0x19F000, crc16` | Verify region; reply payload `01` = OK |
| host→dev | `A0 A1 00` | Reset the device |

## What this means for wireless relaying

- The device pulls data and acknowledges every block, so network latency is harmless. At 1,500,000 baud the relay needs enough UART receive buffer (8 KB in the ESP32 example).
- DTR/RTS power cycling cannot cross a wireless link, so the ESP32 asks the ASR program to soft-reset, catches the bootloader on the download port first, then hands it to makebin. The `0x7F` bytes and DTR/RTS changes makebin sends while waiting are ignored and do not disturb the handshake that follows.
