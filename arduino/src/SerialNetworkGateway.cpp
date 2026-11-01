// SPDX-License-Identifier: Apache-2.0
// Port of firmware/main/gateway.c to an Arduino-ESP32 2.x library:
// four worker tasks, two bounded queues, session ids, first-fault tracking,
// pins detached on disconnect. Parser callbacks never block.
#include "SerialNetworkGateway.h"

#include <WiFi.h>
#include <atomic>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

#include "rfc2217/rfc2217_server.h"

namespace {

constexpr size_t BLOCK = 256;
constexpr TickType_t POLL = pdMS_TO_TICKS(2) ? pdMS_TO_TICKS(2) : 1;
constexpr TickType_t IDLE_POLL = pdMS_TO_TICKS(10) ? pdMS_TO_TICKS(10) : 1;
constexpr uint32_t MAX_BAUD = 2000000;
constexpr uint32_t MIN_BAUD = 300;

// Event bits: *_IDLE = worker observed inactive session; *_EXIT = task finished.
constexpr EventBits_t UART_IDLE = BIT0;
constexpr EventBits_t RX_IDLE = BIT1;
constexpr EventBits_t NET_IDLE = BIT2;
constexpr EventBits_t ALL_IDLE = UART_IDLE | RX_IDLE | NET_IDLE;
constexpr EventBits_t UART_TX_EXIT = BIT4;
constexpr EventBits_t UART_RX_EXIT = BIT5;
constexpr EventBits_t NET_TX_EXIT = BIT6;
constexpr EventBits_t NET_RX_EXIT = BIT7;
constexpr EventBits_t ALL_EXIT = UART_TX_EXIT | UART_RX_EXIT | NET_TX_EXIT | NET_RX_EXIT;

enum ItemKind : uint8_t { ITEM_START, ITEM_DATA, ITEM_COMMAND };
struct UartItem {
    uint32_t session;
    uint16_t len;
    uint8_t kind, command;
    uint8_t data[BLOCK];
};
struct NetItem {
    uint32_t session, rxEpoch;
    uint16_t len;
    bool uartData;
    uint8_t data[BLOCK * 2];
};

enum Fault : int {
    FAULT_NONE, FAULT_INPUT_FULL, FAULT_OUTPUT_FULL, FAULT_PROTOCOL,
    FAULT_UNSUPPORTED, FAULT_UART, FAULT_UART_OVERFLOW, FAULT_UART_LINE,
    FAULT_UART_TIMEOUT, FAULT_SOCKET, FAULT_SOCKET_TIMEOUT, FAULT_WIFI,
    FAULT_QUIESCE, FAULT_NOT_NEGOTIATED, FAULT_IDLE, FAULT_UART_IN_USE,
    FAULT_STOPPED, FAULT_COUNT
};
const char *const kFaultNames[FAULT_COUNT] = {
    "none", "network_to_uart_full", "uart_to_network_full", "malformed_telnet",
    "unsupported_request", "uart_driver", "uart_rx_overflow", "uart_line_error",
    "uart_tx_deadline", "socket_io", "socket_tx_deadline", "wifi_disconnected",
    "shutdown_not_quiescent", "rfc2217_not_negotiated", "idle_timeout",
    "uart_in_use", "maintenance_stopped"
};

bool safePin(int pin, bool output)
{
    if (pin < 0 || pin >= GPIO_NUM_MAX) { return false; }
    if (output ? !GPIO_IS_VALID_OUTPUT_GPIO(pin) : !GPIO_IS_VALID_GPIO(pin)) { return false; }
#if CONFIG_IDF_TARGET_ESP32S3
    // Excludes flash/PSRAM (22-37 incl. octal), USB 19/20, console 43/44,
    // straps 0/3/45/46 and the common onboard RGB LED pin 48.
    return !((pin >= 22 && pin <= 37) || pin == 0 || pin == 3 || pin == 19 ||
             pin == 20 || pin == 43 || pin == 44 || pin == 45 || pin == 46 || pin == 48);
#elif CONFIG_IDF_TARGET_ESP32
    return !((pin >= 6 && pin <= 11) || pin == 1 || pin == 3 || pin == 0 || pin == 2 ||
             pin == 12 || pin == 15);
#else
    return true;
#endif
}

// Latch the inactive level, then connect the pad as a plain GPIO output.
bool driveOutput(int pin, bool asserted, bool activeLow)
{
    if (pin < 0) { return true; }
    gpio_set_level(static_cast<gpio_num_t>(pin), (asserted ^ activeLow) ? 1 : 0);
    gpio_config_t c = {};
    c.pin_bit_mask = 1ULL << pin;
    c.mode = GPIO_MODE_OUTPUT;
    c.pull_up_en = GPIO_PULLUP_DISABLE;
    c.pull_down_en = GPIO_PULLDOWN_DISABLE;
    c.intr_type = GPIO_INTR_DISABLE;
    return gpio_config(&c) == ESP_OK;
}

bool setLevel(int pin, bool asserted, bool activeLow)
{
    return pin >= 0 && gpio_set_level(static_cast<gpio_num_t>(pin), (asserted ^ activeLow) ? 1 : 0) == ESP_OK;
}

// Stop driving; keep a weak pull toward the inactive level.
void releaseOutput(int pin, bool activeLow)
{
    if (pin < 0) { return; }
    gpio_config_t c = {};
    c.pin_bit_mask = 1ULL << pin;
    c.mode = GPIO_MODE_INPUT;
    c.pull_up_en = activeLow ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE;
    c.pull_down_en = activeLow ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE;
    c.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&c);
}

void updatePeak(std::atomic<uint32_t> &peak, QueueHandle_t q)
{
    uint32_t now = uxQueueMessagesWaiting(q);
    uint32_t old = peak.load();
    while (now > old && !peak.compare_exchange_weak(old, now)) {}
}

bool timeReached(uint32_t now, uint32_t at) { return static_cast<int32_t>(now - at) >= 0; }

}  // namespace

