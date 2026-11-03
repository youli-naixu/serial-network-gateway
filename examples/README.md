# Examples

[简体中文](README.zh-CN.md) · Wiring: [docs/wiring.md](../docs/wiring.md)

## ESP32 side

[`arduino/examples/AsrWirelessFlash`](../arduino/examples/AsrWirelessFlash/AsrWirelessFlash.ino): a minimal Arduino sketch (ESP32 / ESP32-S3, Arduino-ESP32 2.0.x).

- Joins Wi-Fi (edit `APP_WIFI_SSID` / `APP_WIFI_PASSWORD` in the file or define them as build flags).
- `POST /api/maintenance/enter?mode=isp`: sends `<asr_isp>` on the application UART so the ASR restarts, catches the bootloader on the download port, then opens the RFC2217 gateway on TCP 3333.
- `GET /api/maintenance/status`: `lastAppAck`, `caught`, `lastCatchMs` and the gateway status.
- Pins are constants at the top of the file; change them to match your wiring.
- The example endpoints have no authentication. Use them on a trusted network only, or add a token check.

In the PC program's Settings, enter the ESP32's address; with the enter URL left empty it uses `http://<ESP32 address>/api/maintenance/enter?mode=isp`.

To add this to an existing ESP32 program, follow the embedding notes in [arduino/README.md](../arduino/README.md) and copy `resetAsrIntoBootloader()` from the example.

## ASR PRO side

Requires TianwenBlock (the vendor's IDE; not included here).

- [`asrpro/asr_isp_snippet.cpp`](asrpro/asr_isp_snippet.cpp): about a dozen lines for your own TianwenBlock project (code mode). On `<asr_isp>` it replies `<asr_isp:ok>`, then soft-resets. A "speak first, then restart" variant is included.
- [`asrpro/asr_isp_minimal.hd`](asrpro/asr_isp_minimal.hd): a minimal project that TianwenBlock opens directly. Wake word "天问五幺", command "测试一下". On the flashing command it says "收到烧录命令，正在重启进入下载模式" (flashing command received, restarting into download mode) and then restarts.

### How to use

1. Open `asr_isp_minimal.hd` in TianwenBlock (code mode) and click "生成模型" (generate model). This needs the internet and a TianwenBlock account for speech synthesis.
2. Download it over **USB the first time**. The old firmware does not know the restart command.
3. Unplug the ASR's USB cable and wire the ESP32 as in the wiring guide. From then on, choose "ASR 无线烧录 (COMn)" in TianwenBlock to download wirelessly.

When adding the restart command to your own project:

- Send `<asr_isp:ok>` **before** speaking and restarting; the ESP32 waits only 300 ms for it.
- Keep prompt plus restart under about 7 seconds. The ESP32 example tries to catch the bootloader for 8 s; after that, power-cycle the ASR by hand.
