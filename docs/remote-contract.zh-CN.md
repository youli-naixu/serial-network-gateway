# 远端接口约定

[English](remote-contract.md) | [简体中文](remote-contract.zh-CN.md) · [README](../README.zh-CN.md)

## 角色与协议选择

PC 网关接受一个本机客户端，连接一个已配置的远端 TCP 端点。设备模式原样转发网络流，不实现目标专用烧录协议、不创建 Windows COM 设备，也不转换 raw TCP 与 RFC2217。

| 配置协议 | 本机客户端要求 | 远端端点要求 |
| --- | --- | --- |
| `raw` | raw TCP 字节流 | raw 字节流，UART 参数在传输之外预先配置。 |
| `rfc2217` | Telnet COM-port/RFC2217 客户端 | 能协商并实现所需 UART 设置的 RFC2217 服务端。 |

远端也可采用拟开发 ESP32-S3 固件以外的兼容实现。必须针对所需功能提供兼容证据；TCP 端口可连接并不足够。

## 二进制传输与 UART 设置

raw TCP 必须保留所有字节值并正确处理任意读写分片。TCP 分块不是应用数据包边界。raw 模式下，网关无法从有效载荷推断波特率、校验、停止位、调制解调器控制或启动时序。

RFC2217 扩展 Telnet。端点须协商所需选项、处理跨 TCP 读取拆分的命令，并正确转义字面 IAC（`0xFF`）。网关观察 RFC2217 请求和回复以生成统计，同时转发原始字节，不修复任一端不支持或错误的协商。

必须区分以下证据：

- `serial_requested`：观察到的本机客户端串口/控制请求。
- `serial_confirmed`：观察到的远端回复；模拟模式下是模拟回复。
- 物理测量：在真实板卡测得的 UART 位时序或控制引脚波形。
- 目标成功：应用和目标确认预期操作成功，支持时包含校验/回读。

这些层级不能互相替代。固件实现必须说明支持的波特率范围、数据位、校验、停止位、流控、DTR/RTS 映射、BREAK 行为及调制解调器/线路状态处理。不支持的功能不能因为模拟器接受了请求就宣称硬件已实现。

不预设通用的 DTR/RTS 到复位或启动引脚映射。目标下载模式、极性、电平转换和脉宽需要板级设计及[硬件验证](hardware-validation.zh-CN.md)。

## 会话生命周期

PC 生命周期为 `ready` → `connecting` → `active`，会话结束后进入 `needs_reset`。启动失败也可能报告 `error`。拒绝第二个并发客户端。断线、发送停滞或传输错误会关闭两个方向并清理当前会话的应用队列。

GUI 中先关闭客户端 COM 和重定向器会话，再显式重新就绪。CLI 的 `serve` 在会话结束后退出，必须重新启动。新会话不得收到旧待发数据。自动重放部分固件镜像或命令流不属于此约定。

远端实现应在释放旧 UART/网络会话后才接受新连接，说明如何处理 UART 缓存，并防止旧会话数据泄漏到新会话。远端自身的客户端限制和清理行为须单独测试，不能从 PC 网关行为推断。

## 缓冲与诊断

网关对两个方向使用独立有界应用队列并施加背压。发送停滞超时会结束会话，不会静默丢字节后继续。配置的缓冲上限只针对这些队列，不代表进程总内存、TCP 内核缓冲、重定向器或 UART 硬件的上限。

TCP 计数包含传输协议字节；有效载荷计数通过观察扣除已识别的 RFC2217/Telnet 开销。两种计数都不能证明目标已消费或烧录数据。导出的状态可能包含端点/配置信息、错误，以及其他诊断操作获得的设备元信息；公开前必须脱敏。

## 设备配置示例

以下仅为文档示例。`192.0.2.10` 是保留的文档地址，不是真实可用服务。仅在验证远端实现和网络可达后，在本地替换。

```json
{
  "schema_version": 1,
  "selected": "uart-lab",
  "profiles": [
    {
      "name": "uart-lab",
      "mode": "device",
      "protocol": "rfc2217",
      "listen_host": "127.0.0.1",
      "listen_port": 7000,
      "remote_host": "192.0.2.10",
      "remote_port": 2217
    }
  ]
}
```

这里 `2217` 是示例端口，不代表固件默认值。保存并核对本地配置，只有准备进入设备门禁时才运行 `python -m serial_gateway --config local-device.json serve --profile uart-lab`。PC 检查期间可用 `--simulate` 将目标覆盖为本机模拟器。

## 网络边界

PC 入口限制在回环地址。raw TCP 和 RFC2217 自身不提供身份认证或加密，本项目不为所转发协议补充这些保护。通过可信网络边界或独立配置的已认证隧道限制远端访问。不要将未认证的 UART 端点暴露到公网。

报告与数据处理说明见[安全政策](../SECURITY.zh-CN.md)。连上 Wi-Fi 不等于 RFC2217 客户端已认证，回环监听也不阻止其他本机进程连接。

## 协议参考

- [RFC 2217：Telnet Com Port Control Option](https://www.rfc-editor.org/rfc/rfc2217)
- [pySerial RFC2217 支持](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#rfc2217-network-ports)
- [igrr ESP-IDF RFC2217 服务端](https://github.com/igrr/rfc2217-server)