struct SerialNetworkGateway::Impl {
    SerialNetworkGatewayConfig cfg;
    bool configured = false;
    bool running = false;
    std::atomic<bool> errorState{false};

    QueueHandle_t toUart = nullptr, toNet = nullptr, uartEvents = nullptr;
    SemaphoreHandle_t uartLock = nullptr;
    EventGroupHandle_t ev = nullptr;
    rfc2217_server_t parser = nullptr;

    std::atomic<bool> active{false}, negotiated{false}, uartReady{false};
    std::atomic<bool> stopRequested{false}, listening{false};
    bool uartOwned = false;  // only the UART writer task touches this
    std::atomic<uint32_t> session{0}, rxEpoch{0};
    std::atomic<int> socketFd{-1};
    std::atomic<int> firstFault{FAULT_NONE}, lastFault{FAULT_NONE};

    std::atomic<uint32_t> baud{115200};
    std::atomic<uint8_t> bits{8}, parity{1}, stops{1};
    std::atomic<bool> dtr{false}, rts{false}, breakOn{false};
    std::atomic<uint32_t> unsupportedControls{0};

    std::atomic<uint32_t> tcpRx{0}, uartTx{0}, uartRx{0}, tcpTx{0};
    std::atomic<uint32_t> toUartPeak{0}, toNetPeak{0};
    std::atomic<uint32_t> rejected{0}, lineErrors{0};
    std::atomic<uint32_t> clientIp{0};
    std::atomic<uint32_t> lastActivityMs{0}, lastSessionEventMs{0};

    UartItem staging{};  // owned by the network reader

    // Host-task-only pulse scheduling.
    bool resetPending = false, bootPending = false;
    uint32_t resetReleaseAt = 0, bootReleaseAt = 0;
    bool bootAsserted = false;
    wifi_ps_type_t prevWifiSleep = WIFI_PS_MIN_MODEM;

    EnterCallback onEnter;
    ExitCallback onExit;

    // ---- shared helpers -------------------------------------------------
    void fault(int reason)
    {
        int expected = FAULT_NONE;
        firstFault.compare_exchange_strong(expected, reason);
        if (reason != FAULT_NONE) { lastFault.store(reason); }
        active.store(false);
    }
    bool live(uint32_t id) const { return active.load() && session.load() == id; }
    void touch() { lastActivityMs.store(millis()); }

    bool enqueueWire(const uint8_t *data, size_t len, bool isUart, uint32_t epoch)
    {
        uint32_t id = session.load();
        if (!live(id)) { return false; }
        if (len > BLOCK * 2) { fault(FAULT_PROTOCOL); return false; }
        NetItem item;
        item.session = id; item.rxEpoch = epoch; item.len = len; item.uartData = isUart;
        memcpy(item.data, data, len);
        if (xQueueSend(toNet, &item, 0) != pdTRUE) { fault(FAULT_OUTPUT_FULL); return false; }
        updatePeak(toNetPeak, toNet);
        return true;
    }

    bool flushStaging()
    {
        if (!staging.len) { return true; }
        if (!live(staging.session)) { staging.len = 0; return false; }
        bool ok = xQueueSend(toUart, &staging, 0) == pdTRUE;
        staging.len = 0;
        if (!ok) { fault(FAULT_INPUT_FULL); } else { updatePeak(toUartPeak, toUart); }
        return ok;
    }

    bool needNegotiation() const { return !cfg.quietMode && !negotiated.load(); }

    // ---- parser callbacks (network reader / UART writer context) ---------
    static int onWire(void *ctx, const uint8_t *data, size_t len)
    {
        return static_cast<Impl *>(ctx)->enqueueWire(data, len, false, 0) ? 0 : -1;
    }
    static void onProtocol(void *ctx) { static_cast<Impl *>(ctx)->fault(FAULT_PROTOCOL); }
    static void onNegotiated(void *ctx) { static_cast<Impl *>(ctx)->negotiated.store(true); }
    static void onData(void *ctx, const uint8_t *data, size_t len)
    {
        Impl *s = static_cast<Impl *>(ctx);
        if (!s->active.load()) { return; }
        if (s->needNegotiation()) { s->fault(FAULT_NOT_NEGOTIATED); return; }
        while (len && s->active.load()) {
            size_t n = BLOCK - s->staging.len;
            if (n > len) { n = len; }
            memcpy(s->staging.data + s->staging.len, data, n);
            s->staging.len += n; data += n; len -= n;
            if (s->staging.len == BLOCK && !s->flushStaging()) { return; }
        }
    }
    static void onCommand(void *ctx, uint8_t cmd, const uint8_t *data, size_t len)
    {
        Impl *s = static_cast<Impl *>(ctx);
        if (!s->active.load()) { return; }
        if (s->needNegotiation()) { s->fault(FAULT_NOT_NEGOTIATED); return; }
        if (!s->flushStaging()) { return; }
        UartItem item;
        item.session = s->session.load(); item.kind = ITEM_COMMAND; item.command = cmd;
        item.len = len;
        memcpy(item.data, data, len);
        if (xQueueSend(s->toUart, &item, 0) != pdTRUE) { s->fault(FAULT_INPUT_FULL); }
        else { updatePeak(s->toUartPeak, s->toUart); }
    }

    // ---- UART ownership (UART writer task) -------------------------------
    uart_port_t port() const { return static_cast<uart_port_t>(cfg.uartNum); }

