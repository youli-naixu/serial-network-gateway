# SerialNetworkGateway（Arduino-ESP32）

实验性 RFC2217（Telnet COM-PORT-OPTION）串口网关：把 ESP32 的一个 UART 暴露在 TCP 端口上，电脑端用虚拟串口或 `rfc2217://` 客户端经 WiFi 访问目标板。可单独运行，也可作为已有业务工程里的按需**维护模式**。

状态：仅在 Arduino-ESP32 2.0.17（PlatformIO `espressif32@6.10.0`，ESP32-S3）下编译通过，**未做实物验证**，电气特性、与具体电脑软件的互操作和时序均未测试。

## 接线

| ESP32 | 目标板 |
|---|---|
| `txPin` | RX |
| `rxPin` | TX |
| GND | GND |
| `resetPin`（可选） | EN / RESET |
| `bootPin`（可选） | BOOT / IO0 |

- 只支持 3.3V 电平，5V 目标板需加电平转换；不要经 UART 引脚给目标板反向供电。
- 目标板 RX 线上只能有一个发送端。若同时接了 USB 转串口或其他 MCU，必须断开或隔离（串联电阻、二极管线与、三态缓冲）。两个推挽输出对顶可能损坏双方。
- 引脚默认 `-1`，未配置 UART 和安全 TX/RX 引脚时 `begin()` 返回错误。ESP32-S3 上拒绝 flash/PSRAM 引脚（22-37）、USB（19/20）、控制台（43/44）、Strapping（0/3/45/46）和 GPIO48。

## 安静模式（默认开启）

部分虚拟串口驱动（例如作者电脑上实测的 HW VSP3 Single）打开 COM 时直接发送 SET-BAUDRATE/DATASIZE/PARITY/STOPSIZE/CONTROL 子协商，不先发 WILL/DO；服务器主动发出的 IAC WILL/DO、NOTIFY-MODEMSTATE 等会被当作串口数据交给应用。因此安静模式下：

- 不主动发送任何选项协商或通知；
- 对客户端的 DO/WILL 正常应答，只回复客户端发来的子协商；
- 收到第一条 COM-PORT-OPTION 子协商即视为完成 RFC2217 协商；
- 未收到 SET-BAUDRATE 前保持 `defaultBaud`（有的驱动打开后不下发全部波特率变化，应把 `defaultBaud` 设成目标板实际使用的值，例如下载波特率）；
- 客户端不请求就不动 DTR/RTS。

在 Windows 11 上实测 HW VSP3 Single 3.1.2：打开串口时会下发串口参数，但不发 Telnet 选项协商；服务器主动发出的选项字节会被它当成串口数据；不转发 DTR/RTS/BREAK；打开后高于 57600 的波特率变化不会下发。配套电脑端软件的 com0com 桥接能转发上述全部内容，目标板需要控制线或中途切换波特率时请使用它。

使用 pySerial `rfc2217://` 等标准客户端时设 `quietMode = false`：服务器会发送 WILL SGA、DO BINARY、WILL COM-PORT-OPTION，协商前收到数据即关闭会话。

## 支持与拒绝的请求

支持：SET-BAUDRATE（300..2000000，0 为查询）、SET-DATASIZE 5..8、SET-PARITY 无/奇/偶、SET-STOPSIZE 1/2/1.5、SET-CONTROL 无流控、BREAK 置位/清除/查询（通过反相 TX 线让其保持低电平）、DTR/RTS 置位/清除/查询、line/modem state mask 0、PURGE-DATA。

未配置引脚时收到 DTR/RTS 请求：如实回复当前状态（“关”，因为没有驱动任何引脚），并计入 `ignoredControls`，不再断开会话。很多电脑端工具打开串口时会主动设置 DTR/RTS，这样既能正常使用，又不会虚报成功。

拒绝（以 `unsupported_request` 关闭会话；RFC2217 没有通用 NAK，网关不假装成功）：XON/XOFF 与硬件流控、DSR/DCD 流控、MARK/SPACE 校验、非零 mask、NOTIFY-*、FLOWCONTROL-SUSPEND/RESUME、NOP 以外的 Telnet 命令。

## 数据路径

