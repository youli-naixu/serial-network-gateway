# 可复现 PC 模拟

[English](simulation.md) | [简体中文](simulation.zh-CN.md) · [README](../README.zh-CN.md)

## 范围与前置条件

本流程使用回环 Socket 和内置回显模拟器，不安装驱动、不创建 COM 设备、不连接 UART 网关，也不模拟目标 Bootloader。结果只属于[验证矩阵](verification.zh-CN.md)中的 PC 模拟条目。

使用干净的源码副本、Windows、Python 3.11+，GUI 需要 Tk；在仓库根目录打开两个终端。当前 alpha 的 Python 版本矩阵尚待验证，先前本机原型使用 Python 3.14。先安装依赖，随后模拟无需外部网络端点：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

每个终端明确使用虚拟环境内的解释器，或按日常 Shell 操作激活环境。下文的 `python` 均指该解释器。

## 自动自检

```powershell
python -m serial_gateway --version
python -m pytest -q
python -m serial_gateway selftest --bytes 65536 --output artifacts/selftest.json
```

验收条件为测试通过、自检退出码 `0`、`"ok": true`，且 `checks` 中每项均通过。记录实际测试数量，不要将原型的数量复制成新版本结果。自检使用临时回环端口，不依赖已保存的真实设备配置。

已实现的自检覆盖 RFC2217 协商、模拟串口和控制变化、包含 `0xFF` 的二进制回显、有效载荷计数、应用队列上限、第二客户端拒绝、断线隔离、显式重新就绪和新会话标记。模拟控制不能验证任何电气输出或波特率精度。测试套件另外覆盖传输与配置；描述范围时应检查当前套件。

完整 JSON 留在本地。公开证据只保留脱敏结果、源码修订、工具版本、范围和通过/失败摘要。

## 确定性配置档

将以下内容保存为工作副本中的 `local-profiles.json`。后续如填入真实地址或备注，应保留为本地文件。这些配置名不依赖默认 GUI 语言。

```json
{
  "schema_version": 1,
  "selected": "simulation-rfc2217",
  "profiles": [
    {
      "name": "simulation-rfc2217",
      "mode": "simulator",
      "protocol": "rfc2217",
      "listen_host": "127.0.0.1",
      "listen_port": 7000
    },
    {
      "name": "simulation-raw",
      "mode": "simulator",
      "protocol": "raw",
      "listen_host": "127.0.0.1",
      "listen_port": 7001
    }
  ]
}
```

省略的字段采用模型默认值。`--config` 放在子命令之前：

```powershell
python -m serial_gateway --config local-profiles.json profiles
```

## RFC2217 手动回显

在终端 A 启动模拟器，等待状态为 `ready`：

```powershell
python -m serial_gateway --config local-profiles.json serve --profile simulation-rfc2217 --simulate --port 7000 --duration 120 --output artifacts/rfc2217-session.json
```

在终端 B 运行下面的 PowerShell here-string。它打开 RFC2217 URL，不打开 Windows COM 设备：

```powershell
@'
import serial

payload = bytes(range(256)) * 16
with serial.serial_for_url("rfc2217://127.0.0.1:7000", baudrate=115200, timeout=5) as port:
    port.baudrate = 57600
    port.bytesize = 8
    port.parity = "N"
    port.stopbits = 1
    port.write(payload)
    received = bytearray()
    while len(received) < len(payload):
        block = port.read(len(payload) - len(received))
        if not block:
            raise TimeoutError("Incomplete RFC2217 echo")
        received.extend(block)
    assert bytes(received) == payload, "RFC2217 payload mismatch"
print("RFC2217 echo passed: 4096 bytes")
'@ | python -
```

以逐字节一致和客户端成功信息为通过条件。客户端关闭后，终端 A 应结束会话，不自动接受下一个连接。保存的最终快照应描述 `needs_reset`；`serial_confirmed` 表示协议回复，不是硬件实测参数。

## raw TCP 手动回显

在终端 A 启动独立 raw 模式会话：

```powershell
python -m serial_gateway --config local-profiles.json serve --profile simulation-raw --simulate --port 7001 --duration 120 --output artifacts/raw-session.json
```

在终端 B 运行：

```powershell
@'
import socket

payload = bytes(range(256)) * 16
with socket.create_connection(("127.0.0.1", 7001), timeout=5) as connection:
    connection.settimeout(5)
    connection.sendall(payload)
    received = bytearray()
    while len(received) < len(payload):
        block = connection.recv(len(payload) - len(received))
        if not block:
            raise ConnectionError("Incomplete raw TCP echo")
        received.extend(block)
    assert bytes(received) == payload, "Raw TCP payload mismatch"
print("Raw TCP echo passed: 4096 bytes")
'@ | python -
```

raw 模式不协商串口参数。串口设置显示为空不等于传输失败。

## GUI 与会话检查

```powershell
python -m serial_gateway --config local-profiles.json gui
```

选择模拟配置并启动，再运行上面匹配的客户端。检查计数增长、界面保持响应，停止或断线不会静默续传。关闭客户端，显式重新就绪，再启动新客户端检查新会话。CLI 在会话结束后退出；应重新运行 `serve`，没有独立的 CLI 重新就绪子命令。

英文支持集成后检查两种语言，包括配置编辑、诊断、错误、缩放和退出。在此之前，英文 GUI 检查记为待验证。自检操作始终使用独立模拟，不跟随 GUI 的真实设备目标。

## 失败判读

| 现象 | 下一步检查 |
| --- | --- |
| 导入错误 | 确认终端使用安装了可编辑项目和 pySerial 的解释器。 |
| 监听无法启动 | 检查端口是否空闲，同时修改服务端和客户端端口。`--port 0` 适合自动测试，但需读取实际分配端口。 |
| RFC2217 协商超时 | 确认两端均使用 RFC2217、服务已就绪，且没有旧客户端占用会话。 |
| 客户端关闭后连接被拒绝 | CLI 启动新会话；GUI 则先关闭驱动旧 COM 会话再显式重新就绪。 |
| 回显或队列检查失败 | 保留脱敏失败详情和准确修订，不通过丢字节或启用重放绕过检查。 |
| TCP 测试通过但串口应用找不到 COM | 按[驱动指南](windows-virtual-com.zh-CN.md)安装并验证独立虚拟 COM 产品。 |

模拟通过是驱动和硬件工作前置条件，不能替代对应验收门禁。