    bool uartOpen()
    {
        if (uart_is_driver_installed(port())) { fault(FAULT_UART_IN_USE); return false; }
        baud.store(cfg.defaultBaud); bits.store(8); parity.store(1); stops.store(1);
        dtr.store(false); rts.store(false); breakOn.store(false);
        uart_config_t c = {};
        c.baud_rate = static_cast<int>(cfg.defaultBaud);
        c.data_bits = UART_DATA_8_BITS;
        c.parity = UART_PARITY_DISABLE;
        c.stop_bits = UART_STOP_BITS_1;
        c.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
        c.source_clk = UART_SCLK_APB;
        // TX ring buffer = 0: only this task writes, uart_tx_chars never waits.
        if (uart_driver_install(port(), cfg.uartRxBufferSize, 0, 64, &uartEvents, 0) != ESP_OK) {
            fault(FAULT_UART);
            return false;
        }
        uartOwned = true;
        if (uart_param_config(port(), &c) != ESP_OK ||
            !driveOutput(cfg.dtrPin, false, cfg.dtrActiveLow) ||
            !driveOutput(cfg.rtsPin, false, cfg.rtsActiveLow) ||
            uart_set_pin(port(), cfg.txPin, cfg.rxPin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK ||
            uart_flush_input(port()) != ESP_OK) {
            fault(FAULT_UART);
            return false;
        }
        xQueueReset(uartEvents);
        uartReady.store(true);
        return true;
    }

    void uartClose()
    {
        uartReady.store(false);
        if (xSemaphoreTake(uartLock, pdMS_TO_TICKS(100)) != pdTRUE) { fault(FAULT_QUIESCE); return; }
        // Detach stops future driven bits; a byte already on the wire cannot be recalled.
        gpio_reset_pin(static_cast<gpio_num_t>(cfg.txPin));
        gpio_reset_pin(static_cast<gpio_num_t>(cfg.rxPin));
        releaseOutput(cfg.dtrPin, cfg.dtrActiveLow);
        releaseOutput(cfg.rtsPin, cfg.rtsActiveLow);
        if (uartOwned && uart_is_driver_installed(port())) {
            uart_flush_input(port());
            uart_driver_delete(port());
        }
        uartOwned = false;
        uartEvents = nullptr;
        dtr.store(false); rts.store(false); breakOn.store(false);
        xSemaphoreGive(uartLock);
    }

    bool waitUartIdle(uint32_t id)
    {
        int64_t deadline = esp_timer_get_time() + static_cast<int64_t>(cfg.ioDeadlineMs) * 1000;
        while (live(id)) {
            if (uart_wait_tx_done(port(), 0) == ESP_OK) { return true; }
            if (esp_timer_get_time() >= deadline) { fault(FAULT_UART_TIMEOUT); return false; }
            vTaskDelay(POLL);
        }
        return false;
    }

    bool setModem(int pin, bool activeLow, bool next, std::atomic<bool> &state)
    {
        if (!setLevel(pin, next, activeLow)) { return false; }
        state.store(next);
        return true;
    }

    void command(const UartItem &item)
    {
        uint8_t cmd = item.command, value = item.len ? item.data[0] : 0;
        uint8_t reply[4] = {0};
        size_t replyLen = 1;
        esp_err_t err = ESP_OK;
        bool supported = true;
        // Never change the format of an in-flight character.
        if (cmd != 12 && !waitUartIdle(item.session)) { return; }
        if (xSemaphoreTake(uartLock, pdMS_TO_TICKS(20)) != pdTRUE) { fault(FAULT_UART); return; }
        if (!live(item.session)) { xSemaphoreGive(uartLock); return; }
        switch (cmd) {
        case 1: {  // SET-BAUDRATE (0 = query)
            uint32_t requested = (static_cast<uint32_t>(item.data[0]) << 24) |
                                 (static_cast<uint32_t>(item.data[1]) << 16) |
                                 (static_cast<uint32_t>(item.data[2]) << 8) | item.data[3];
            if (requested && (requested < MIN_BAUD || requested > MAX_BAUD)) { supported = false; break; }
            if (requested) { err = uart_set_baudrate(port(), requested); }
            if (err == ESP_OK && requested) { baud.store(requested); }
            uint32_t b = baud.load();
            reply[0] = b >> 24; reply[1] = b >> 16; reply[2] = b >> 8; reply[3] = b;
            replyLen = 4;
            break;
        }
        case 2:  // SET-DATASIZE
            if (value && (value < 5 || value > 8)) { supported = false; break; }
            if (value) { err = uart_set_word_length(port(), static_cast<uart_word_length_t>(value - 5)); }
            if (err == ESP_OK && value) { bits.store(value); }
            reply[0] = bits.load();
            break;
        case 3:  // SET-PARITY: MARK/SPACE unsupported
            if (value > 3) { supported = false; break; }
            if (value) {
                err = uart_set_parity(port(), value == 1 ? UART_PARITY_DISABLE :
                                      value == 2 ? UART_PARITY_ODD : UART_PARITY_EVEN);
            }
            if (err == ESP_OK && value) { parity.store(value); }
            reply[0] = parity.load();
            break;
        case 4:  // SET-STOPSIZE: 1=1, 2=2, 3=1.5
            if (value > 3) { supported = false; break; }
            if (value) {
                err = uart_set_stop_bits(port(), value == 1 ? UART_STOP_BITS_1 :
                                         value == 2 ? UART_STOP_BITS_2 : UART_STOP_BITS_1_5);
            }
            if (err == ESP_OK && value) { stops.store(value); }
            reply[0] = stops.load();
            break;
        case 5:  // SET-CONTROL
            switch (value) {
            case 0: case 1: reply[0] = 1; break;     // no outbound flow control
            case 13: case 14: reply[0] = 14; break;  // no inbound flow control
            case 4: case 5: case 6:  // BREAK query / on / off: hold TX at space via line inversion
                if (value != 4) {
                    err = uart_set_line_inverse(port(), value == 5 ? UART_SIGNAL_TXD_INV : UART_SIGNAL_INV_DISABLE);
                    if (err == ESP_OK) { breakOn.store(value == 5); }
                }
                reply[0] = breakOn.load() ? 5 : 6;
                break;
            case 7: case 8: case 9:
                // Unconfigured pin: report the real (undriven, off) state instead of
                // closing the session, so clients that always set DTR/RTS still work.
                if (cfg.dtrPin < 0) {
                    if (value == 8) { unsupportedControls.fetch_add(1); }
                    reply[0] = 9;
                    break;
                }
                if (value != 7 && !setModem(cfg.dtrPin, cfg.dtrActiveLow, value == 8, dtr)) { err = ESP_FAIL; }
                reply[0] = dtr.load() ? 8 : 9;
                break;
            case 10: case 11: case 12:
                if (cfg.rtsPin < 0) {
                    if (value == 11) { unsupportedControls.fetch_add(1); }
                    reply[0] = 12;
                    break;
                }
                if (value != 10 && !setModem(cfg.rtsPin, cfg.rtsActiveLow, value == 11, rts)) { err = ESP_FAIL; }
                reply[0] = rts.load() ? 11 : 12;
                break;
            default: supported = false; break;  // XON/XOFF, hardware flow, DSR/DCD
            }
            break;
        case 10: case 11:  // line/modem state masks: no monitoring, only 0 is truthful
            supported = value == 0;
            reply[0] = 0;
            break;
        case 12:  // PURGE-DATA
            if (value < 1 || value > 3) { supported = false; break; }
            // TX purge is an empty barrier, not an abort.
            if ((value & 2) && uart_wait_tx_done(port(), 0) != ESP_OK) { supported = false; break; }
            if (value & 1) {
                rxEpoch.fetch_add(1);
                err = uart_flush_input(port());
                xQueueReset(uartEvents);
            }
            reply[0] = value;
            break;
        default: supported = false; break;  // 6/7 notify, 8/9 suspend/resume
        }
        xSemaphoreGive(uartLock);
        if (!supported) {
            log_w("Unsupported RFC2217 command=%u value=%u, closing session", cmd, value);
            fault(FAULT_UNSUPPORTED);  // RFC2217 has no generic NAK: close, never fake ACK
        } else if (err != ESP_OK) {
            fault(FAULT_UART);
        } else if (live(item.session)) {
            rfc2217_server_reply(parser, cmd + 100, reply, replyLen);
        }
    }

    // ---- worker tasks -----------------------------------------------------
    bool shouldExit() const
    {
        return stopRequested.load() && !active.load() && (xEventGroupGetBits(ev) & NET_RX_EXIT);
    }

    void uartWriter()
    {
        UartItem item;
        for (;;) {
            if (!active.load()) {
                if (uartOwned) { uartClose(); }
                if (!uartOwned) { xEventGroupSetBits(ev, UART_IDLE); }
                if (shouldExit() && !uartOwned) { break; }
                vTaskDelay(IDLE_POLL);
                continue;
            }
            xEventGroupClearBits(ev, UART_IDLE);
            if (xQueueReceive(toUart, &item, POLL) != pdTRUE || !live(item.session)) { continue; }
            if (item.kind == ITEM_START) {
                uartOpen();
            } else if (!uartReady.load()) {
                fault(FAULT_UART);
            } else if (item.kind == ITEM_COMMAND) {
                command(item);
            } else {
                size_t offset = 0;
                int64_t deadline = esp_timer_get_time() + static_cast<int64_t>(cfg.ioDeadlineMs) * 1000;
                while (offset < item.len && live(item.session)) {
                    int n = uart_tx_chars(port(), reinterpret_cast<const char *>(item.data) + offset,
                                          item.len - offset);
                    if (n < 0) { fault(FAULT_UART); break; }
                    offset += n;
                    uartTx.fetch_add(n);
                    if (offset < item.len) {
                        if (esp_timer_get_time() >= deadline) { fault(FAULT_UART_TIMEOUT); break; }
                        vTaskDelay(POLL);
                    }
                }
            }
        }
        xEventGroupSetBits(ev, UART_TX_EXIT);
    }

    void uartReader()
    {
        uint8_t raw[BLOCK], escaped[BLOCK * 2];
        uart_event_t event;
        for (;;) {
            if (!active.load()) {
                xEventGroupSetBits(ev, RX_IDLE);
                if (shouldExit()) { break; }
                vTaskDelay(IDLE_POLL);
                continue;
            }
            xEventGroupClearBits(ev, RX_IDLE);
            if (!uartReady.load()) { vTaskDelay(POLL); continue; }
            uint32_t id = session.load();
            int n = 0;
            uint32_t epoch = 0;
            if (xSemaphoreTake(uartLock, POLL) == pdTRUE) {
                if (live(id) && uartReady.load()) {
                    epoch = rxEpoch.load();
                    // A full event queue could hide an ISR overflow notification.
                    if (uxQueueSpacesAvailable(uartEvents) == 0) { fault(FAULT_UART_OVERFLOW); }
                    for (unsigned count = 0; count < 64 && xQueueReceive(uartEvents, &event, 0) == pdTRUE; ++count) {
                        if (event.type == UART_FIFO_OVF || event.type == UART_BUFFER_FULL) {
                            fault(FAULT_UART_OVERFLOW);
                            break;
                        }
                        if (event.type == UART_PARITY_ERR || event.type == UART_FRAME_ERR ||
                            event.type == UART_BREAK) {
                            lineErrors.fetch_add(1);
                            if (cfg.faultOnLineError) { fault(FAULT_UART_LINE); break; }
                        }
                    }
                    if (live(id)) { n = uart_read_bytes(port(), raw, sizeof(raw), 0); }
                }
                xSemaphoreGive(uartLock);
            }
            if (n < 0) { fault(FAULT_UART); }
            if (n > 0 && live(id)) {
                uartRx.fetch_add(n);
                touch();
                size_t out = 0;
                for (int i = 0; i < n; ++i) {
                    escaped[out++] = raw[i];
                    if (raw[i] == 0xff) { escaped[out++] = 0xff; }
                }
                enqueueWire(escaped, out, true, epoch);
            }
            vTaskDelay(POLL);
        }
        xEventGroupSetBits(ev, UART_RX_EXIT);
    }

    void networkWriter()
    {
        NetItem item;
        for (;;) {
            if (!active.load()) {
                xEventGroupSetBits(ev, NET_IDLE);
                if (shouldExit()) { break; }
                vTaskDelay(IDLE_POLL);
                continue;
            }
            xEventGroupClearBits(ev, NET_IDLE);
            if (xQueueReceive(toNet, &item, POLL) != pdTRUE || !live(item.session)) { continue; }
            if (item.uartData && item.rxEpoch != rxEpoch.load()) { continue; }
            size_t offset = 0;
            int64_t deadline = esp_timer_get_time() + static_cast<int64_t>(cfg.ioDeadlineMs) * 1000;
            while (offset < item.len && live(item.session)) {
                // Once partially sent, finish the frame to avoid leaving a lone IAC.
                if (!offset && item.uartData && item.rxEpoch != rxEpoch.load()) { break; }
                int n = send(socketFd.load(), item.data + offset, item.len - offset, 0);
                if (n > 0) { offset += n; tcpTx.fetch_add(n); continue; }
                if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
                    fault(FAULT_SOCKET);
                    break;
                }
                if (esp_timer_get_time() >= deadline) { fault(FAULT_SOCKET_TIMEOUT); break; }
                vTaskDelay(POLL);
            }
        }
        xEventGroupSetBits(ev, NET_TX_EXIT);
    }

