# SerialNetworkGateway (Arduino-ESP32)

Experimental RFC2217 (Telnet COM-PORT-OPTION) gateway that exposes one ESP32 UART on a TCP port, so a PC-side virtual COM port or `rfc2217://` client can talk to a target board over WiFi. It can run as a standalone sketch or as an on-demand **maintenance mode** inside an existing application.

Status: compile-tested with Arduino-ESP32 2.0.17 (PlatformIO `espressif32@6.10.0`, ESP32-S3). It has **not** been verified on hardware: electrical behaviour, interoperability with specific PC software and timing are untested.

## Wiring

| ESP32 | Target |
|---|---|
| `txPin` | RX |
| `rxPin` | TX |
| GND | GND |
| `resetPin` (optional) | EN / RESET |
| `bootPin` (optional) | BOOT / IO0 |

- 3.3 V logic only. Use a level shifter for 5 V targets. Do not back-power the target through the UART pins.
- Exactly one device may drive the target's RX line. If a USB-serial adapter or another MCU is also connected, disconnect it or isolate it (series resistor / diode-OR / tri-state buffer). Two push-pull transmitters on one line can damage both.
- Pins default to `-1`. `begin()` refuses until a UART and safe TX/RX pins are configured. On ESP32-S3 the library rejects flash/PSRAM pins (22-37), USB (19/20), console (43/44), straps (0/3/45/46) and GPIO48.

## Quiet mode (default on)