四个 FreeRTOS 任务（UART TX、UART RX、TCP TX、TCP RX）加两个有界队列。队列满时以明确的 fault（`network_to_uart_full`、`uart_to_network_full`）断开，不静默丢数据；UART 接收溢出也断开。帧错误/校验错误/BREAK 默认只计数（`lineErrors`），`faultOnLineError = true` 时断开。单客户端，多余连接直接关闭并计数。断线清空队列，数据不跨会话；客户端断开时脱离 TX/RX 引脚。设置 `TCP_NODELAY`，维护期间关闭 WiFi 省电，退出后恢复原设置。

## 在已有工程中使用维护模式

```cpp
SerialNetworkGateway gw;

void setup() {
  SerialNetworkGatewayConfig cfg;
  cfg.uartNum = 1; cfg.txPin = APP_TX; cfg.rxPin = APP_RX;
  cfg.defaultBaud = 115200;
  cfg.maintenanceTimeoutMs = 10UL * 60 * 1000;   // 无客户端 10 分钟自动退出
  gw.configure(cfg);
  gw.onExit([](SerialNetworkGateway::ExitReason) { Serial1.begin(9600, SERIAL_8N1, APP_RX, APP_TX); });
}

void enterMaintenance() { Serial1.end(); if (gw.begin() != SerialNetworkGateway::Error::Ok) Serial1.begin(9600, SERIAL_8N1, APP_RX, APP_TX); }
void loop() { gw.loop(); /* 其他任务照常运行 */ }
```

- 库直接安装 IDF UART 驱动。若该 UART 已有驱动（例如 Serial1 未关闭），`begin()` 返回 `UartInUse`，需先 `Serial1.end()`。
- `loop()` 不阻塞。`end()` 最多等待 `ioDeadlineMs + 1.5 s`；工作任务未停下时返回 `NotQuiescent` 并保留资源，此时不要重开 Serial1，应重启设备。
- 维护计时只在无客户端时进行，每次连接/断开重新计时；`idleTimeoutMs` 用于关闭无数据的已连接会话。
- `resetTarget(ms)`、`setBootPin(bool)`、`enterDownloadMode(resetMs, holdMs)` 控制可选的 RESET/BOOT 引脚（极性可配，默认低有效），客户端无法触发，应由宿主的已鉴权控制路径调用。
- 库内不建 HTTP 服务。若通过 HTTP 暴露 `begin()/end()/statusJson()`，必须放在宿主现有鉴权之后。

## 来源 IP 白名单

```cpp
cfg.allowedIps[0] = IPAddress(/* 你的电脑 */);
cfg.allowedIpCount = 1;   // 0 表示不限制
```

白名单不是鉴权：RFC2217 本身无认证无加密，只应在可信局域网使用，并尽量缩短维护窗口。

## 状态

`status()` 返回结构体，`statusJson()` 返回同内容 JSON：state（`disabled`、`waiting_wifi`、`listening`、`connected`、`error`）、客户端 IP、会话号、四向字节计数（`tcpRx`、`uartTx`、`uartRx`、`tcpTx`）、队列峰值、本次与最近 fault、被拒连接数、线路错误数、当前串口参数、DTR/RTS/BREAK、`ignoredControls`、维护剩余时间。

## 电脑端配合

- 虚拟串口驱动：新建指向 `<esp32-ip>:3333` 的 RFC2217 端口，网关保持安静模式；若驱动不下发波特率变化，把 `defaultBaud` 设成工具使用的波特率。
- pySerial / esptool：`rfc2217://<esp32-ip>:3333`，配合 `quietMode = false`。esptool 的 DTR/RTS 自动复位需要把 `dtrPin`/`rtsPin` 接到自动复位电路；否则由宿主调用 `enterDownloadMode()`，esptool 使用 `--before no_reset`。

## 编译检查

```sh
cd ci
PLATFORMIO_CORE_DIR=<core 目录> pio run
```

`ci/platformio.ini` 针对 `esp32-s3-devkitc-1` 编译两个示例。

## 许可证

Apache-2.0。`src/rfc2217/` 来自 [igrr/rfc2217-server](https://github.com/igrr/rfc2217-server)（Apache-2.0），本地修改列在 `rfc2217_server.c` 文件头。