    bool endSession(int fd)
    {
        active.store(false);
        shutdown(fd, SHUT_RDWR);
        xEventGroupClearBits(ev, ALL_IDLE);
        EventBits_t stopped = xEventGroupWaitBits(ev, ALL_IDLE, pdFALSE, pdTRUE,
                                                  pdMS_TO_TICKS(cfg.ioDeadlineMs + 500));
        if ((stopped & ALL_IDLE) != ALL_IDLE) {
            fault(FAULT_QUIESCE);
            log_e("Workers not quiescent; gateway disabled");
            return false;  // never close/reuse while an owner may still hold data
        }
        close(fd);
        socketFd.store(-1);
        rfc2217_server_session_end(parser);
        staging.len = 0;
        xQueueReset(toUart);  // queues never cross a session
        xQueueReset(toNet);
        clientIp.store(0);
        lastSessionEventMs.store(millis());
        log_i("Session %u closed, reason=%s", (unsigned)session.load(), kFaultNames[firstFault.load()]);
        return true;
    }

    int openListener()
    {
        int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (fd < 0) { return -1; }
        int on = 1;
        sockaddr_in addr = {};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(cfg.tcpPort);
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) ||
            fcntl(fd, F_SETFL, O_NONBLOCK) ||
            bind(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) || listen(fd, 1)) {
            close(fd);
            return -1;
        }
        return fd;
    }

    bool allowed(uint32_t ip) const
    {
        if (cfg.allowedIpCount == 0) { return true; }
        for (uint8_t i = 0; i < cfg.allowedIpCount; ++i) {
            if (static_cast<uint32_t>(cfg.allowedIps[i]) == ip) { return true; }
        }
        return false;
    }

    static bool wifiUp() { return WiFi.status() == WL_CONNECTED; }

    void rejectPending(int server)
    {
        sockaddr_in a; socklen_t l = sizeof(a);
        int extra;
        while ((extra = accept(server, reinterpret_cast<sockaddr *>(&a), &l)) >= 0) {
            shutdown(extra, SHUT_RDWR);
            close(extra);
            rejected.fetch_add(1);
            l = sizeof(a);
        }
    }

    void networkReader()
    {
        uint8_t input[BLOCK];
        bool healthy = true;
        while (!stopRequested.load() && healthy) {
            if (!wifiUp()) { vTaskDelay(pdMS_TO_TICKS(100)); continue; }
            int server = openListener();
            if (server < 0) {
                log_e("Listener on port %u failed", cfg.tcpPort);
                for (int i = 0; i < 30 && !stopRequested.load(); ++i) { vTaskDelay(pdMS_TO_TICKS(100)); }
                continue;
            }
            listening.store(true);
            log_i("RFC2217 gateway listening on port %u (quiet=%d)", cfg.tcpPort, cfg.quietMode);
            while (!stopRequested.load() && wifiUp()) {
                sockaddr_in peer = {};
                socklen_t peerLen = sizeof(peer);
                int fd = accept(server, reinterpret_cast<sockaddr *>(&peer), &peerLen);
                if (fd < 0) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
                uint32_t ip = peer.sin_addr.s_addr;
                int on = 1;
                if (!allowed(ip)) {
                    shutdown(fd, SHUT_RDWR); close(fd);
                    rejected.fetch_add(1);
                    continue;
                }
                if (fcntl(fd, F_SETFL, O_NONBLOCK) ||
                    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on))) {
                    close(fd);
                    continue;
                }
                socketFd.store(fd);
                uint32_t id = session.fetch_add(1) + 1;
                rxEpoch.store(1);
                firstFault.store(FAULT_NONE);
                negotiated.store(false);
                tcpRx.store(0); uartTx.store(0); uartRx.store(0); tcpTx.store(0);
                toUartPeak.store(0); toNetPeak.store(0); lineErrors.store(0);
                clientIp.store(ip);
                staging = UartItem{};
                staging.session = id; staging.kind = ITEM_DATA;
                touch();
                lastSessionEventMs.store(millis());
                active.store(true);
                UartItem start = {};
                start.session = id; start.kind = ITEM_START;
                if (xQueueSend(toUart, &start, 0) != pdTRUE || rfc2217_server_session_begin(parser)) {
                    fault(FAULT_PROTOCOL);
                }
                while (live(id)) {
                    rejectPending(server);  // single client: no queued second session
                    if (stopRequested.load()) { fault(FAULT_STOPPED); break; }
                    if (!wifiUp()) { fault(FAULT_WIFI); break; }
                    if (cfg.idleTimeoutMs && millis() - lastActivityMs.load() > cfg.idleTimeoutMs) {
                        fault(FAULT_IDLE);
                        break;
                    }
                    int n = recv(fd, input, sizeof(input), 0);
                    if (n > 0) {
                        tcpRx.fetch_add(n);
                        touch();
                        rfc2217_server_feed(parser, input, n);
                        flushStaging();
                    } else if (n == 0) {
                        active.store(false);
                    } else if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                        fault(FAULT_SOCKET);
                    } else {
                        vTaskDelay(POLL);
                    }
                }
                if (!endSession(fd)) { healthy = false; break; }
                rejectPending(server);
            }
            listening.store(false);
            close(server);
            if (!stopRequested.load() && healthy) { vTaskDelay(pdMS_TO_TICKS(1000)); }
        }
        if (!healthy) { errorState.store(true); }
        xEventGroupSetBits(ev, NET_RX_EXIT);
    }

    static void uartWriterTask(void *arg) { static_cast<Impl *>(arg)->uartWriter(); vTaskDelete(nullptr); }
    static void uartReaderTask(void *arg) { static_cast<Impl *>(arg)->uartReader(); vTaskDelete(nullptr); }
    static void netWriterTask(void *arg) { static_cast<Impl *>(arg)->networkWriter(); vTaskDelete(nullptr); }
    static void netReaderTask(void *arg) { static_cast<Impl *>(arg)->networkReader(); vTaskDelete(nullptr); }

    void freeResources()
    {
        if (parser) { rfc2217_server_destroy(parser); parser = nullptr; }
        if (toUart) { vQueueDelete(toUart); toUart = nullptr; }
        if (toNet) { vQueueDelete(toNet); toNet = nullptr; }
        if (uartLock) { vSemaphoreDelete(uartLock); uartLock = nullptr; }
        if (ev) { vEventGroupDelete(ev); ev = nullptr; }
    }

    void releaseTargetPins()
    {
        releaseOutput(cfg.resetPin, cfg.resetActiveLow);
        releaseOutput(cfg.bootPin, cfg.bootActiveLow);
        resetPending = bootPending = bootAsserted = false;
    }
};

