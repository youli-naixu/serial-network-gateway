# 验证矩阵

[English](verification.md) | [简体中文](verification.zh-CN.md)

`0.4.0` 快照，环境为 Windows 11 23H2 x64、Python 3.14、ESP32-S3（N16R8）与 ASR PRO（ASRPRO-4M）。每行只写实际取得的证据，没有证据的项目保持“待验收”。

| 项目 | 状态 | 证据 |
| --- | --- | --- |
| Python 测试 | 通过 | 237 项通过（含需手动开启的界面冒烟测试）；ResourceWarning 视为错误。 |
| HW VSP3 Single 3.1.2 | 通过 | 由本程序配置通道（一次管理员确认）。真实 `COM10` → 网关：64 KiB 全 `00..FF` 往返一致；关闭串口后会话结束。 |
| HW VSP3 限制 | 已实测 | 不转发 DTR/RTS/BREAK/PURGE。打开后只下发 57600 及以下的波特率变化；数据位、校验、停止位只有同时改波特率才下发。服务器主动发出的 Telnet 选项会变成串口数据。 |
| com0com 3.0.0.0 签名驱动 | 通过 | 在默认 Windows 安全设置下安装，签名有效；端口对由本程序创建。 |
| com0com 桥接 | 通过 | `COM11` → 桥接 → 网关：打开后设置 1.5 Mbaud 与 7E2、切换 DTR/RTS、BREAK 开关，全部到达网关；64 KiB 和 16 KiB 二进制往返一致。 |
| 关闭检测 | 通过 | 配置 `ri=ropen` 后，关闭 `COM11` 会正常结束桥接会话；DTR 脉冲不会误判为关闭。 |
| 烧录助手 | 通过 | 单页主窗口中英文；放进 Windows“端口”类的 `ASR 无线烧录 (COM8)` 能被天问Block（Qt 串口列表）看到；准备清单、烧录记录、托盘、单实例、开机自启开关均已实际操作。 |
| 高级工具窗口 | 已预览 | 四个页签（网关、虚拟串口、ESP32 设备、诊断）中英文截图检查。 |
| ESP32 库 | 已编译 | Arduino-ESP32 2.0.17（espressif32 6.10.0）：三个示例（含 `AsrWirelessFlash`）均能编译。 |
| ESP-IDF 固件 | 已编译 | ESP-IDF 5.4.0：默认镜像和完整链接检查镜像均编译成功。 |
| ESP32 实物运行 | 通过 | 同一套“复位并接住”代码嵌入在 ESP32-S3 正式固件中：`<asr_isp>` 应答 22–282 毫秒，约 135 毫秒接住 bootloader。独立示例 `AsrWirelessFlash` 只做了编译。 |
| 电气 | 通过 | 按 docs/wiring.zh-CN.md 3.3V 直连。ASR 插着 USB 时板载 CH340K 也驱动 PB6，下载口无响应；拔掉 USB 即正常。 |
| 目标板烧录 | 通过 | 天问Block 的 makebin.exe 经带备注的虚拟串口、Wi-Fi、ESP32：1,699,840 字节 ASRPRO-4M 镜像完整写入并校验，用时 42–55 秒，共 5 次（含连续会话）；之后 ASR 业务通信正常。 |
| ASR 示例 | 部分 | `asr_isp_minimal.hd` 的 C++ 用天问Block 0.2.8 SDK 编译通过。语音提示需要天问在线“生成模型”，尚未烧录和试听。 |

TCP 或 RFC2217 应答只说明请求到达了协议层，不能证明引脚已经动作，也不能证明目标板 Flash 已写入。
