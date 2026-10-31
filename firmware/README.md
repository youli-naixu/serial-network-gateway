# ESP32-S3 RFC2217 gateway / 串口网络网关

**Experimental: compile-verified, no hardware tests. 实验性：实际编译通过，未上板测试。**

Independent ESP-IDF project. No OTA, HTTP API or target reset recipes. This task did not flash, open a serial port or control a network device.
独立 ESP-IDF 工程，不含 OTA、HTTP API 或目标复位时序。本次不刷写、不打开串口、不控制联网设备。

## Build / 构建

Install Python and `platformio==6.1.19`, then from the repository root:
安装 Python 与 `platformio==6.1.19` 后，在项目根目录运行：

```sh
python firmware/scripts/build.py
python firmware/scripts/build.py --check-enabled
```

All tool packages stay under `firmware/.pio-core`, isolated from existing Arduino projects. Fixed versions: `espressif32@6.10.0`, ESP-IDF 5.4.0, Xtensa GCC 14.2.0+20241119. The upstream library declares IDF >=5.1; this firmware uses 5.4 driver components. The build script sets `GIT_CEILING_DIRECTORIES` to prevent a parent's unborn Git HEAD breaking the packaged IDF. It never invokes upload or monitor.
工具链全部在固件自己的 `.pio-core` 内，与现有 Arduino 工程隔离。上游组件要求 IDF>=5.1，本固件使用 IDF5.4 驱动组件。脚本限制 Git 父目录探测，并且只编译，从不上传或监视串口。

Output / 产物：`.pio/build/<environment>/{firmware.bin,firmware.elf,bootloader.bin,partitions.bin}`. The build script also copies these to ignored `artifacts/<environment>/` so switching environments cannot remove the prior verification artifacts. 构建脚本同时复制到被忽略的 `artifacts/<environment>/`，避免切换环境清掉上一次验证产物。
`esp32s3_compilecheck` is a build fixture with synthetic pins, not a wiring recommendation; see the script for its enforced hardware-disabled gate.
`esp32s3_compilecheck` 是使用假定引脚的编译检查配置，不是接线推荐；脚本设置强制禁止硬件运行的门禁。

## Defaults and wiring / 默认状态与接线

- UART is off until `GW_UART_PINS_CONFIRMED=y` and valid distinct TX/RX pins are configured. All pins default to `-1`; optional DTR/RTS have independent pins and polarity.
  UART 须显式确认接线及有效 TX/RX 引脚才启用，所有引脚默认 `-1`；DTR/RTS 独立映射与极性配置。
- Wi-Fi is off by default; missing/invalid credentials fail safe. No AP fallback. Use menuconfig and ignored `sdkconfig.esp32s3`; never commit credentials, local configuration or build outputs. Wi-Fi credentials use RAM storage and `WIFI_PS_NONE`.
  Wi-Fi 默认关闭；缺少凭据时停用，无 AP 回退。仅在被忽略的本地配置中保存凭据，禁止入库。
- Generic S3 profile: 4 MB flash, DIO 40 MHz, no PSRAM dependency. It fits an N16R8 module without claiming to use all memory. USB Serial/JTAG console is separate from UART1; protected USB/console/strap/flash/OPI pins are rejected.
  通用 S3 配置使用 4MB flash、不依赖 PSRAM，可适配 N16R8 容量；日志与 UART1 分离，拒绝受保护引脚。
- **3.3 V logic only.** Isolate the CH340/CH343 TX before connecting gateway TX to target RX. Never join two push-pull TX outputs; share ground and verify power separately.
  **仅限 3.3V 逻辑。** 必须隔离 CH340/CH343 TX，禁止双 TX 并联，共地并核对供电。
- ASR `PG_EN`, reset voltage/polarity/timing are unconfirmed. No automatic ASR/ESP target reset sequence is included. Initial 115200 baud and all GPIO choices are generic, not verified ASR settings.
  ASR `PG_EN`、复位电压/极性/时序未确认；不包含目标自动复位。115200 与 GPIO 均不代表已确认的 ASR 配置。

## Protocol / 协议