// ---------------------------------------------------------------------------

SerialNetworkGateway::SerialNetworkGateway() : impl_(new Impl()) {}

SerialNetworkGateway::~SerialNetworkGateway()
{
    if (impl_->running) {
        if (stop(ExitReason::Requested) != Error::Ok) {
            return;  // workers may still reference impl_: leak instead of use-after-free
        }
    }
    delete impl_;
}

SerialNetworkGateway::Error SerialNetworkGateway::configure(const SerialNetworkGatewayConfig &c)
{
    if (impl_->running) { return Error::AlreadyRunning; }
    impl_->configured = false;
    if (c.uartNum < 1 || c.uartNum >= UART_NUM_MAX) { return Error::InvalidConfig; }
    if (!safePin(c.txPin, true) || !safePin(c.rxPin, false)) { return Error::InvalidConfig; }
    const int optional[] = {c.dtrPin, c.rtsPin, c.resetPin, c.bootPin};
    for (int pin : optional) {
        if (pin != -1 && !safePin(pin, true)) { return Error::InvalidConfig; }
    }
    const int all[] = {c.txPin, c.rxPin, c.dtrPin, c.rtsPin, c.resetPin, c.bootPin};
    for (unsigned i = 0; i < 6; ++i) {
        for (unsigned j = 0; j < i; ++j) {
            if (all[i] >= 0 && all[i] == all[j]) { return Error::InvalidConfig; }
        }
    }
    if (c.tcpPort == 0 || c.defaultBaud < MIN_BAUD || c.defaultBaud > MAX_BAUD ||
        c.queueDepth < 2 || c.queueDepth > 64 ||
        c.uartRxBufferSize < 256 || c.uartRxBufferSize > 65536 ||
        c.ioDeadlineMs < 100 || c.ioDeadlineMs > 10000 ||
        c.allowedIpCount > SerialNetworkGatewayConfig::kMaxAllowedIps) {
        return Error::InvalidConfig;
    }
    for (uint8_t i = 0; i < c.allowedIpCount; ++i) {
        if (static_cast<uint32_t>(c.allowedIps[i]) == 0) { return Error::InvalidConfig; }
    }
    impl_->cfg = c;
    impl_->configured = true;
    return Error::Ok;
}

