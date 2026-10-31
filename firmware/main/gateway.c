// SPDX-License-Identifier: Apache-2.0
// Experimental ESP32-S3 gateway. No flashing, OTA, reset sequences or device profiles.
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <inttypes.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/event_groups.h"
#include "rfc2217_server.h"

bool gateway_wifi_start(void);
bool gateway_wifi_ready(void);
static const char *TAG = "gateway";
#define UART_PORT UART_NUM_1
#define BLOCK 256
#define UART_IDLE BIT0
#define RX_IDLE BIT1
#define NET_IDLE BIT2
#define ALL_IDLE (UART_IDLE | RX_IDLE | NET_IDLE)
#define POLL pdMS_TO_TICKS(2)
#define DEADLINE_US ((int64_t)CONFIG_GW_IO_DEADLINE_MS * 1000)

/* Queue 1 serializes DATA and control changes. Queue 2 holds complete wire frames.
 * These are the only application payload queues; the UART RX driver is also bounded.
 * Parser callbacks never touch UART, sockets, a waitable mutex or a blocking queue.
 */
typedef enum { ITEM_START, ITEM_DATA, ITEM_COMMAND } item_kind_t;
typedef struct {
    uint32_t session;
    uint16_t len;
    uint8_t kind, command;
    uint8_t data[BLOCK];
} uart_item_t;
typedef struct {
    uint32_t session, rx_epoch;
    uint16_t len;
    bool uart_data;
    uint8_t data[BLOCK * 2];
} net_item_t;
typedef enum {
    FAULT_NONE, FAULT_INPUT_FULL, FAULT_OUTPUT_FULL, FAULT_PROTOCOL,
    FAULT_UNSUPPORTED, FAULT_UART, FAULT_UART_OVERFLOW, FAULT_UART_LINE,
    FAULT_UART_TIMEOUT, FAULT_SOCKET, FAULT_SOCKET_TIMEOUT, FAULT_WIFI,
    FAULT_QUIESCE, FAULT_NOT_NEGOTIATED
} fault_t;
static const char *const fault_names[] = {
    "none", "network_to_uart_full", "uart_to_network_full", "malformed_telnet",
    "unsupported_request", "uart_driver", "uart_rx_overflow", "uart_line_error",
    "uart_tx_deadline", "socket_io", "socket_tx_deadline", "wifi_disconnected",
    "shutdown_not_quiescent", "rfc2217_not_negotiated"
};
static QueueHandle_t to_uart, to_net, uart_events;
static SemaphoreHandle_t uart_lock;
static EventGroupHandle_t idle;
static rfc2217_server_t parser;
static atomic_bool active, negotiated, uart_ready;
static atomic_uint session, rx_epoch;
static atomic_int socket_fd = ATOMIC_VAR_INIT(-1);
static atomic_int first_fault;
static int last_session_fault; /* Retained across reconnect; only network-RX owns it. */
static uint32_t baud;
static uint8_t bits, parity, stops;
static bool dtr, rts;

static void fault(fault_t reason)
{
    int expected = FAULT_NONE;
    atomic_compare_exchange_strong(&first_fault, &expected, reason);
    atomic_store(&active, false);
}

static bool live(uint32_t id)
{
    return atomic_load(&active) && atomic_load(&session) == id;
}

static bool safe_pin(int pin, bool output)
{
    /* Conservative public S3 policy also excludes N16R8 flash/OPI pins, USB,
     * console, straps and common onboard LED pins. No inferred ASR mapping. */
    if (pin < 0 || (output ? !GPIO_IS_VALID_OUTPUT_GPIO(pin) : !GPIO_IS_VALID_GPIO(pin))) {
        return false;
    }
    return !((pin >= 22 && pin <= 37) || pin == 0 || pin == 3 ||
             pin == 19 || pin == 20 || pin == 43 || pin == 44 ||
             pin == 45 || pin == 46 || pin == 48);
}

static bool pins_valid(void)
{
#ifndef CONFIG_GW_UART_PINS_CONFIRMED
    return false;
#else
    const int pins[] = {CONFIG_GW_TX_PIN, CONFIG_GW_RX_PIN, CONFIG_GW_DTR_PIN, CONFIG_GW_RTS_PIN};
    for (unsigned i = 0; i < 4; ++i) {
        if (i >= 2 && pins[i] == -1) { continue; }
        if (!safe_pin(pins[i], i != 1)) { return false; }
        for (unsigned j = 0; j < i; ++j) {
            if (pins[i] == pins[j]) { return false; }
        }
    }
    return true;
#endif
}

