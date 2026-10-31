# Source and patch / 来源与补丁

Vendored component / 内置组件：**igrr/rfc2217-server**

- Repository: https://github.com/igrr/rfc2217-server
- Fixed commit: `281e424d4bca6c85fb8e4d727dd1fa0c315b95f1`
- License: Apache-2.0, unmodified license in `components/rfc2217_server/LICENSE.txt`.
- Imported files: `include/rfc2217_server.h`, `src/rfc2217_server.c`, `CMakeLists.txt`, `idf_component.yml`, `LICENSE.txt`.
- Upstream component requires ESP-IDF >=5.1. This project is compiled with 5.4.

The application preserves upstream's Telnet state machine, option negotiation and IAC framing. It does not reproduce the UART esptool target-reset example or infer ASR wiring from it.
本项目保留上游 Telnet 状态机、选项协商与 IAC framing；不照搬 uart_esptool 的 ESP 目标复位示例，不据此推断 ASR 接线。

Local modifications / 本地修改：

1. Add external transport callbacks (`on_write`, `on_command`, `on_protocol_error`) and begin/end/feed/reply APIs. Commands are dispatched to an application queue; application replies only after real driver operations. External `start/stop` are rejected. The original blocking socket/pthread path is unused, not claimed fixed.
   添加外部传输/命令回调，交给应用四任务管理；仅实际操作成功才回复。上游阻塞式 socket/pthread 路径不使用，不声称已修复其 stop。
2. External `send_data` uses bounded stack framing instead of the upstream send mutex and unbounded allocation. Application serializes entire frames through a queue; parser callbacks do not wait for socket writes.
   外部发送用有界 framing 与应用队列，回调不等待 socket 锁。
3. Correct fragmented subnegotiation fast path: ordinary TCP fragments inside SB must not leak into UART payload. Fail closed on overlong/malformed SB; validate command lengths before dispatch. No silent overflow recovery into UART data.
   修复跨 TCP 分片子协商被误当串口数据的问题；超长/畸形包明确故障，不泄漏控制字节。
4. Disable unsolicited ECHO; send correct negotiation replies for BINARY/SGA. Reject unsupported Telnet control commands including BREAK. Initialize disconnected socket to -1; free option state and mutex on destroy.
   禁用 ECHO，补全 BINARY/SGA 应答，拒绝 BREAK 等未实现命令，清理组件状态。

Reviewed upstream issues / 已查看上游问题：

- #4: IAC escaping and bytewise reception (closed); #5: purge response (closed).
- #6: uart_esptool and related fixes (merged/closed); #7: slow baud negotiation (open at review).
- Source inspection additionally found blocking accept/recv/join in stop, send_data errors not propagated by the legacy path, and unsupported format requests acknowledged as success. These are why the external adapter is used; they are not presented as upstream issue reports.
  stop 阻塞、旧路径发送错误传播与格式假成功来自源码检查，并非声称已有对应 issue。

The public firmware remains experimental. Build success is not proof of electrical behavior, protocol interoperability or bounded hardware cutoff latency.
公开固件保持实验性，编译成功不等于电气、协议互操作或硬件截断时延验收。