| Request / 请求 | Implemented behavior / 实现 |
|---|---|
| Binary, escaped IAC | Bidirectional UART, upstream Telnet parser / 双向 UART，上游解析器 |
| Baud | Query/set 300–2,000,000; accepted nominal setting / 查询设置，返回名义配置 |
| Data/parity/stop | 5–8 bits, N/O/E parity, 1/1.5/2 stop bits and queries / 数据位、校验、停止位及查询 |
| DTR/RTS | Independent GPIO query/set, configurable polarity / 独立 GPIO 查询设置 |
| Flow control | Only no-flow, outbound 0/1 and inbound 13/14 / 仅无流控 |
| BREAK, mark/space, other flow modes | Rejected; close without success ACK / 拒绝并断开，无假成功 |
| Line/modem status, suspend/resume | Rejected / 未实现并拒绝 |
| Line/modem masks | Only zero mask accepted / 仅零掩码 |
| RX purge (1) | Flush RX driver and invalidate unsent RX records / 清 RX 并使未发送 RX 记录失效 |
| TX purge (2), both (3) | Empty-only TX barrier; busy TX rejected / TX 已空才确认，忙态拒绝 |

RFC2217 has no generic NAK. Unsupported/malformed requests record a fault and close TCP. Clients requiring unconfigured DTR/RTS will fail. RX purge cannot retract bytes already sent into TCP; TX purge is not falsely implemented by RX flush. Not full RFC2217 coverage or a proven ASR programmer.
RFC2217 无通用 NAK，未实现/畸形请求记录故障并断开。依赖未配置 DTR/RTS 的客户端会失败。RX 清除不能撤回已交 TCP 数据，TX purge 不冒用 RX flush。这不是完整 RFC2217，也未证明 ASR 烧录兼容。

## Concurrency and limits / 并发与边界

Four tasks own TCP RX/parser/session, UART TX/control, UART RX, and TCP TX. Two bounded queues (32 records each by default) carry ordered UART data/control and complete wire frames; a 256-byte private staging block batches byte callbacks. UART RX has an independent 4096-byte driver buffer. Parser callbacks enqueue with zero wait. TCP writes are nonblocking with NODELAY and a per-block deadline; second clients are rejected.
四任务分别负责网络接收与会话、UART 发送与控制、UART 接收、网络发送；两条有界队列默认各 32 块，256 字节暂存块合并逐字节回调。UART RX 有独立驱动缓冲，回调零等待。网络发送非阻塞、NODELAY、有时限，拒绝第二客户端。

Queue full, UART overflow/line errors, unsupported operations and deadlines create explicit faults. The first session error and most recent nonzero error are retained in RAM/logs across reconnect. Logs only use the separate console. UART TX has one owner, no software TX ring, FIFO-only writes, a 2ms cancellation poll and a default 1000ms per-block deadline. Format changes poll TX-idle while RX remains independent.
队列满、溢出、线路错误、未实现请求与超时均明确故障；保存会话首错及上次非零故障。日志仅发独立 console。UART TX 唯一任务拥有，无软件 TX ring，FIFO 有界写入，2ms 取消轮询，每块默认 1000ms 时限；改变格式前等待 TX 空，RX 独立运行。

Disconnect marks inactive, detaches pins, flushes RX only and deletes the UART driver under a short RX mutex. It cannot recall already transmitted bits. Workers must acknowledge quiescence before queues/parser/socket reset; failure disables reconnect. Session IDs prevent old data replay; fresh sessions restore initial baud/8N1 and inactive controls. Wi-Fi retries at fixed 3s intervals. Physical cutoff latency and saturation behavior have not been measured.
断线后标记停用、脱离引脚、只清 RX，并与 RX 任务互斥后卸载驱动；无法撤回已发送位。所有任务确认停用后才重置，失败则禁止重连。会话代号阻止旧缓存重播，新会话恢复初始 baud/8N1/非有效控制电平。Wi-Fi 固定 3 秒重连。物理截断时延、满载行为尚未测量。

High-baud throughput and client compatibility require an isolated test board. RFC2217 is unauthenticated plaintext: use a trusted isolated network or external secure tunnel.
高波特率吞吐及客户端兼容性仍需隔离测试板验证。RFC2217 是未鉴权明文，仅用于可信隔离网络或外部安全隧道。

Source and local patch: [THIRD_PARTY.md](THIRD_PARTY.md).
来源和补丁见 [THIRD_PARTY.md](THIRD_PARTY.md)。
