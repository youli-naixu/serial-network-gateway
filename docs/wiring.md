# Wiring (ESP32 + ASR PRO wireless flashing)

[简体中文](wiring.zh-CN.md)

Both the ESP32 and the ASR PRO use 3.3 V logic, so the signal wires connect directly without level shifting. There are three groups:

```
 ESP32                        ASR PRO
 ─────                        ───────
 GPIO9  ───────────────────→  PB6   download RX   ┐ 1. Download port (for flashing, required)
 GPIO10 ←───────────────────  PB5   download TX   ┘
 GPIO2  ───────────────────→  PA6   UART RX       ┐ 2. Application UART (tells the ASR to restart
 GPIO1  ←───────────────────  PA5   UART TX       ┘    into download mode)
 GND    ────────────────────  GND                 3. Common ground
```

| ESP32 | ASR PRO | Purpose |
|---|---|---|
| GPIO9 (TX) | PB6 (UART0 RX) | Download data: PC → ASR |
| GPIO10 (RX) | PB5 (UART0 TX) | Download replies: ASR → PC |
| GPIO2 (TX) | PA6 (Serial2 RX) | Sends the `<asr_isp>` restart command |
| GPIO1 (RX) | PA5 (Serial2 TX) | Receives the `<asr_isp:ok>` reply |
| GND | GND | Common ground |

The ESP32 pins are constants in the example sketch; change them to match your wiring. On the ASR the download port is always PB5/PB6 (UART0 in the chip manual); which pins carry the application UART depends on your ASR program.

## Before flashing

- **Unplug the USB cable from the ASR board.** Its USB-serial chip (CH340K) also drives PB6; with USB plugged in it fights the ESP32's GPIO9 and the download fails. If you must keep USB connected, put a resistor of about 1 kΩ between GPIO9 and PB6.
- The program running on the ASR must handle the `<asr_isp>` restart command (see `examples/asrpro/`). The first such program has to go in over USB the normal way; after that every download can be wireless.
- If the ASR program does not know the command (or is broken), power-cycle the ASR by hand while the flashing tool waits; the ESP32 catches it the same way.

## Why these three groups

For about 850 ms after power-up the ASR PRO listens on its download port. If it receives the sync bytes and a handshake packet it enters download mode; otherwise it runs the user program. The official USB board power-cycles the chip through transistors driven by DTR/RTS to create that moment. Wi-Fi has no such control lines, so:

1. The ESP32 sends `<asr_isp>` on the **application UART**; the ASR program replies and soft-resets.
2. The ESP32 immediately sends the sync bytes and handshake on the **download port** and catches the freshly started bootloader.
3. Once caught, the bootloader keeps waiting, and the official tool on the PC takes over over Wi-Fi.

## What wrong wiring looks like

Messages in this program's checklist and their usual causes:

| Message | Usual cause |
|---|---|
| The ASR did not answer the restart command | Application UART wires swapped, no common ground, or no `asr_isp` in the ASR program |
| Received the restart command but the download port stayed silent | GPIO9/10 and PB6/PB5 swapped or missing, or the ASR's USB still plugged in |
| Cannot reach ESP32 | ESP32 off, on another network, or the address is wrong |

The ESP32's `GET /api/maintenance/status` shows the same facts: `lastAppAck` (the ASR answered the restart command), `caught` (the download port caught the bootloader) and `lastCatchRx` (raw bytes received on the download port).

## Power

The boards may have separate supplies but must share ground. The ASR draws noticeable current when its speaker plays, so do not power it from an ESP32 GPIO. To let the ESP32 hard-reset the ASR (for recovering a broken program), add a MOSFET switch module on the ASR's 5 V line, controlled by one ESP32 GPIO.
