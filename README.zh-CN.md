# ASR 无线烧录助手 · Serial Network Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

不插 USB，在电脑上用官方软件（天问Block）点“下载”，就能经 Wi-Fi + ESP32 给 ASR PRO 语音模块烧录固件。

```text
天问Block ── 选择 “ASR 无线烧录 (COMn)” ──▶ 本程序 ── Wi-Fi ──▶ ESP32 ──▶ ASR PRO 下载口
```

**状态：`0.4.0`。** 已在 Windows 11 + ESP32-S3 + ASRPRO-4M 上实测：完整固件 1.7 MB 无线烧录约 50 秒，连续多次成功。详见[验证矩阵](docs/verification.zh-CN.md)。

## 三步上手

1. **接线**：ESP32 GPIO9→ASR PB6、GPIO10←PB5（下载口），GPIO2→PA6、GPIO1←PA5（业务串口），共地。烧录时拔掉 ASR 的 USB。详见[接线说明](docs/wiring.zh-CN.md)。
2. **装软件**：
   - ESP32 刷入 [`arduino/examples/AsrWirelessFlash`](arduino/examples/AsrWirelessFlash/AsrWirelessFlash.ino)（改 Wi-Fi 名称密码）。
   - ASR 用 USB 先烧一次带重启命令的程序：[最简工程](examples/asrpro/asr_isp_minimal.hd)，或把[代码片段](examples/asrpro/asr_isp_snippet.cpp)加进你的工程。
   - 电脑安装 [com0com 签名版](https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/)（`com0com-3.0.0.0-i386-and-x64-signed.zip`）。
