# Changelog / 更新记录

## 0.4.0 — 2026-11-04

### Added / 新增

- ASR Wireless Flash Assistant: the program opens a single-page window for beginners (status light, the port to choose, checklist with fix buttons, download history). Closing it keeps it in the tray; one copy at a time; run-at-sign-in in Settings, off by default. / ASR 无线烧录助手：打开程序即单页主窗口（状态灯、要选的串口、带修复按钮的准备清单、烧录记录）；关窗缩到托盘；只运行一份；“设置”里可开机自启，默认关闭。
- Plain-language error messages with what to do next (port busy, ESP32 unreachable, wiring problems). / 错误提示改成人话并给出下一步（串口被占用、连不上 ESP32、接线问题）。
- Wiring guide (docs/wiring*.md, also in the app) and ASR download protocol notes (docs/asrpro-download-protocol*.md). / 接线说明（docs/wiring*.md，程序内也有）与 ASR 下载协议整理（docs/asrpro-download-protocol*.md）。
- Examples: ESP32 sketch `arduino/examples/AsrWirelessFlash` (built in CI), ASR snippet, and a minimal TianwenBlock project that speaks a prompt before restarting. / 示例：ESP32 程序 `arduino/examples/AsrWirelessFlash`（CI 编译）、ASR 代码片段、先播报再重启的最简天问工程。

### Changed / 变更

- The former six tabs moved to an "Advanced tools" window with four tabs: Gateway, Virtual COM, ESP32 device, Diagnostics. / 原来的六个页签移到“高级工具”窗口，合并为四个：网关、虚拟串口、ESP32 设备、诊断。
- Station: errors before any flashing tool opened the port are shown as station errors, not failed downloads; each download is recorded with result, size and duration. / 无线烧录：烧录软件打开串口前的错误显示为待命错误，不算一次失败烧录；每次烧录记录结果、大小和用时。
- ESP32: the bootloader catch window is 8 s, so an ASR program may speak before restarting. / ESP32：接住 bootloader 的时间窗口为 8 秒，ASR 程序可以先播报再重启。

### Verified / 已验证

- Five complete wireless downloads of an ASRPRO-4M (1,699,840 bytes, 42–55 s) through TianwenBlock's makebin.exe, including back-to-back sessions and one from the new window. 237 tests pass. / ASRPRO-4M 完整无线烧录 5 次（1,699,840 字节，42–55 秒），含连续会话和一次经新主窗口；237 项测试通过。

## 0.3.0-alpha.1 — 2026-11-03

### Added / 新增

- One-click wireless flashing tab and `station` CLI: a labelled virtual COM in the Windows Ports class (visible to Qt/pyserial pickers such as TianwenBlock), automatic "enter download mode" request before each session, re-arms after every download. Verified with two back-to-back ASR PRO downloads. / 一键无线烧录页签与 `station` 命令：在 Windows“端口”类中创建带备注的虚拟 COM（天问Block 等基于 Qt/pyserial 的软件可见），每次会话前自动请求进入下载模式，烧完自动等待下一次；已实测连续两次 ASR PRO 下载成功。
- ESP32 library: optional `sessionStartHook` for target preparation. Bridge CLI: `--wait-open`, `--repeat`, `--enter-url`, `--connect-retry`. / ESP32 库：可选 `sessionStartHook`；桥接命令新增 `--wait-open`、`--repeat`、`--enter-url`、`--connect-retry`。
- Virtual COM management: detect HW VSP3 Single and com0com, configure the HW VSP channel, create/remove com0com pairs and enable close detection, each through one Windows administrator prompt. / 虚拟 COM 管理：检测 HW VSP3 Single 与 com0com，配置 HW VSP 通道，新建、删除 com0com 端口对并启用关闭检测，每项操作一次管理员确认。
- com0com bridge: forwards baud/format changes made after open, DTR, RTS and BREAK to an RFC2217 endpoint; ends the session when the application closes its port. / com0com 桥接：把打开后的波特率与格式变化、DTR、RTS、BREAK 转发到 RFC2217 端点；应用关闭串口时结束会话。
- ESP32 device checks: TCP reachability, optional RFC2217 baud probe, HTTP status, LAN scan. / ESP32 设备检测：TCP 连通、可选波特率探测、HTTP 状态、局域网扫描。
- Arduino library `SerialNetworkGateway` (maintenance mode for existing firmware) with standalone and embedded examples. / Arduino 库 `SerialNetworkGateway`（给现有固件加维护模式），附独立与嵌入示例。
- Quiet RFC2217 mode on the simulator and the ESP32 library (no unsolicited negotiation or notifications). / 模拟器与 ESP32 库的安静模式（不主动发送协商或通知）。
- ESP32 library: BREAK support; DTR/RTS requests for unconfigured pins are answered truthfully instead of closing the session. / ESP32 库：支持 BREAK；未配置引脚的 DTR/RTS 请求如实回复，不再断开会话。

### Fixed / 修复

- Packaged builds: the elevated virtual-COM helper started the GUI instead of running its job; both launchers now route `--vcom-apply-job`. / 打包版：提权助手会启动界面而不是执行任务；两个启动器现在都会转交 `--vcom-apply-job`。
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