SerialNetworkGateway::Error SerialNetworkGateway::begin()
{
    Impl &s = *impl_;
    if (!s.configured) { return Error::NotConfigured; }
    if (s.running) { return Error::AlreadyRunning; }
    if (s.errorState.load()) { return Error::NotQuiescent; }
    if (uart_is_driver_installed(s.port())) { return Error::UartInUse; }

    s.toUart = xQueueCreate(s.cfg.queueDepth, sizeof(UartItem));
    s.toNet = xQueueCreate(s.cfg.queueDepth, sizeof(NetItem));
    s.uartLock = xSemaphoreCreateMutex();
    s.ev = xEventGroupCreate();
    rfc2217_server_config_t pc = {};
    pc.ctx = &s;
    pc.on_client_connected = Impl::onNegotiated;
    pc.on_data_received = Impl::onData;
    pc.on_write = Impl::onWire;
    pc.on_command = Impl::onCommand;
    pc.on_protocol_error = Impl::onProtocol;
    pc.port = s.cfg.tcpPort;
    pc.quiet = s.cfg.quietMode;
    if (!s.toUart || !s.toNet || !s.uartLock || !s.ev || rfc2217_server_create(&pc, &s.parser)) {
        s.freeResources();
        return Error::NoMemory;
    }

    s.stopRequested.store(false);
    s.active.store(false);
    s.listening.store(false);
    s.firstFault.store(FAULT_NONE);
    s.clientIp.store(0);
    s.baud.store(s.cfg.defaultBaud);
    s.bits.store(8); s.parity.store(1); s.stops.store(1);
    s.rejected.store(0);

    struct TaskDef { TaskFunction_t fn; const char *name; uint32_t stack; UBaseType_t prio; EventBits_t exitBit; };
    const TaskDef defs[] = {
        {Impl::uartWriterTask, "sng_uart_tx", 4096, 10, UART_TX_EXIT},
        {Impl::uartReaderTask, "sng_uart_rx", 4096, 11, UART_RX_EXIT},
        {Impl::netWriterTask, "sng_tcp_tx", 4096, 9, NET_TX_EXIT},
        {Impl::netReaderTask, "sng_tcp_rx", 6144, 9, NET_RX_EXIT},
    };
    EventBits_t created = 0;
    for (const TaskDef &d : defs) {
        if (xTaskCreate(d.fn, d.name, d.stack, &s, d.prio, nullptr) != pdPASS) { break; }
        created |= d.exitBit;
    }
    if (created != ALL_EXIT) {
        s.stopRequested.store(true);
        xEventGroupSetBits(s.ev, ALL_EXIT & ~created);  // pretend absent tasks exited
        EventBits_t bits = xEventGroupWaitBits(s.ev, ALL_EXIT, pdFALSE, pdTRUE, pdMS_TO_TICKS(1000));
        if ((bits & ALL_EXIT) == ALL_EXIT) { s.freeResources(); } else { s.errorState.store(true); }
        return Error::TaskCreateFailed;
    }

    s.prevWifiSleep = WiFi.getSleep();
    s.resetPending = s.bootPending = s.bootAsserted = false;
    if (!driveOutput(s.cfg.resetPin, false, s.cfg.resetActiveLow) ||
        !driveOutput(s.cfg.bootPin, false, s.cfg.bootActiveLow)) {
        s.running = true;
        ExitCallback saved = std::move(s.onExit);  // never entered: no onExit
        stop(ExitReason::Requested);
        s.onExit = std::move(saved);
        return Error::InvalidConfig;
    }
    WiFi.setSleep(false);
    s.lastSessionEventMs.store(millis());
    s.running = true;
    if (s.onEnter) { s.onEnter(); }
    return Error::Ok;
}

