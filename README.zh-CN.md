# Serial Network Gateway

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Windows 应用和不同 UART 目标的通用串口网络工具。PC 网关提供回环 TCP 入口、raw TCP 或 RFC2217 透明转发、配置档、会话统计和本机回显模拟器。独立的 ESP32-S3 固件正在使用 ESP-IDF 和 `igrr/rfc2217-server` 实现。

**目标版本：`0.2.0-alpha.1`，本地验收待完成。** 先前的 `0.1.0` PC 原型曾有 99 项测试在本机通过。该历史结果不能验证当前修订、Windows 虚拟 COM 驱动、固件或真实目标。当前证据边界见[验证矩阵](docs/verification.zh-CN.md)。本项目尚不声称 ASR 开箱可烧录或硬件已通过测试。

## 工作方式

```text
串口应用
    |
    v
单独安装的虚拟 COM 驱动和重定向器
    |  raw TCP 或 RFC2217
    v
PC 网关 127.0.0.1:7000
    |
    +--> 内置本机回显模拟器
    |
    +--> 网络 UART 端点 --> UART 目标
         （ESP32-S3 固件开发中）
```

原生支持 TCP 或 RFC2217 的客户端可以直接连接，无需虚拟 COM 驱动。PC 网关自身不创建 Windows COM 设备。它转发所选协议，不负责将 raw TCP 转换成 RFC2217。

ASR PRO 是一个拟验证的目标实例。其他 UART 目标在协议、电气接口和控制要求匹配时也可以复用 PC 传输层。模拟器是回显端点，不是目标芯片的 Bootloader。

## 从源码快速开始

在仓库根目录运行以下命令。构建目标为 Windows 和 Python 3.11 或更高版本；先前本机原型使用 Python 3.14。当前 alpha 的逐版本验证尚待完成。GUI 需要 Tk 支持。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m serial_gateway selftest --output artifacts/selftest.json
.\.venv\Scripts\python.exe -m serial_gateway gui
```

自检成功时退出码为 `0`，报告中有 `"ok": true`。它创建临时回环端点，不读取真实设备配置、不打开 COM 设备。raw TCP 和 RFC2217 客户端示例见[可复现 PC 模拟指南](docs/simulation.zh-CN.md)。

先前 GUI 原型为中文。英文 GUI 支持正在开发；提供英文文档不代表所有界面文字已经翻译并检查。打开 GUI 不会启动连接。接入驱动或设备前先使用模拟器。

此源码快速开始不承诺已有可下载二进制、已签名安装器或已验证便携版。打包验收单独记录。

## 能力与边界

| 范围 | PC 实现及边界 |
| --- | --- |
| 传输 | raw TCP 和 RFC2217 转发；通过旁路观察 RFC2217 获取协议统计，不改写转发流。 |
| 本机监听 | 仅限回环地址。示例使用 `127.0.0.1`。单个活跃客户端，拒绝并发客户端。 |
| 缓冲 | 双向有界应用队列、背压、可配置块大小和超时。队列上限不代表所有操作系统或驱动缓冲都有同一上限。 |
| 会话 | 断线结束当前会话并丢弃应用待发队列。不自动重放或续传。 |
| 配置档 | JSON 配置档、GUI 导入导出、schema 校验、原子保存。 |
| 诊断 | 本机模拟、字节和队列计数、观察串口请求与回复、只读环境检查。 |
| 虚拟 COM | 需要外部产品。可选 hub4com 适配器使用已有 com0com 端口对，不安装驱动。 |
| 固件 | 独立 ESP-IDF ESP32-S3 实现正在开发；构建和硬件验收待完成。 |
| 目标烧录 | 取决于应用和目标。TCP 送达和 RFC2217 回复不能证明烧录成功或真实控制信号时序正确。 |

raw TCP 只传输字节，应提前在远端设置 UART 参数。RFC2217 的参数协商能力取决于驱动、远端实现和实际接线。模拟器接受 DTR、RTS、BREAK 或某个波特率，不代表硬件能复现这些设置。

## 命令行

激活虚拟环境，或明确使用虚拟环境内的 Python：

```powershell
python -m serial_gateway --help
python -m serial_gateway --version
python -m serial_gateway profiles
python -m serial_gateway environment
python -m serial_gateway selftest --bytes 65536 --output artifacts/selftest.json
python -m serial_gateway serve --simulate --port 7000
python -m serial_gateway driver-help --protocol rfc2217 --port 7000
```

`--config` 是全局选项，放在子命令之前。例如 `python -m serial_gateway --config local-profiles.json profiles` 读取[模拟指南](docs/simulation.zh-CN.md)中的配置。`serve --duration 30` 设置有界运行时间；`--port 0` 申请临时本机端口。

会话结束后，关闭应用的 COM 连接并清理重定向器的旧会话。GUI 中显式重新就绪；CLI 中重新启动 `serve` 进程。除了退出码，还应检查最终状态和错误字段。

`environment` 只读取设备元信息和已安装软件登记，不打开 COM。输出可能包含设备标识和本机路径，分享前需要脱敏。

## 文档

| 指南 | 内容 |
| --- | --- |
| [PC 模拟](docs/simulation.zh-CN.md) | 源码环境、确定性配置、二进制回显检查、预期结果。 |
| [Windows 虚拟 COM](docs/windows-virtual-com.zh-CN.md) | HW VSP3 和 com0com/hub4com 路线及驱动验收流程。 |
| [远端接口约定](docs/remote-contract.zh-CN.md) | raw/RFC2217 行为、控制参数、会话及网络边界。 |
| [固件构建](docs/firmware.zh-CN.md) | 独立 ESP-IDF 构建前置条件和证据门禁。 |
| [硬件验证](docs/hardware-validation.zh-CN.md) | ESP32-S3 电气检查、UART/控制验证、ASR 实例门禁。 |
| [验证矩阵](docs/verification.zh-CN.md) | 原型历史证据、当前待验收项、公开发布门禁。 |

每篇指南顶部均链接对应英文版本。

## 开发与发布

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m serial_gateway selftest --output artifacts/selftest.json
python -m serial_gateway gui
```

打包前检查两种 GUI 语言与显示缩放。记录准确源码修订、Windows/Python 版本、测试结果和产物校验值。二进制发布与公开上传须先完成所声明范围的本地验收；未完成的驱动或硬件条目必须保留待验证标记。另见[贡献指南](CONTRIBUTING.zh-CN.md)、[安全政策](SECURITY.zh-CN.md)和[变更日志](CHANGELOG.md)。

## 许可与第三方软件

本项目原创代码和文档使用 [Apache License 2.0](LICENSE)。署名和依赖边界见 [NOTICE](NOTICE)。pySerial 使用 BSD-3-Clause，`igrr/rfc2217-server` 组件使用 Apache-2.0。虚拟 COM 产品是独立获取的第三方工具，本项目不捆绑或重新许可这些产品。
