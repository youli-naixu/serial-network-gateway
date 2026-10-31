# Windows 虚拟 COM 接入

[English](windows-virtual-com.md) | [简体中文](windows-virtual-com.zh-CN.md) · [README](../README.zh-CN.md)

## 范围与当前状态

Serial Network Gateway 提供回环 TCP 入口。需要 Windows COM 设备的串口应用，还需要独立的虚拟 COM 驱动和重定向器。`0.2.0-alpha.1` 的驱动安装及端到端虚拟 COM 验收尚待完成。找到可执行文件或卸载登记不等于内核驱动已加载。

先完成 [PC 模拟](simulation.zh-CN.md)。驱动验收阶段只用模拟端点；接入真实硬件须满足独立的[硬件门禁](hardware-validation.zh-CN.md)。

## 选择路线

| 路线 | 结构 | 必须验证的内容 |
| --- | --- | --- |
| HW VSP3 | 应用 → 虚拟 COM → HW VSP3 TCP 客户端 → 网关 | 准确产品版本、许可、驱动签名/加载状态、Windows 兼容性、NVT 行为、串口与控制转发。 |
| com0com + hub4com | 应用 → 虚拟端口对一端 → hub4com 管理的另一端 → 网关 | 配对关系、对外 COM 名、签名驱动兼容性、hub4com 过滤器、串口与控制转发。 |
| 原生 RFC2217 客户端 | 应用 → RFC2217 URL → 网关 | 不创建 Windows 虚拟 COM；只适用于原生支持该协议的应用。 |

从官方渠道获取产品并核对条款。本项目不重新分发这些产品。安装可能需要管理员权限和重启，应作为单独明确的环境配置步骤。保持 Secure Boot、驱动签名强制检查和内存完整性/HVCI 的正常设置。如果现有驱动无法在这些设置下加载，将该路线标为阻塞并选择受支持产品。关闭防护不是本项目的前置条件。

## 只读发现

```powershell
python -m serial_gateway environment
python -m serial_gateway driver-help --protocol rfc2217 --port 7000
```

发现操作不打开 COM、不创建端口对。通过 Windows 设备管理器和厂商工具确认真实驱动状态。不要把硬件 ID、设备序列号、安装路径和原始发现输出写入公开报告。

## HW VSP3 设置

1. 按许可安装合适的官方版本。针对准确 Windows 构建和安全设置，核对厂商当前兼容性说明。旧版本文档不能证明当前兼容。
2. 按[模拟指南](simulation.zh-CN.md)启动 RFC2217 模拟配置，本机端口为 `7000`。
3. 在 HW VSP3 中选择空闲虚拟 COM 号，TCP 目标设为 `127.0.0.1`，端口为 `7000`。
4. RFC2217 模式使用厂商选项 **NVT Enable** 和 **Remote port setup**。核对已安装版本的手册；普通 Telnet/NVT 本身不保证远端 UART 配置。
5. 创建/打开虚拟端口，运行下文 COM 回显检查。在应用内改变波特率和线路设置，同时观察网关的请求与确认参数。
6. 核对重定向器的连接与重试行为。如果无法关闭自动重连，网关仍会在会话结束后拒绝连接，直到显式重新就绪。重新就绪前必须关闭应用并清理重定向器会话。

raw TCP 模式关闭 NVT/Telnet/RFC2217 处理，并使用 `7001` 端口的 raw 模拟配置。真实 raw 端点必须预先设置匹配的 UART 参数。raw 模式无法验证远端波特率或控制线转发。

## com0com 与 hub4com 设置

用厂商配置工具创建新的虚拟端口对，为应用选择空闲 COM 名。不要重命名或接管已有物理端口。hub4com 一端必须是已枚举的 `CNCBn`，并有对应的 `CNCAn`。例如 `CNCB0` 只适用于实际编号为 `0` 的端口对，不是通用默认值。按上游 RFC2217 配对说明配置，包括该版本要求的波特率仿真。

本项目外部适配器构建 hub4com 的 `escparse`、`pinmap`、`linectl` 和 Telnet COM-port 过滤器。它运行现有可执行文件，打开前核对端口对，将目标限制为 `127.0.0.1`，并请求 `--reconnect=no`。该集成仍须通过真实驱动验收。普通 pySerial 字节桥不能暴露虚拟端口另一端的全部 Windows DCB/IOCTL 变化。