SerialNetworkGateway::Error SerialNetworkGateway::stop(ExitReason reason)
{
    Impl &s = *impl_;
    if (!s.running) { return Error::NotRunning; }
    s.stopRequested.store(true);
    EventBits_t bits = xEventGroupWaitBits(s.ev, ALL_EXIT, pdFALSE, pdTRUE,
                                           pdMS_TO_TICKS(s.cfg.ioDeadlineMs + 1500));
    s.releaseTargetPins();
    WiFi.setSleep(s.prevWifiSleep);
    if ((bits & ALL_EXIT) != ALL_EXIT || s.errorState.load()) {
        // Keep queues/parser alive: a stuck worker may still use them.
        s.errorState.store(true);
        s.running = false;
        return Error::NotQuiescent;
    }
    s.freeResources();
    s.running = false;
    if (s.onExit) { s.onExit(reason); }
    return Error::Ok;
}

SerialNetworkGateway::Error SerialNetworkGateway::end() { return stop(ExitReason::Requested); }

void SerialNetworkGateway::loop()
{
    Impl &s = *impl_;
    if (!s.running) { return; }
    uint32_t now = millis();
    if (s.resetPending && timeReached(now, s.resetReleaseAt)) {
        setLevel(s.cfg.resetPin, false, s.cfg.resetActiveLow);
        s.resetPending = false;
    }
    if (s.bootPending && !s.resetPending && timeReached(now, s.bootReleaseAt)) {
        setLevel(s.cfg.bootPin, false, s.cfg.bootActiveLow);
        s.bootPending = false;
        s.bootAsserted = false;
    }
    if (s.errorState.load()) {
        stop(ExitReason::Requested);  // reports NotQuiescent, frees nothing
        return;
    }
    if (s.cfg.maintenanceTimeoutMs && !s.active.load() &&
        now - s.lastSessionEventMs.load() > s.cfg.maintenanceTimeoutMs) {
        stop(ExitReason::MaintenanceTimeout);
    }
}

bool SerialNetworkGateway::isRunning() const { return impl_->running; }

void SerialNetworkGateway::onEnter(EnterCallback callback) { impl_->onEnter = std::move(callback); }
void SerialNetworkGateway::onExit(ExitCallback callback) { impl_->onExit = std::move(callback); }

SerialNetworkGateway::Error SerialNetworkGateway::resetTarget(uint32_t pulseMs)
{
    Impl &s = *impl_;
    if (!s.running) { return Error::NotRunning; }
    if (s.cfg.resetPin < 0) { return Error::PinNotConfigured; }
    if (pulseMs < 1 || pulseMs > 10000) { return Error::InvalidArgument; }
    if (s.resetPending) { return Error::Busy; }
    if (!setLevel(s.cfg.resetPin, true, s.cfg.resetActiveLow)) { return Error::InvalidConfig; }
    s.resetReleaseAt = millis() + pulseMs;
    s.resetPending = true;
    return Error::Ok;
}

SerialNetworkGateway::Error SerialNetworkGateway::setBootPin(bool asserted)
{
    Impl &s = *impl_;
    if (!s.running) { return Error::NotRunning; }
    if (s.cfg.bootPin < 0) { return Error::PinNotConfigured; }
    if (s.bootPending) { return Error::Busy; }
    if (!setLevel(s.cfg.bootPin, asserted, s.cfg.bootActiveLow)) { return Error::InvalidConfig; }
    s.bootAsserted = asserted;
    return Error::Ok;
}

SerialNetworkGateway::Error SerialNetworkGateway::enterDownloadMode(uint32_t resetMs, uint32_t bootHoldMs)
{
    Impl &s = *impl_;
    if (!s.running) { return Error::NotRunning; }
    if (s.cfg.resetPin < 0 || s.cfg.bootPin < 0) { return Error::PinNotConfigured; }
    if (resetMs < 1 || resetMs > 10000 || bootHoldMs > 10000) { return Error::InvalidArgument; }
    if (s.resetPending || s.bootPending) { return Error::Busy; }
    if (!setLevel(s.cfg.bootPin, true, s.cfg.bootActiveLow) ||
        !setLevel(s.cfg.resetPin, true, s.cfg.resetActiveLow)) {
        return Error::InvalidConfig;
    }
    uint32_t now = millis();
    s.resetReleaseAt = now + resetMs;
    s.bootReleaseAt = now + resetMs + bootHoldMs;
    s.resetPending = s.bootPending = s.bootAsserted = true;
    return Error::Ok;
}

