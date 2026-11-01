// SPDX-License-Identifier: Apache-2.0
// Standalone RFC2217 serial gateway. Fill in the placeholders below.
// Experimental: compile-tested only, not a hardware acceptance test.
#include <WiFi.h>
#include <SerialNetworkGateway.h>

// WiFi credentials. Leave empty to skip WiFi (the sketch only prints a hint).
#ifndef GATEWAY_WIFI_SSID
#define GATEWAY_WIFI_SSID ""
#endif
#ifndef GATEWAY_WIFI_PASSWORD
#define GATEWAY_WIFI_PASSWORD ""
#endif

// Target wiring (3.3 V logic only). -1 = not configured: begin() will refuse.
#ifndef GATEWAY_UART_NUM
#define GATEWAY_UART_NUM 1
#endif
#ifndef GATEWAY_TX_PIN
#define GATEWAY_TX_PIN -1  // ESP32 TX -> target RX
#endif
#ifndef GATEWAY_RX_PIN
#define GATEWAY_RX_PIN -1  // ESP32 RX <- target TX
#endif
#ifndef GATEWAY_RESET_PIN
#define GATEWAY_RESET_PIN -1  // optional, active low
#endif
#ifndef GATEWAY_BOOT_PIN
#define GATEWAY_BOOT_PIN -1  // optional, active low
#endif

SerialNetworkGateway gateway;
uint32_t lastStatusMs = 0;

void setup()
{
    Serial.begin(115200);
    delay(200);

    if (strlen(GATEWAY_WIFI_SSID) == 0) {
        Serial.println("GATEWAY_WIFI_SSID is empty: WiFi not started. Define it and rebuild.");
    } else {
        WiFi.mode(WIFI_STA);
        WiFi.begin(GATEWAY_WIFI_SSID, GATEWAY_WIFI_PASSWORD);
        Serial.println("Connecting to WiFi...");
    }

    SerialNetworkGatewayConfig cfg;
    cfg.uartNum = GATEWAY_UART_NUM;
    cfg.txPin = GATEWAY_TX_PIN;
    cfg.rxPin = GATEWAY_RX_PIN;
    cfg.resetPin = GATEWAY_RESET_PIN;
    cfg.bootPin = GATEWAY_BOOT_PIN;
    cfg.tcpPort = 3333;
    cfg.defaultBaud = 115200;
    cfg.maintenanceTimeoutMs = 0;  // standalone: never auto-exit
    // cfg.allowedIps[0] = IPAddress(...); cfg.allowedIpCount = 1;

    SerialNetworkGateway::Error err = gateway.configure(cfg);
    if (err == SerialNetworkGateway::Error::Ok) { err = gateway.begin(); }
    Serial.printf("Gateway start: %s\n", SerialNetworkGateway::errorName(err));
    if (err != SerialNetworkGateway::Error::Ok) {
        Serial.println("Set GATEWAY_TX_PIN / GATEWAY_RX_PIN to safe GPIOs for your board.");
    }
}

void loop()
{
    gateway.loop();

    // Console commands: 'r' = reset target, 'd' = enter download mode, 's' = status.
    while (Serial.available()) {
        int c = Serial.read();
        if (c == 'r') {
            Serial.println(SerialNetworkGateway::errorName(gateway.resetTarget(100)));
        } else if (c == 'd') {
            Serial.println(SerialNetworkGateway::errorName(gateway.enterDownloadMode(100, 300)));
        } else if (c == 's') {
            Serial.println(gateway.statusJson());
        }
    }

    if (millis() - lastStatusMs > 10000) {
        lastStatusMs = millis();
        if (WiFi.status() == WL_CONNECTED) {
            Serial.printf("IP %s, ", WiFi.localIP().toString().c_str());
        }
        Serial.println(gateway.statusJson());
    }
    delay(5);
}
