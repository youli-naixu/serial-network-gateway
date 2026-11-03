// SPDX-License-Identifier: Apache-2.0
// SerialNetworkGateway: experimental RFC2217 (Telnet COM-PORT-OPTION) UART bridge
// for Arduino-ESP32 2.x. Single client, bounded queues, fail-closed on overflow.
#pragma once

#include <Arduino.h>
#include <IPAddress.h>
#include <functional>

struct SerialNetworkGatewayConfig {
    // UART owned by the gateway while a client is connected. 0 (console) is rejected.
    int uartNum = -1;
    int txPin = -1;
    int rxPin = -1;

    // Optional modem-control outputs driven only by client SET-CONTROL requests.
    // When a pin is -1, requests are answered with the real state (off) and
    // counted in ignoredControls instead of closing the session.
    int dtrPin = -1;
    bool dtrActiveLow = false;
    int rtsPin = -1;
    bool rtsActiveLow = false;

    // Optional target RESET / BOOT outputs, driven only by explicit host calls
    // (resetTarget / setBootPin / enterDownloadMode), never by the client.
    int resetPin = -1;
    bool resetActiveLow = true;
    int bootPin = -1;
    bool bootActiveLow = true;

    uint16_t tcpPort = 3333;
    uint32_t defaultBaud = 115200;      // used until the client sends SET-BAUDRATE
    uint8_t queueDepth = 8;              // blocks per direction (2..64)
    uint32_t uartRxBufferSize = 4096;    // IDF UART driver RX ring (256..65536)
    uint32_t idleTimeoutMs = 300000;     // close a session with no traffic; 0 = off
    uint32_t maintenanceTimeoutMs = 600000; // auto end() with no client; 0 = off
    uint32_t ioDeadlineMs = 2000;        // per-block UART TX / socket TX deadline

    // Quiet mode: never send unsolicited Telnet negotiation or notifications;
    // only answer what the client asks. Required for some virtual-COM drivers.
    bool quietMode = true;
    // UART frame/parity/break events: count only (false) or close the session.
    bool faultOnLineError = false;

    // Optional target preparation run once per session after the UART driver is
    // installed (defaultBaud, configured pins) and before any client byte or
    // command reaches the UART. Runs in the gateway's UART task; client traffic
    // is queued meanwhile, so keep it short and within queueDepth blocks. The
    // gateway restores the configured pins and defaultBaud afterwards and drops
    // whatever the hook left in the RX buffer. A false return is counted in
    // hookFailures; the session continues.
    std::function<bool(int uartNum)> sessionStartHook;

    // Optional source IPv4 allow-list. Count 0 = accept any source.
    static constexpr uint8_t kMaxAllowedIps = 4;
    IPAddress allowedIps[kMaxAllowedIps];
    uint8_t allowedIpCount = 0;
};

enum class SerialNetworkGatewayState : uint8_t {
    Disabled,     // not in maintenance mode
    WaitingWifi,  // maintenance mode, no WiFi STA connection / no listener yet
    Listening,    // waiting for a client
    Connected,    // client session active, UART owned by the gateway
    Error         // shutdown could not quiesce workers; resources intentionally kept
};

struct SerialNetworkGatewayStatus {
    SerialNetworkGatewayState state;
    const char *stateName;
    IPAddress clientIp;          // 0.0.0.0 when no client
    uint32_t session;            // increments per accepted client
    uint32_t tcpRxBytes;         // raw bytes received from the socket (incl. Telnet)
    uint32_t uartTxBytes;        // payload bytes written to the UART
    uint32_t uartRxBytes;        // bytes read from the UART
    uint32_t tcpTxBytes;         // bytes sent to the socket (incl. escaping/replies)
    uint8_t queueDepth;
    uint32_t toUartQueuePeak;
    uint32_t toNetQueuePeak;
    const char *sessionFault;    // first fault of the current/last session
    const char *lastFault;       // most recent non-"none" fault, kept across sessions
    uint32_t rejectedConnections;
    uint32_t lineErrors;
    uint32_t baud;
    uint8_t dataBits;
    char parity;                 // 'N', 'O', 'E'
    const char *stopBits;        // "1", "1.5", "2"
    bool dtr;
    bool rts;
    bool breakOn;                // TX held at space by a client BREAK request
    uint32_t ignoredControls;    // DTR/RTS "on" requests for unconfigured pins
    uint32_t hookRuns;           // sessionStartHook calls since begin()
    uint32_t hookFailures;       // of which returned false
    bool quietMode;
    uint16_t tcpPort;
    uint32_t maintenanceRemainingMs; // 0 when disabled, timer off, or connected
};

class SerialNetworkGateway {
public:
    enum class Error : uint8_t {
        Ok,
        NotConfigured,
        InvalidConfig,
        AlreadyRunning,
        NotRunning,
        UartInUse,
        NoMemory,
        TaskCreateFailed,
        NotQuiescent,
        PinNotConfigured,
        Busy,
        InvalidArgument
    };
    enum class ExitReason : uint8_t { Requested, MaintenanceTimeout };

    using EnterCallback = std::function<void()>;
    using ExitCallback = std::function<void(ExitReason)>;

    SerialNetworkGateway();
    ~SerialNetworkGateway();
    SerialNetworkGateway(const SerialNetworkGateway &) = delete;
    SerialNetworkGateway &operator=(const SerialNetworkGateway &) = delete;

    // Validates and stores the configuration. Not allowed while running.
    Error configure(const SerialNetworkGatewayConfig &config);
    // Enter maintenance mode. Fails with UartInUse if any driver owns the UART
    // (call Serial1.end() etc. first). Does not touch TX/RX until a client connects.
    Error begin();
    // Leave maintenance mode: close the client, release pins, restore WiFi sleep.
    // Waits up to ioDeadlineMs + 1.5 s for workers. Calls onExit on success.
    Error end();
    // Non-blocking housekeeping (reset pulses, maintenance timeout). Call often.
    void loop();

    bool isRunning() const;
    SerialNetworkGatewayStatus status() const;
    String statusJson() const;

    void onEnter(EnterCallback callback);
    void onExit(ExitCallback callback);

    // Explicit target control. Require begin() and a configured pin. Non-blocking:
    // pulses are released from loop().
    Error resetTarget(uint32_t pulseMs = 100);
    Error setBootPin(bool asserted);
    // Assert BOOT, pulse RESET for resetMs, keep BOOT for bootHoldMs more.
    Error enterDownloadMode(uint32_t resetMs = 100, uint32_t bootHoldMs = 300);

    static const char *errorName(Error error);

    struct Impl;

private:
    Error stop(ExitReason reason);
    Impl *impl_;
};