static int output_level(bool asserted, bool invert)
{
    return asserted ^ invert;
}

static bool dtr_invert(void)
{
#ifdef CONFIG_GW_DTR_ACTIVE_LOW
    return true;
#else
    return false;
#endif
}
static bool rts_invert(void)
{
#ifdef CONFIG_GW_RTS_ACTIVE_LOW
    return true;
#else
    return false;
#endif
}

static void detach_pins(void)
{
    const int pins[] = {CONFIG_GW_TX_PIN, CONFIG_GW_RX_PIN, CONFIG_GW_DTR_PIN, CONFIG_GW_RTS_PIN};
    for (unsigned i = 0; i < 4; ++i) {
        if (pins[i] >= 0) { gpio_reset_pin(pins[i]); }
    }
}

static bool output_init(int pin, bool invert)
{
    if (pin < 0) { return true; }
    /* Set the inactive latch BEFORE connecting output. */
    return gpio_set_level(pin, output_level(false, invert)) == ESP_OK &&
           gpio_set_direction(pin, GPIO_MODE_OUTPUT) == ESP_OK;
}

static bool uart_open(void)
{
    baud = CONFIG_GW_INITIAL_BAUD;
    bits = 8; parity = 1; stops = 1; dtr = false; rts = false;
    uart_config_t config = {
        .baud_rate = baud, .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE, .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    /* TX software ring buffer = 0. Only this task calls TX APIs, so the driver's
     * tx_mux in uart_tx_chars cannot contend with another writer. It only fills
     * available FIFO space; it does not wait for wire completion. */
    if (uart_driver_install(UART_PORT, 4096, 0, 64, &uart_events, 0) != ESP_OK ||
        uart_param_config(UART_PORT, &config) != ESP_OK ||
        !output_init(CONFIG_GW_DTR_PIN, dtr_invert()) ||
        !output_init(CONFIG_GW_RTS_PIN, rts_invert()) ||
        uart_set_pin(UART_PORT, CONFIG_GW_TX_PIN, CONFIG_GW_RX_PIN,
                     UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK ||
        uart_flush_input(UART_PORT) != ESP_OK) {
        return false;
    }
    xQueueReset(uart_events);
    atomic_store(&uart_ready, true);
    return true;
}

static void uart_close(void)
{
    /* Called only by UART owner; RX uses the same short mutex around zero-wait
     * reads. Flush is RX-only, never described as aborting TX. Pin detach stops
     * future driven bits; a byte already on the wire cannot be recalled. */
    atomic_store(&uart_ready, false);
    if (xSemaphoreTake(uart_lock, pdMS_TO_TICKS(100)) != pdTRUE) {
        fault(FAULT_QUIESCE);
        return;
    }
    detach_pins();
    if (uart_is_driver_installed(UART_PORT)) {
        uart_flush_input(UART_PORT);
        uart_driver_delete(UART_PORT);
    }
    uart_events = NULL;
    xSemaphoreGive(uart_lock);
}

static bool enqueue_wire(const uint8_t *data, size_t len, bool is_uart, uint32_t epoch)
{
    uint32_t id = atomic_load(&session);
    if (!live(id)) { return false; }
    if (len > BLOCK * 2) { fault(FAULT_PROTOCOL); return false; }
    net_item_t item = {.session = id, .rx_epoch = epoch, .len = len, .uart_data = is_uart};
    memcpy(item.data, data, len);
    if (xQueueSend(to_net, &item, 0) != pdTRUE) {
        fault(FAULT_OUTPUT_FULL);
        return false;
    }
    return true;
}

static int on_wire(void *ctx, const uint8_t *data, size_t len)
{
    (void)ctx;
    return enqueue_wire(data, len, false, 0) ? 0 : -1;
}
static void on_protocol(void *ctx) { (void)ctx; fault(FAULT_PROTOCOL); }
static void on_negotiated(void *ctx) { (void)ctx; atomic_store(&negotiated, true); }

/* One small staging block belongs exclusively to network-RX. It batches the
 * upstream parser's byte callbacks so a binary 0xff stream cannot exhaust the
 * record queue merely by fragmenting into one-byte records. Flush before each
 * control preserves DATA/control ordering. */
static uart_item_t staging;
static bool flush_staging(void)
{
    if (!staging.len) { return true; }
    if (!live(staging.session)) { staging.len = 0; return false; }
    bool ok = xQueueSend(to_uart, &staging, 0) == pdTRUE;
    staging.len = 0;
    if (!ok) { fault(FAULT_INPUT_FULL); }
    return ok;
}
static void on_data(void *ctx, const uint8_t *data, size_t len)
{
    (void)ctx;
    if (!atomic_load(&active)) { return; }
    if (!atomic_load(&negotiated)) { fault(FAULT_NOT_NEGOTIATED); return; }
    while (len && atomic_load(&active)) {
        size_t n = BLOCK - staging.len;
        if (n > len) { n = len; }
        memcpy(staging.data + staging.len, data, n);
        staging.len += n; data += n; len -= n;
        if (staging.len == BLOCK && !flush_staging()) { return; }
    }
}
static void on_command(void *ctx, uint8_t cmd, const uint8_t *data, size_t len)
{
    (void)ctx;
    if (!atomic_load(&active)) { return; }
    if (!atomic_load(&negotiated)) { fault(FAULT_NOT_NEGOTIATED); return; }
    if (!flush_staging()) { return; }
    uart_item_t item = {.session = atomic_load(&session), .kind = ITEM_COMMAND,
                        .command = cmd, .len = len};
    memcpy(item.data, data, len);
    if (xQueueSend(to_uart, &item, 0) != pdTRUE) { fault(FAULT_INPUT_FULL); }
}

static bool wait_uart_idle(uint32_t id)
{
    int64_t deadline = esp_timer_get_time() + DEADLINE_US;
    while (live(id)) {
        if (uart_wait_tx_done(UART_PORT, 0) == ESP_OK) { return true; }
        if (esp_timer_get_time() >= deadline) { fault(FAULT_UART_TIMEOUT); return false; }
        vTaskDelay(POLL);
    }
    return false;
}

static void command(const uart_item_t *item)
{
    uint8_t cmd = item->command, value = item->len ? item->data[0] : 0;
    uint8_t reply[4] = {0};
    size_t reply_len = 1;
    esp_err_t err = ESP_OK;
    bool supported = true;
    /* A command never changes an in-flight character's format. RX has its own
     * task and remains serviced throughout this bounded TX-idle wait. */
    if (cmd != 12 && !wait_uart_idle(item->session)) { return; }
    if (xSemaphoreTake(uart_lock, pdMS_TO_TICKS(20)) != pdTRUE) { fault(FAULT_UART); return; }
    if (!live(item->session)) { xSemaphoreGive(uart_lock); return; }
    switch (cmd) {
    case 1: {
        uint32_t requested = ((uint32_t)item->data[0] << 24) | ((uint32_t)item->data[1] << 16) |
                             ((uint32_t)item->data[2] << 8) | item->data[3];
        if (requested && (requested < 300 || requested > 2000000)) { supported = false; break; }
        if (requested) { err = uart_set_baudrate(UART_PORT, requested); }
        /* RFC2217 reports the accepted nominal setting, not divisor rounding. */
        if (err == ESP_OK && requested) { baud = requested; }
        reply[0] = baud >> 24; reply[1] = baud >> 16; reply[2] = baud >> 8; reply[3] = baud;
        reply_len = 4;
        break;
    }
    case 2:
        if (value && (value < 5 || value > 8)) { supported = false; break; }
        if (value) { err = uart_set_word_length(UART_PORT, (uart_word_length_t)(value - 5)); }
        if (err == ESP_OK && value) { bits = value; }
        reply[0] = bits;
        break;
    case 3:
        if (value > 3) { supported = false; break; } /* M/S parity unsupported */
        if (value) {
            err = uart_set_parity(UART_PORT, value == 1 ? UART_PARITY_DISABLE :
                                  value == 2 ? UART_PARITY_ODD : UART_PARITY_EVEN);
        }
        if (err == ESP_OK && value) { parity = value; }
        reply[0] = parity;
        break;
    case 4:
        if (value > 3) { supported = false; break; }
        if (value) {
            err = uart_set_stop_bits(UART_PORT, value == 1 ? UART_STOP_BITS_1 :
                                     value == 2 ? UART_STOP_BITS_2 : UART_STOP_BITS_1_5);
        }
        if (err == ESP_OK && value) { stops = value; }
        reply[0] = stops;
        break;
    case 5:
        switch (value) {
        case 0: case 1: reply[0] = 1; break; /* no outbound flow control */
        case 13: case 14: reply[0] = 14; break; /* no inbound flow control */
        case 7: case 8: case 9:
            if (CONFIG_GW_DTR_PIN < 0) { supported = false; break; }
            if (value != 7) {
                bool next = value == 8;
                err = gpio_set_level(CONFIG_GW_DTR_PIN, output_level(next, dtr_invert()));
                if (err == ESP_OK) { dtr = next; }
            }
            reply[0] = dtr ? 8 : 9;
            break;
        case 10: case 11: case 12:
            if (CONFIG_GW_RTS_PIN < 0) { supported = false; break; }
            if (value != 10) {
                bool next = value == 11;
                err = gpio_set_level(CONFIG_GW_RTS_PIN, output_level(next, rts_invert()));
                if (err == ESP_OK) { rts = next; }
            }
            reply[0] = rts ? 11 : 12;
            break;
        default: supported = false; break; /* BREAK and all other flow modes */
        }
        break;
    case 10: case 11:
        /* No hardware line/modem input monitoring. Only mask=0 is truthful. */
        supported = value == 0;
        reply[0] = 0;
        break;
    case 12:
        if (value < 1 || value > 3) { supported = false; break; }
        /* TX purge is an EMPTY barrier, not an abort. All earlier software
         * records are consumed in-order. If hardware is busy, fail the session
         * instead of pretending that uart_flush_input() can abort TX. */
        if ((value & 2) && uart_wait_tx_done(UART_PORT, 0) != ESP_OK) {
            supported = false;
            break;
        }
        if (value & 1) {
            atomic_fetch_add(&rx_epoch, 1); /* invalidate unsent RX records */
            err = uart_flush_input(UART_PORT);
            xQueueReset(uart_events);
        }
        reply[0] = value;
        break;
    default: supported = false; break; /* 6/7 status, 8/9 suspend/resume */
    }
    xSemaphoreGive(uart_lock);
    if (!supported) {
        ESP_LOGW(TAG, "Unsupported RFC2217 command=%u value=%u / 拒绝请求", cmd, value);
        fault(FAULT_UNSUPPORTED); /* RFC2217 has no generic NAK: close, no ACK. */
    } else if (err != ESP_OK) {
        fault(FAULT_UART);
    } else if (live(item->session)) {
        rfc2217_server_reply(parser, cmd + 100, reply, reply_len);
    }
}

static void uart_writer(void *arg)
{
    (void)arg;
    uart_item_t item;
    for (;;) {
        if (!atomic_load(&active)) {
            if (uart_is_driver_installed(UART_PORT)) { uart_close(); }
            if (!uart_is_driver_installed(UART_PORT)) { xEventGroupSetBits(idle, UART_IDLE); }
            vTaskDelay(POLL);
            continue;
        }
        xEventGroupClearBits(idle, UART_IDLE);
        if (xQueueReceive(to_uart, &item, POLL) != pdTRUE || !live(item.session)) { continue; }
        if (item.kind == ITEM_START) {
            if (!uart_open()) { fault(FAULT_UART); }
        } else if (!atomic_load(&uart_ready)) {
            fault(FAULT_UART);
        } else if (item.kind == ITEM_COMMAND) {
            command(&item);
        } else {
            size_t offset = 0;
            int64_t deadline = esp_timer_get_time() + DEADLINE_US;
            while (offset < item.len && live(item.session)) {
                int n = uart_tx_chars(UART_PORT, (const char *)item.data + offset, item.len - offset);
                if (n < 0) { fault(FAULT_UART); break; }
                offset += n;
                if (offset < item.len) {
                    if (esp_timer_get_time() >= deadline) { fault(FAULT_UART_TIMEOUT); break; }
                    vTaskDelay(POLL);
                }
            }
        }
    }
}

static void uart_reader(void *arg)
{
    (void)arg;
    uint8_t raw[BLOCK], escaped[BLOCK * 2];
    uart_event_t event;
    for (;;) {
        if (!atomic_load(&active)) {
            xEventGroupSetBits(idle, RX_IDLE);
            vTaskDelay(POLL);
            continue;
        }
        xEventGroupClearBits(idle, RX_IDLE);
        if (!atomic_load(&uart_ready)) { vTaskDelay(POLL); continue; }
        uint32_t id = atomic_load(&session);
        int n = 0;
        uint32_t epoch = 0;
        if (xSemaphoreTake(uart_lock, POLL) == pdTRUE) {
            if (live(id) && atomic_load(&uart_ready)) {
                epoch = atomic_load(&rx_epoch);
                /* A full event queue could lose the ISR overflow notification. */
                if (uxQueueSpacesAvailable(uart_events) == 0) { fault(FAULT_UART_OVERFLOW); }
                for (unsigned count = 0; count < 64 && xQueueReceive(uart_events, &event, 0) == pdTRUE; ++count) {
                    if (event.type == UART_FIFO_OVF || event.type == UART_BUFFER_FULL) {
                        fault(FAULT_UART_OVERFLOW); break;
                    }
                    if (event.type == UART_PARITY_ERR || event.type == UART_FRAME_ERR || event.type == UART_BREAK) {
                        fault(FAULT_UART_LINE); break;
                    }
                }
                if (live(id)) { n = uart_read_bytes(UART_PORT, raw, sizeof(raw), 0); }
            }
            xSemaphoreGive(uart_lock);
        }
        if (n < 0) { fault(FAULT_UART); }
        if (n > 0 && live(id)) {
            size_t out = 0;
            for (int i = 0; i < n; ++i) {
                escaped[out++] = raw[i];
                if (raw[i] == 255) { escaped[out++] = 255; }
            }
            enqueue_wire(escaped, out, true, epoch);
        }
        vTaskDelay(POLL);
    }
}

static void network_writer(void *arg)
{
    (void)arg;
    net_item_t item;
    for (;;) {
        if (!atomic_load(&active)) {
            xEventGroupSetBits(idle, NET_IDLE);
            vTaskDelay(POLL);
            continue;
        }
        xEventGroupClearBits(idle, NET_IDLE);
        if (xQueueReceive(to_net, &item, POLL) != pdTRUE || !live(item.session)) { continue; }
        if (item.uart_data && item.rx_epoch != atomic_load(&rx_epoch)) { continue; }
        size_t offset = 0;
        int64_t deadline = esp_timer_get_time() + DEADLINE_US;
        while (offset < item.len && live(item.session)) {
            /* Once partially sent, finish this escaped frame to avoid leaving
             * a lone IAC. A purge cannot retract TCP bytes already submitted. */
            if (!offset && item.uart_data && item.rx_epoch != atomic_load(&rx_epoch)) { break; }
            int n = send(atomic_load(&socket_fd), item.data + offset, item.len - offset, 0);
            if (n > 0) { offset += n; continue; }
            if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
                fault(FAULT_SOCKET); break;
            }
            if (esp_timer_get_time() >= deadline) { fault(FAULT_SOCKET_TIMEOUT); break; }
            vTaskDelay(POLL);
        }
    }
}

static bool end_session(int fd)
{
    atomic_store(&active, false);
    shutdown(fd, SHUT_RDWR); /* Wakes TCP; close waits for our sole writer. */
    xEventGroupClearBits(idle, ALL_IDLE);
    EventBits_t stopped = xEventGroupWaitBits(idle, ALL_IDLE, pdFALSE, pdTRUE,
                                             pdMS_TO_TICKS(CONFIG_GW_IO_DEADLINE_MS + 500));
    if ((stopped & ALL_IDLE) != ALL_IDLE) {
        fault(FAULT_QUIESCE);
        ESP_LOGE(TAG, "Workers not quiescent; reconnect disabled / 任务未停止，禁止重连");
        return false; /* no close/reuse/reset while an owner may still hold data */
    }
    close(fd);
    atomic_store(&socket_fd, -1);
    rfc2217_server_session_end(parser);
    staging.len = 0;
    xQueueReset(to_uart);
    xQueueReset(to_net);
    int current = atomic_load(&first_fault);
    if (current != FAULT_NONE) { last_session_fault = current; }
    ESP_LOGI(TAG, "Session=%" PRIu32 " closed reason=%s retained_error=%s / 会话已关闭",
             (uint32_t)atomic_load(&session), fault_names[current], fault_names[last_session_fault]);
    return true;
}

static int listener(void)
{
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) { return -1; }
    int on = 1;
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons(CONFIG_GW_PORT),
                                  .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) ||
        fcntl(fd, F_SETFL, O_NONBLOCK) ||
        bind(fd, (struct sockaddr *)&address, sizeof(address)) || listen(fd, 1)) {
        close(fd); return -1;
    }
    return fd;
}