在 PowerShell 中按提示填写真实安装路径和已枚举端点：

```powershell
$Setupc = Read-Host "Absolute path to installed setupc.exe"
$Hub4com = Read-Host "Absolute path to installed hub4com.exe"
$VirtualEndpoint = Read-Host "Enumerated virtual CNCBn endpoint"
python -m serial_gateway com-bridge --setupc "$Setupc" --hub4com "$Hub4com" --endpoint "$VirtualEndpoint" --port 7000
```

不带 `--run` 时只预览参数，不打开端口。预览成功不等于端口对枚举成功或驱动通过验收。在另一终端启动 RFC2217 模拟器后，再明确运行桥接：

```powershell
python -m serial_gateway com-bridge --setupc "$Setupc" --hub4com "$Hub4com" --endpoint "$VirtualEndpoint" --port 7000 --run
```

客户端使用应用 COM 端点期间保持桥接运行，检查后用 Ctrl+C 停止。只关闭应用可能不会终止 hub4com 网络连接；检查会话隔离时应主动停止桥接或断开连接。此适配器面向 RFC2217，不是 raw 适配器，也不安装驱动。

## COM 回显验收

仅使用新建的虚拟应用端点。先在厂商工具中确认身份；打开物理串口可能改变控制线。在单独终端运行：

```powershell
$TestCom = Read-Host "Confirmed virtual application COM name"
@'
import sys
import serial

payload = bytes(range(256)) * 16
with serial.Serial(sys.argv[1], baudrate=115200, timeout=5, write_timeout=5) as port:
    port.write(payload)
    received = bytearray()
    while len(received) < len(payload):
        block = port.read(len(payload) - len(received))
        if not block:
            raise TimeoutError("Incomplete virtual COM echo")
        received.extend(block)
    assert bytes(received) == payload, "Virtual COM payload mismatch"
print("Virtual COM echo passed: 4096 bytes")
'@ | python - "$TestCom"
```

这只证明一种配置下的二进制回显。以下验收用例需分别完成和记录：

| 用例 | 所需证据 |
| --- | --- |
| 驱动加载与枚举 | 驱动版本、发布者/签名状态、Windows 构建、正常安全设置，以及 COM 成功枚举和打开。 |
| 二进制完整性 | 回显逐字节相等，包含 `0x00`、`0xFF`、`0x11`、`0x13`，不存在隐式文本转换。 |
| 串口配置 | 改变波特率、数据位、校验和停止位，对比应用请求与模拟器回复；重复检查目标所需设置。 |
| 控制信号 | 分别改变 DTR、RTS、BREAK，检查哪些请求到达模拟器；记录不支持或被重映射的信号。 |
| 单会话行为 | 拒绝第二网关客户端；主动结束重定向器连接；确认网关需要重新就绪。 |
| 新会话 | 关闭应用与重定向器，重新就绪/重启，再连接并交换不同标记，确认没有旧字节。 |
| 目标应用 | 确认实际串口应用可枚举和打开 COM；烧录验收另需真实硬件。 |

驱动可能缓存数据、仿真波特率、映射握手信号，或在 COM 关闭后仍保持 Socket。应观察这些行为，不要假设关闭 COM 等于网络断线。在依赖控制线进行复位或进入下载模式前，还要在真实引脚上重新验证。

## 证据与恢复

将脱敏产品/版本信息和逐项结果记录到[验证矩阵](verification.zh-CN.md)。不能只凭软件已安装或参数预览成功发布兼容性标记。

会话失败时，停止应用、停止重定向器并结束网关会话。解决报告的问题后开启新会话，不重放只传输了一部分的烧录流。清理时使用厂商正常卸载/移除端口对流程；网关不管理内核驱动卸载。

## 官方参考

- [HW group：HW VSP3](https://www.hw-group.com/software/hw-vsp3-virtual-serial-port)：获取当前产品和手册；可用性和支持的 Windows 版本可能变化。
- [com0com 项目](https://com0com.sourceforge.net/)：虚拟串口对和相关工具。
- [hub4com README](https://com0com.sourceforge.net/hub4com/ReadMe.txt)：上游 RFC2217 客户端/端口对示例。
- [pySerial RFC2217 文档](https://pyserial.readthedocs.io/en/latest/url_handlers.html#rfc2217)：直接协议客户端与限制。
