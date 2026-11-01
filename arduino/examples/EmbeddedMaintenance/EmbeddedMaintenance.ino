// SPDX-License-Identifier: Apache-2.0
// Embedding the gateway into an existing application as an on-demand
// "maintenance mode". The main loop keeps running its other work; the business
// UART is released with Serial1.end() before the gateway takes it, and restored
// in onExit. Experimental: compile-tested only.
#include <WiFi.h>
#include <SerialNetworkGateway.h>

#ifndef APP_WIFI_SSID
#define APP_WIFI_SSID ""
#endif
#ifndef APP_WIFI_PASSWORD
#define APP_WIFI_PASSWORD ""
#endif

// Business UART (Serial1). -1 = not configured; maintenance mode will refuse.
#ifndef APP_UART_RX_PIN
#define APP_UART_RX_PIN -1
#endif
#ifndef APP_UART_TX_PIN
#define APP_UART_TX_PIN -1
#endif
constexpr uint32_t APP_UART_BAUD = 9600;
constexpr uint32_t MAINTENANCE_DEFAULT_BAUD = 115200;  // until client SET-BAUDRATE

SerialNetworkGateway gateway;
bool businessUartOn = false;
uint32_t heartbeatMs = 0;

void startBusinessUart()
{
    if (APP_UART_RX_PIN < 0 || APP_UART_TX_PIN < 0) { return; }
    Serial1.begin(APP_UART_BAUD, SERIAL_8N1, APP_UART_RX_PIN, APP_UART_TX_PIN);
    businessUartOn = true;
}

void stopBusinessUart()
{
    if (businessUartOn) {
        Serial1.flush();
        Serial1.end();  // deletes the IDF UART driver so the gateway can own UART1
        businessUartOn = false;
    }
}

void readBusinessUart()
{
    if (!businessUartOn) { return; }  // gateway owns the UART during maintenance
    while (Serial1.available()) {
        int c = Serial1.read();
        (void)c;  // application protocol handling goes here
    }
}

bool enterMaintenance()
{
    if (gateway.isRunning()) { return true; }
    stopBusinessUart();
    SerialNetworkGateway::Error err = gateway.begin();
    if (err != SerialNetworkGateway::Error::Ok) {
        Serial.printf("Maintenance refused: %s\n", SerialNetworkGateway::errorName(err));
        startBusinessUart();  // give the UART back to the application
        return false;
    }
    return true;
}

void exitMaintenance()
{
    SerialNetworkGateway::Error err = gateway.end();
    if (err != SerialNetworkGateway::Error::Ok && err != SerialNetworkGateway::Error::NotRunning) {
        // NotQuiescent: the UART may still be owned. Do not restart Serial1; reboot instead.
        Serial.printf("Maintenance exit failed: %s\n", SerialNetworkGateway::errorName(err));
    }
}

void setup()
{
    Serial.begin(115200);
    delay(200);
    if (strlen(APP_WIFI_SSID) == 0) {
        Serial.println("APP_WIFI_SSID is empty: WiFi not started.");
    } else {
        WiFi.mode(WIFI_STA);
        WiFi.begin(APP_WIFI_SSID, APP_WIFI_PASSWORD);
    }
    startBusinessUart();

    SerialNetworkGatewayConfig cfg;
    cfg.uartNum = 1;               // same UART as Serial1
    cfg.txPin = APP_UART_TX_PIN;   // same pins as the business link
    cfg.rxPin = APP_UART_RX_PIN;
    cfg.defaultBaud = MAINTENANCE_DEFAULT_BAUD;
    cfg.maintenanceTimeoutMs = 10UL * 60UL * 1000UL;  // auto-exit after 10 min without a client
    cfg.quietMode = true;
    Serial.printf("Gateway configure: %s\n", SerialNetworkGateway::errorName(gateway.configure(cfg)));

    gateway.onEnter([]() { Serial.println("Maintenance mode: UART handed to gateway"); });
    gateway.onExit([](SerialNetworkGateway::ExitReason reason) {
        Serial.printf("Maintenance ended (%s), restoring Serial1\n",
                      reason == SerialNetworkGateway::ExitReason::MaintenanceTimeout ? "timeout" : "requested");
        startBusinessUart();
    });
}

void loop()
{
    readBusinessUart();   // other application work keeps running
    gateway.loop();       // non-blocking

    // Local console trigger for the demo. In a real product, call
    // enterMaintenance()/exitMaintenance() from an authenticated control path.
    while (Serial.available()) {
        int c = Serial.read();
        if (c == 'm') { enterMaintenance(); }
        if (c == 'x') { exitMaintenance(); }
        if (c == 's') { Serial.println(gateway.statusJson()); }
    }

    if (millis() - heartbeatMs > 5000) {
        heartbeatMs = millis();
        Serial.printf("app alive, maintenance=%d\n", gateway.isRunning());
    }
    delay(5);
}