static void network_reader(void *arg)
{
    (void)arg;
    uint8_t input[BLOCK];
    for (;;) {
        while (!gateway_wifi_ready()) { vTaskDelay(pdMS_TO_TICKS(100)); }
        int server = listener();
        if (server < 0) { ESP_LOGE(TAG, "Listener failed / 监听失败"); vTaskDelay(pdMS_TO_TICKS(3000)); continue; }
        ESP_LOGI(TAG, "Experimental RFC2217 ready, port=%d / 实验性服务就绪", CONFIG_GW_PORT);
        while (gateway_wifi_ready()) {
            int fd = accept(server, NULL, NULL);
            if (fd < 0) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
            int on = 1;
            if (fcntl(fd, F_SETFL, O_NONBLOCK) ||
                setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on))) { close(fd); continue; }
            atomic_store(&socket_fd, fd);
            uint32_t id = atomic_fetch_add(&session, 1) + 1;
            atomic_store(&rx_epoch, 1);
            atomic_store(&first_fault, FAULT_NONE);
            atomic_store(&negotiated, false);
            staging = (uart_item_t){.session = id, .kind = ITEM_DATA};
            atomic_store(&active, true);
            uart_item_t start = {.session = id, .kind = ITEM_START};
            if (xQueueSend(to_uart, &start, 0) != pdTRUE || rfc2217_server_session_begin(parser)) {
                fault(FAULT_PROTOCOL);
            }
            while (live(id)) {
                /* Explicitly reject extra clients; no delayed/queued session. */
                int extra = accept(server, NULL, NULL);
                if (extra >= 0) { shutdown(extra, SHUT_RDWR); close(extra); }
                if (!gateway_wifi_ready()) { fault(FAULT_WIFI); break; }
                int n = recv(fd, input, sizeof(input), 0);
                if (n > 0) {
                    rfc2217_server_feed(parser, input, n);
                    flush_staging();
                } else if (n == 0) {
                    atomic_store(&active, false);
                } else if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                    fault(FAULT_SOCKET);
                } else {
                    vTaskDelay(POLL);
                }
            }
            if (!end_session(fd)) {
                close(server);
                vTaskDelete(NULL);
            }
            /* Reject pending connections across the boundary as well. */
            int extra;
            while ((extra = accept(server, NULL, NULL)) >= 0) { close(extra); }
        }
        close(server);
        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}

