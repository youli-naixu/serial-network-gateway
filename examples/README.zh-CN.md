# 示例代码

[English](README.md) · 接线见 [docs/wiring.zh-CN.md](../docs/wiring.zh-CN.md)

## ESP32 端

[`arduino/examples/AsrWirelessFlash`](../arduino/examples/AsrWirelessFlash/AsrWirelessFlash.ino)：最小可用的 Arduino 程序（ESP32 / ESP32-S3，Arduino-ESP32 2.0.x）。

- 连上 Wi-Fi（编辑文件里的 `APP_WIFI_SSID` / `APP_WIFI_PASSWORD`，或用编译参数定义）。
- `POST /api/maintenance/enter?mode=isp`：经业务串口发 `<asr_isp>` 让 ASR 重启，在下载口接住 bootloader，再在 TCP 3333 打开 RFC2217 网关。
- `GET /api/maintenance/status`：`lastAppAck`、`caught`、`lastCatchMs` 和网关状态。
- 引脚在文件开头的常量里，按实际接线修改。
- 示例接口没有鉴权，只在可信网络里用；要放到别人能访问的网络里，请自己加令牌校验。

在电脑端“设置”里填 ESP32 的地址即可，进入下载模式的地址留空会自动使用 `http://<ESP32地址>/api/maintenance/enter?mode=isp`。

想把功能加进已有的 ESP32 程序：参考 [arduino/README.zh-CN.md](../arduino/README.zh-CN.md) 的嵌入说明，并照搬示例里的 `resetAsrIntoBootloader()`。

## ASR PRO 端

需要天问Block（官方软件，本仓库不附带）。

- [`asrpro/asr_isp_snippet.cpp`](asrpro/asr_isp_snippet.cpp)：十几行，贴进你自己的天问工程（代码模式）。收到 `<asr_isp>` 先回 `<asr_isp:ok>`，再软件复位；另附“先播报再重启”的写法。
- [`asrpro/asr_isp_minimal.hd`](asrpro/asr_isp_minimal.hd)：最简工程，天问Block 直接打开。唤醒词“天问五幺”，命令词“测试一下”；收到烧录命令时说“收到烧录命令，正在重启进入下载模式”，说完再重启。

### 用法

1. 天问Block 打开 `asr_isp_minimal.hd`（代码模式），点“生成模型”，等语音合成和模型生成完成（需要联网和天问账号）。
2. **第一次**用 USB 线按普通方式下载。旧程序不认识重启命令，所以第一次只能走 USB。
3. 拔掉 ASR 的 USB，按接线说明接好 ESP32。之后在天问里选“ASR 无线烧录 (COMn)”就能无线下载。

把重启命令加进你自己的工程时，注意两点：

- 回复 `<asr_isp:ok>` 要在播报和重启**之前**，ESP32 只等 300 毫秒。
- 播报加重启的总时间不要超过约 7 秒。ESP32 示例只等 8 秒去接 bootloader，超时后需要你手动给 ASR 重新上电。