SerialNetworkGatewayStatus SerialNetworkGateway::status() const
{
    const Impl &s = *impl_;
    SerialNetworkGatewayStatus st;
    if (s.errorState.load()) { st.state = SerialNetworkGatewayState::Error; }
    else if (!s.running) { st.state = SerialNetworkGatewayState::Disabled; }
    else if (s.active.load()) { st.state = SerialNetworkGatewayState::Connected; }
    else if (s.listening.load()) { st.state = SerialNetworkGatewayState::Listening; }
    else { st.state = SerialNetworkGatewayState::WaitingWifi; }
    static const char *const names[] = {"disabled", "waiting_wifi", "listening", "connected", "error"};
    st.stateName = names[static_cast<uint8_t>(st.state)];
    st.clientIp = IPAddress(s.clientIp.load());
    st.session = s.session.load();
    st.tcpRxBytes = s.tcpRx.load();
    st.uartTxBytes = s.uartTx.load();
    st.uartRxBytes = s.uartRx.load();
    st.tcpTxBytes = s.tcpTx.load();
    st.queueDepth = s.cfg.queueDepth;
    st.toUartQueuePeak = s.toUartPeak.load();
    st.toNetQueuePeak = s.toNetPeak.load();
    int ff = s.firstFault.load(), lf = s.lastFault.load();
    st.sessionFault = kFaultNames[(ff >= 0 && ff < FAULT_COUNT) ? ff : 0];
    st.lastFault = kFaultNames[(lf >= 0 && lf < FAULT_COUNT) ? lf : 0];
    st.rejectedConnections = s.rejected.load();
    st.lineErrors = s.lineErrors.load();
    st.baud = s.baud.load();
    st.dataBits = s.bits.load();
    uint8_t p = s.parity.load();
    st.parity = p == 2 ? 'O' : p == 3 ? 'E' : 'N';
    uint8_t sb = s.stops.load();
    st.stopBits = sb == 2 ? "2" : sb == 3 ? "1.5" : "1";
    st.dtr = s.dtr.load();
    st.rts = s.rts.load();
    st.breakOn = s.breakOn.load();
    st.ignoredControls = s.unsupportedControls.load();
    st.quietMode = s.cfg.quietMode;
    st.tcpPort = s.cfg.tcpPort;
    st.maintenanceRemainingMs = 0;
    if (s.running && s.cfg.maintenanceTimeoutMs && !s.active.load()) {
        uint32_t elapsed = millis() - s.lastSessionEventMs.load();
        st.maintenanceRemainingMs = elapsed < s.cfg.maintenanceTimeoutMs ? s.cfg.maintenanceTimeoutMs - elapsed : 0;
    }
    return st;
}

String SerialNetworkGateway::statusJson() const
{
    SerialNetworkGatewayStatus st = status();
    char ip[16] = "";
    if (static_cast<uint32_t>(st.clientIp) != 0) {
        snprintf(ip, sizeof(ip), "%u.%u.%u.%u", st.clientIp[0], st.clientIp[1], st.clientIp[2], st.clientIp[3]);
    }
    char buf[768];
    snprintf(buf, sizeof(buf),
             "{\"state\":\"%s\",\"client\":\"%s\",\"session\":%u,"
             "\"bytes\":{\"tcpRx\":%u,\"uartTx\":%u,\"uartRx\":%u,\"tcpTx\":%u},"
             "\"queues\":{\"depth\":%u,\"toUartPeak\":%u,\"toNetPeak\":%u},"
             "\"sessionFault\":\"%s\",\"lastFault\":\"%s\",\"rejected\":%u,\"lineErrors\":%u,"
             "\"serial\":{\"baud\":%u,\"dataBits\":%u,\"parity\":\"%c\",\"stopBits\":\"%s\","
             "\"dtr\":%s,\"rts\":%s,\"break\":%s},\"ignoredControls\":%u,"
             "\"quiet\":%s,\"port\":%u,\"maintenanceRemainingMs\":%u}",
             st.stateName, ip, (unsigned)st.session,
             (unsigned)st.tcpRxBytes, (unsigned)st.uartTxBytes, (unsigned)st.uartRxBytes, (unsigned)st.tcpTxBytes,
             (unsigned)st.queueDepth, (unsigned)st.toUartQueuePeak, (unsigned)st.toNetQueuePeak,
             st.sessionFault, st.lastFault, (unsigned)st.rejectedConnections, (unsigned)st.lineErrors,
             (unsigned)st.baud, (unsigned)st.dataBits, st.parity, st.stopBits,
             st.dtr ? "true" : "false", st.rts ? "true" : "false", st.breakOn ? "true" : "false",
             (unsigned)st.ignoredControls,
             st.quietMode ? "true" : "false", (unsigned)st.tcpPort, (unsigned)st.maintenanceRemainingMs);
    return String(buf);
}

const char *SerialNetworkGateway::errorName(Error error)
{
    switch (error) {
    case Error::Ok: return "ok";
    case Error::NotConfigured: return "not_configured";
    case Error::InvalidConfig: return "invalid_config";
    case Error::AlreadyRunning: return "already_running";
    case Error::NotRunning: return "not_running";
    case Error::UartInUse: return "uart_in_use";
    case Error::NoMemory: return "no_memory";
    case Error::TaskCreateFailed: return "task_create_failed";
    case Error::NotQuiescent: return "not_quiescent";
    case Error::PinNotConfigured: return "pin_not_configured";
    case Error::Busy: return "busy";
    case Error::InvalidArgument: return "invalid_argument";
    }
    return "unknown";
}