void app_main(void)
{
#ifdef CONFIG_GW_COMPILE_CHECK_ONLY
    /* Volatile read retains real driver/network linkage for build verification.
     * Never changed by firmware: this artifact always returns before init. */
    static const volatile bool compile_only = true;
    if (compile_only) {
        ESP_LOGW(TAG, "Compile fixture: permanently idle / 编译检查固件永久停用");
        return;
    }
#endif
    ESP_LOGI(TAG, "Experimental serial gateway / 实验性串口网关; console is separate from UART1");
    if (!pins_valid()) {
        ESP_LOGW(TAG, "UART disabled: confirm safe pins in menuconfig / UART 未启用，须确认安全引脚");
        return;
    }
    if (!gateway_wifi_start()) { return; }
    to_uart = xQueueCreate(CONFIG_GW_QUEUE_BLOCKS, sizeof(uart_item_t));
    to_net = xQueueCreate(CONFIG_GW_QUEUE_BLOCKS, sizeof(net_item_t));
    uart_lock = xSemaphoreCreateMutex();
    idle = xEventGroupCreate();
    if (!to_uart || !to_net || !uart_lock || !idle) { ESP_LOGE(TAG, "Allocation failed / 内存分配失败"); return; }
    rfc2217_server_config_t config = {
        .on_client_connected = on_negotiated, .on_data_received = on_data,
        .on_write = on_wire, .on_command = on_command, .on_protocol_error = on_protocol,
    };
    if (rfc2217_server_create(&config, &parser)) { return; }
    /* Start worker owners before the session manager. No upstream socket threads. */
    if (xTaskCreate(uart_writer, "uart_tx", 4096, NULL, 10, NULL) != pdPASS ||
        xTaskCreate(uart_reader, "uart_rx", 4096, NULL, 11, NULL) != pdPASS ||
        xTaskCreate(network_writer, "tcp_tx", 4096, NULL, 9, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Worker creation failed; gateway stays idle / 工作任务失败，保持停用");
        return;
    }
    if (xTaskCreate(network_reader, "tcp_rx", 6144, NULL, 9, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Session task failed; gateway stays idle / 会话任务失败，保持停用");
    }
}