3. **烧录**：从 [Releases](https://github.com/youli-naixu/serial-network-gateway/releases) 下载并打开 `ASR 无线烧录助手`。第一次按提示填 ESP32 地址、点“准备虚拟串口”（弹一次管理员确认）。之后在天问Block 里选 **ASR 无线烧录 (COMn)**，点下载。

程序界面只有一页：大状态灯、要选的串口名、准备情况清单（哪一项没就绪就标红并给出修复按钮）和烧录记录。关掉窗口会缩到右下角托盘继续待命；开机自动运行默认关闭，可在“设置”里打开。

## 原理

ASR PRO 上电后约 850 毫秒内，下载口收到同步字节和握手包才会进入下载模式。官方 USB 板靠 DTR/RTS 控制三极管断电重启来制造这个时机；Wi-Fi 上没有这两根线，所以：

1. 烧录软件打开虚拟串口时，本程序请求 ESP32 进入下载模式；
2. ESP32 经业务串口发 `<asr_isp>`，ASR 程序回复后软件复位；ESP32 立即在下载口接住 bootloader（实测约 135 毫秒）；
3. bootloader 被接住后会一直等待，本程序把官方软件的数据、波特率切换（115200→750000→1500000）经 RFC2217 转发给 ESP32。

电脑端用 [com0com](docs/wiring.zh-CN.md) 造一对虚拟串口：一头给天问Block，另一头由本程序接走。下载协议的细节（帧格式、CRC、两阶段流程）见 [docs/asrpro-download-protocol.zh-CN.md](docs/asrpro-download-protocol.zh-CN.md)。

## 高级工具

主窗口的“高级工具”打开原来的通用功能，本项目也能给其他需要“网络串口”的场景用：

| 页签 | 作用 |
| --- | --- |
| 网关 | 配置档、原始 TCP 或 RFC2217、超时和缓冲区；双向字节计数和事件记录。 |
| 虚拟串口 | 检测 HW VSP3 Single 与 com0com，新建、删除 com0com 端口对；com0com 桥接到任意 RFC2217 端点。 |
| ESP32 设备 | TCP 连通性、可选波特率探测、设备 HTTP 状态、局域网扫描。 |
| 诊断 | 本机环境检查、离线自检、导出脱敏诊断。 |

**ESP32 库（`arduino/`，Arduino-ESP32 2.x）**：`SerialNetworkGateway` 给现有固件加“维护模式”，开启期间接管一路 UART 经 RFC2217 转发，结束后交还原业务。`firmware/` 保留一份独立的 ESP-IDF 固件作为备选。

## 选哪个虚拟 COM 驱动

本项目不附带内核驱动，只管理你自行安装的驱动：

| 在 Windows 11 23H2 实测 | HW VSP3 Single 3.1.2 | com0com 3.0.0.0（已签名）+ 本项目桥接 |
| --- | --- | --- |
| 二进制数据、打开时的参数 | 支持 | 支持 |
| 打开后修改波特率 | 仅 57600 及以下 | 支持，已测 1.5 Mbaud |
| 修改数据位 / 校验 / 停止位 | 只有同时改波特率才下发 | 支持 |
| DTR / RTS / BREAK | 不支持 | 支持 |
| 应用关闭串口时结束会话 | 支持 | 支持（关闭检测） |

目标板需要复位线或会话中途切换波特率时（大多数 Bootloader 都需要），用 **com0com**。只需固定速率传数据时，HW VSP 更简单。

HW VSP 还会把服务器主动发出的 Telnet 选项字节当成串口数据交给应用，所以网关默认使用“安静模式”，只回应客户端的请求。

## 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m serial_gateway            # ASR 无线烧录助手
.\.venv\Scripts\python.exe -m serial_gateway gui        # 高级工具
.\.venv\Scripts\python.exe -m serial_gateway selftest
```

用 com0com 做不接硬件的回环测试：

1. 在高级工具 **虚拟串口** 页点击“新建端口对…”（例如 `COM11`），在弹出的管理员确认中点“是”。
2. 在 **网关** 页保持“本机模拟”，点击“启动网关”。
3. 在 **虚拟串口** 页下方的 com0com 桥接里选择端口对，点击“启动桥接”，目标默认是 `127.0.0.1:7000`。
4. 用任意串口助手打开 `COM11`：发出的数据会原样返回，网关会显示你设置的串口参数和 DTR/RTS。

连接真实设备时，把桥接目标改成 ESP32 的地址和端口（默认 `3333`）。

## 命令行

```powershell
python -m serial_gateway --help
python -m serial_gateway station config --host 192.168.50.20
python -m serial_gateway station prepare --yes
python -m serial_gateway station run
python -m serial_gateway vcom status
python -m serial_gateway vcom create-pair --com COM11 --yes
python -m serial_gateway bridge --endpoint CNCB0 --host 127.0.0.1 --port 7000
python -m serial_gateway device check --host 192.168.50.20 --token-env GATEWAY_TOKEN
python -m serial_gateway device scan
python -m serial_gateway serve --simulate --port 7000
```

会修改系统的命令必须加 `--yes`。令牌只能通过 `--token-env` 指定的环境变量读取。

## 安全提示

- 只能使用 3.3 V 电平。目标板的 RX 线上只能有一个发送端；接 ESP32 TX 之前，先断开或隔离板载 USB 转串口芯片。
- RFC2217 没有鉴权。ESP32 要放在可信网络中，并设置来源 IP 白名单。
- 导出诊断时不包含令牌、数据内容和安装路径。

## 文档

| 文档 | 内容 |
| --- | --- |
| [接线说明](docs/wiring.zh-CN.md) | ESP32 与 ASR PRO 怎么接、接错时的现象。 |
| [示例代码](examples/README.zh-CN.md) | ESP32 示例、ASR 代码片段与最简工程。 |
| [ASR 下载协议](docs/asrpro-download-protocol.zh-CN.md) | 官方下载工具的帧格式和流程（抓包整理）。 |
| [Windows 虚拟 COM](docs/windows-virtual-com.zh-CN.md) | 驱动安装、实测行为、关闭检测。 |
| [远端接口约定](docs/remote-contract.zh-CN.md) | 网络端需要实现的内容。 |
| [电脑端模拟](docs/simulation.zh-CN.md) | 不接硬件的可复现测试。 |
| [ESP32 库](arduino/README.zh-CN.md) | 接线、API、嵌入现有固件。 |
| [ESP-IDF 固件](docs/firmware.zh-CN.md) | 备选的独立固件。 |
| [硬件验收](docs/hardware-validation.zh-CN.md) | 连接真实目标板之前的步骤。 |
| [验证矩阵](docs/verification.zh-CN.md) | 哪些已测试、哪些尚未测试。 |

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q -W error::ResourceWarning
python tools/check_public_tree.py
```

另见[贡献指南](CONTRIBUTING.zh-CN.md)、[安全说明](SECURITY.zh-CN.md)和[更新记录](CHANGELOG.md)。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。`arduino/src/rfc2217/` 和 `firmware/` 中的 RFC2217 解析器来自 [igrr/rfc2217-server](https://github.com/igrr/rfc2217-server)（Apache-2.0），本地修改列在源码中。pySerial 采用 BSD-3-Clause。HW VSP3 和 com0com 是独立产品，本项目不附带。