Some virtual-COM drivers (for example HW VSP3 Single, as tested by the author's PC setup) send SET-BAUDRATE / DATASIZE / PARITY / STOPSIZE / CONTROL subnegotiations when the COM port opens, without sending WILL/DO first, and pass any unsolicited Telnet bytes from the server (IAC WILL/DO, NOTIFY-MODEMSTATE) to the application as serial data. In quiet mode the gateway therefore:

- never sends option requests or notifications on its own;
- answers client DO/WILL normally and replies only to subnegotiations the client sent;
- treats the first COM-PORT-OPTION subnegotiation as RFC2217 negotiation;
- keeps `defaultBaud` until the client sends SET-BAUDRATE (some drivers do not forward every baud change after the port is open, so pick a `defaultBaud` that matches your target, such as its download baud rate);
- leaves DTR/RTS untouched unless the client requests a change.

Measured with HW VSP3 Single 3.1.2 on Windows 11: it sends serial settings on open but not unsolicited Telnet negotiation, treats any server-initiated option bytes as serial data, does not forward DTR/RTS/BREAK, and drops baud changes above 57600 after the port is open. The companion PC tool's com0com bridge forwards all of these; use it when the target needs control lines or a mid-session baud switch.

Set `quietMode = false` for standard clients such as pySerial `rfc2217://`; the server then sends WILL SGA, DO BINARY and WILL COM-PORT-OPTION, and data before negotiation closes the session.

## Supported and rejected requests

Supported: SET-BAUDRATE (300..2000000, 0 = query), SET-DATASIZE 5..8, SET-PARITY none/odd/even, SET-STOPSIZE 1/2/1.5, SET-CONTROL no flow control, BREAK on/off/query (TX held at space by inverting the TX line), DTR/RTS set/clear/query, line/modem state mask 0, PURGE-DATA.

DTR/RTS without a configured pin: the request is answered with the real state ("off", because nothing is driven) and counted in `ignoredControls`, instead of closing the session. Many PC tools set DTR/RTS on open; this keeps them working while still reporting truthfully.

Rejected (session closed with `unsupported_request`; RFC2217 has no generic NAK, so the gateway never sends a fake acknowledgement): XON/XOFF and hardware flow control, DSR/DCD flow, MARK/SPACE parity, non-zero line/modem masks, NOTIFY-*, FLOWCONTROL-SUSPEND/RESUME, Telnet commands other than NOP.

## Data path

Four FreeRTOS tasks (UART TX, UART RX, TCP TX, TCP RX) and two bounded queues. A full queue closes the session with an explicit fault (`network_to_uart_full`, `uart_to_network_full`); data is never silently dropped. UART RX overflow closes the session. Frame/parity/break events are counted (`lineErrors`) and close the session only with `faultOnLineError = true`. One client at a time; extra connections are closed and counted. Queues are cleared on disconnect and never carry data across sessions. TX/RX pins are detached when the client disconnects. `TCP_NODELAY` is set and WiFi power save is disabled while maintenance mode is active, then restored.

## Maintenance mode in an existing application

```cpp
SerialNetworkGateway gw;

void setup() {
  SerialNetworkGatewayConfig cfg;
  cfg.uartNum = 1; cfg.txPin = APP_TX; cfg.rxPin = APP_RX;
  cfg.defaultBaud = 115200;
  cfg.maintenanceTimeoutMs = 10UL * 60 * 1000;   // auto exit without a client
  gw.configure(cfg);
  gw.onExit([](SerialNetworkGateway::ExitReason) { Serial1.begin(9600, SERIAL_8N1, APP_RX, APP_TX); });
}

void enterMaintenance() { Serial1.end(); if (gw.begin() != SerialNetworkGateway::Error::Ok) Serial1.begin(9600, SERIAL_8N1, APP_RX, APP_TX); }
void loop() { gw.loop(); /* other work continues */ }
```

- The library installs the IDF UART driver itself. If any driver already owns the UART, `begin()` returns `UartInUse`; call `Serial1.end()` first.
- `loop()` is non-blocking. `end()` waits up to `ioDeadlineMs + 1.5 s` for the workers. If they do not stop it returns `NotQuiescent`, keeps its resources, and you should not restart `Serial1`; reboot instead.
- The maintenance timer runs while no client is connected and restarts at each connect/disconnect. `idleTimeoutMs` closes a connected session without traffic.
- `resetTarget(ms)`, `setBootPin(bool)` and `enterDownloadMode(resetMs, holdMs)` drive the optional RESET/BOOT pins (polarity configurable, active low by default). They are never triggered by the client; call them from your own authenticated control path.
- The library does not start any HTTP server. Expose `begin()/end()/statusJson()` only behind your application's existing authentication.

## Source IP allow-list

```cpp
cfg.allowedIps[0] = IPAddress(/* your PC */);
cfg.allowedIpCount = 1;   // 0 = accept any source
```

The allow-list is not authentication: RFC2217 has no authentication or encryption. Use it on a trusted LAN only, and keep maintenance windows short.

## Status

`status()` returns a struct and `statusJson()` returns the same data as JSON: state (`disabled`, `waiting_wifi`, `listening`, `connected`, `error`), client IP, session number, byte counters (`tcpRx`, `uartTx`, `uartRx`, `tcpTx`), queue peaks, session and last fault, rejected connections, line errors, current serial parameters, DTR/RTS/BREAK, `ignoredControls` and remaining maintenance time.

## PC side

- Virtual COM driver: create a port pointing to `<esp32-ip>:3333` with RFC2217 enabled, keep the gateway in quiet mode, and set `defaultBaud` to the rate your tool uses if the driver does not forward baud changes.
- pySerial / esptool: `rfc2217://<esp32-ip>:3333` with `quietMode = false`. esptool's DTR/RTS reset needs `dtrPin`/`rtsPin` wired to an auto-reset circuit; otherwise trigger `enterDownloadMode()` from the host application and use `--before no_reset`.

## Build check

```sh
cd ci
PLATFORMIO_CORE_DIR=<core dir> pio run
```

`ci/platformio.ini` builds both examples for `esp32-s3-devkitc-1`.

## License

Apache-2.0. `src/rfc2217/` is vendored from [igrr/rfc2217-server](https://github.com/igrr/rfc2217-server) (Apache-2.0) with local modifications listed at the top of `rfc2217_server.c`.
