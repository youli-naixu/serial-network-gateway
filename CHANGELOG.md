# Changelog / 更新记录

## 0.3.0-alpha.1 — unreleased / 未发布

### Added / 新增

- Virtual COM management: detect HW VSP3 Single and com0com, configure the HW VSP channel, create/remove com0com pairs and enable close detection, each through one Windows administrator prompt. / 虚拟 COM 管理：检测 HW VSP3 Single 与 com0com，配置 HW VSP 通道，新建、删除 com0com 端口对并启用关闭检测，每项操作一次管理员确认。
- com0com bridge: forwards baud/format changes made after open, DTR, RTS and BREAK to an RFC2217 endpoint; ends the session when the application closes its port. / com0com 桥接：把打开后的波特率与格式变化、DTR、RTS、BREAK 转发到 RFC2217 端点；应用关闭串口时结束会话。
- ESP32 device checks: TCP reachability, optional RFC2217 baud probe, HTTP status, LAN scan. / ESP32 设备检测：TCP 连通、可选波特率探测、HTTP 状态、局域网扫描。
- Arduino library `SerialNetworkGateway` (maintenance mode for existing firmware) with standalone and embedded examples. / Arduino 库 `SerialNetworkGateway`（给现有固件加维护模式），附独立与嵌入示例。
- Quiet RFC2217 mode on the simulator and the ESP32 library (no unsolicited negotiation or notifications). / 模拟器与 ESP32 库的安静模式（不主动发送协商或通知）。
- ESP32 library: BREAK support; DTR/RTS requests for unconfigured pins are answered truthfully instead of closing the session. / ESP32 库：支持 BREAK；未配置引脚的 DTR/RTS 请求如实回复，不再断开会话。

### Fixed / 修复

- Bridge: opening the hidden com0com end at Windows' default 7 data bits silently cleared bit 7 of every byte; it is now forced to 8N1 binary. / 桥接：隐藏端按 Windows 默认 7 位数据位打开，会清掉每个字节的最高位；现强制 8N1 二进制。
- Bridge: a normal application close now ends with FIN instead of RST. / 桥接：应用正常关闭时以 FIN 结束而不是 RST。
- GUI: tab scroll region now shrinks after re-layout instead of leaving blank space. / 界面：页签滚动区域在重新布局后会正确缩小，不再留出空白。

### Verified / 已验证

- 207 Python tests; real COM10 (HW VSP) and COM11 (com0com) end-to-end on Windows 11; both ESP32 builds compile. / 207 项 Python 测试；Windows 11 上真实 COM10（HW VSP）与 COM11（com0com）端到端；两种 ESP32 构建均可编译。

### Not verified / 未验证

- ESP32 on hardware, electrical wiring, reset/boot timing, and real target programming. / ESP32 实物运行、电气接线、复位/启动时序与真实目标烧录。

## 0.2.0-alpha.1 — unreleased / 未发布

### Added / 新增

- Bilingual desktop UI and command-line help. / 中英双语桌面界面和命令行帮助。
- Explicit virtual COM acceptance command for verified virtual ports only. / 仅针对已核验虚拟端口的显式COM验收命令。
- HW VSP3 channel preview without credentials. / 不含凭据的HW VSP3单口配置预览。
- Standalone ESP32-S3 firmware based on `igrr/rfc2217-server`. / 基于`igrr/rfc2217-server`的独立ESP32-S3固件。
- Bilingual verification, hardware, security and contribution guides. / 双语验证、硬件、安全与贡献文档。

### Verified / 已验证

- 135 local Python tests and English self-test. / 本机135项Python测试及英文自检。
- Default and compile-check ESP32-S3 firmware builds. / ESP32-S3默认与完整链接检查固件构建。

### Not verified / 未验证

- Real Windows virtual COM traffic, ESP32 hardware runtime and ASR programming. / 真实Windows虚拟COM流量、ESP32实物运行及ASR烧录。
