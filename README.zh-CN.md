# Serial Network Gateway 串口网络网关

[English](README.md) | [简体中文](README.zh-CN.md)

通过网络使用串口。Windows 程序打开一个普通的 `COMx` 口，数据、串口参数和控制线经 TCP/RFC2217 传到 ESP32，由 ESP32 驱动真实 UART。项目是通用的：起因是无线烧录一块语音模块，但数据通路里没有任何针对特定芯片的处理。

**状态：`0.3.0-alpha`。电脑端已在 Windows 11 上用真实虚拟 COM 驱动跑通端到端；ESP32 库已能编译，尚未在实物上运行。** 详见[验证矩阵](docs/verification.zh-CN.md)。

```text
应用程序（烧录工具、串口助手……）
   │ 打开 COM11
   ▼
com0com 端口对  COM11 ↔ CNCB0        （或 HW VSP3：COM10 → TCP）
   ▼
电脑端桥接：数据、串口参数、DTR/RTS、BREAK → RFC2217
   ▼ TCP
ESP32 网关库 ── UART ──▶ 目标板
```

## 功能

**桌面程序（Windows，Python/Tk，中英双语）**，一个窗口六个页签：

| 页签 | 作用 |
| --- | --- |
| 基本配置 / 高级参数 | 配置档（可导入导出）、原始 TCP 或 RFC2217、超时和缓冲区大小。 |
| 连接与统计 | 双向字节计数、队列峰值、请求与确认的串口参数、事件记录。 |
| 虚拟 COM | 检测 HW VSP3 Single 与 com0com，并列出各自能转发的内容；配置 HW VSP 通道，新建和删除 com0com 端口对。每次修改会弹出一次 Windows 管理员确认。 |
| com0com 桥接 | 把端口对的隐藏端接到 RFC2217 端点。打开串口**之后**再改波特率和格式也能转发，同时转发 DTR、RTS、BREAK。应用关闭串口时会话随之结束。 |
| ESP32 设备 | TCP 连通性、可选的 RFC2217 波特率探测、设备 HTTP 状态，以及局域网扫描。令牌只保存在内存里。 |

内置本机回显模拟器和自检，不接硬件也能测试整条链路。所有功能也能通过命令行使用。

**ESP32 库（`arduino/`，Arduino-ESP32 2.x）。** `SerialNetworkGateway` 给现有固件加上“维护模式”：开启期间接管一路 UART 并通过 RFC2217 转发，结束后把 UART 交还原业务。使用有界队列，出错时明确报告而不是丢数据；同一时间只接一个客户端，数据不跨会话；支持来源 IP 白名单，超时自动退出。可选的 RESET、BOOT 引脚由你自己的已鉴权代码触发。

`firmware/` 目录保留一份独立的 ESP-IDF 固件作为备选。

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

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m serial_gateway selftest
.\.venv\Scripts\python.exe -m serial_gateway gui
```

用 com0com 做不接硬件的回环测试：

1. 在 **虚拟 COM** 页点击“新建端口对…”（例如 `COM11`），在弹出的管理员确认中点“是”。
2. 在 **基本配置** 页保持“本机模拟”，点击“启动网关”。
3. 在 **com0com 桥接** 页选择端口对，点击“启动桥接”，目标默认是 `127.0.0.1:7000`。
4. 用任意串口助手打开 `COM11`：发出的数据会原样返回，网关会显示你设置的串口参数和 DTR/RTS。

连接真实设备时，把桥接目标改成 ESP32 的地址和端口（默认 `3333`）。

## 命令行

```powershell
python -m serial_gateway --help
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
