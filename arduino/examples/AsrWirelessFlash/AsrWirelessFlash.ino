// SPDX-License-Identifier: Apache-2.0
// Minimal ESP32 side of ASR PRO wireless flashing.
//
// 1. Joins Wi-Fi.
// 2. POST /api/maintenance/enter?mode=isp  -> asks the running ASR program to
//    restart (<asr_isp> on the application UART), catches the ASR bootloader on
//    the download port, then opens the RFC2217 gateway on TCP 3333.
// 3. The PC tool ("ASR 无线烧录助手") connects and relays the official
//    flashing software. After the session the application UART is restored.
//
// Wiring (3.3 V, common GND): see docs/wiring.md in the repository.
//   ESP32 DL_TX_PIN  -> ASR PB6   ESP32 DL_RX_PIN  <- ASR PB5   (download port)
//   ESP32 APP_TX_PIN -> ASR PA6   ESP32 APP_RX_PIN <- ASR PA5   (application UART)
#include <WebServer.h>
#include <WiFi.h>
#include <SerialNetworkGateway.h>

#ifndef APP_WIFI_SSID
#define APP_WIFI_SSID ""
#endif
#ifndef APP_WIFI_PASSWORD
#define APP_WIFI_PASSWORD ""
#endif

constexpr int APP_RX_PIN = 1;          // <- ASR PA5
constexpr int APP_TX_PIN = 2;          // -> ASR PA6
constexpr uint32_t APP_BAUD = 9600;    // the ASR program's Serial2 speed
constexpr int DL_TX_PIN = 9;           // -> ASR PB6 (UART0 RX)
constexpr int DL_RX_PIN = 10;          // <- ASR PB5 (UART0 TX)
constexpr uint32_t DL_BAUD = 115200;   // ROM bootloader handshake speed
// Long enough for an ASR program that speaks a prompt before restarting.
constexpr uint32_t CATCH_WINDOW_MS = 8000;
constexpr uint32_t EXIT_AFTER_SESSION_MS = 5000;
constexpr uint32_t EXIT_WITHOUT_CLIENT_MS = 120000;

WebServer server(80);
SerialNetworkGateway gateway;
bool flashing = false;
bool lastAppAck = false;
bool lastCaught = false;
uint32_t lastCatchMs = 0;
uint32_t sessionAtEnter = 0;
uint32_t idleSinceMs = 0;

void startAppUart() { Serial1.begin(APP_BAUD, SERIAL_8N1, APP_RX_PIN, APP_TX_PIN); }

bool waitFor(const char *needle, uint32_t timeoutMs) {
  String seen;
  const uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (Serial1.available()) {
      seen += static_cast<char>(Serial1.read());
      if (seen.indexOf(needle) >= 0) return true;
      if (seen.length() > 64) seen.remove(0, 32);
    }
    delay(2);
  }
  return false;
}

// Asks the ASR program to restart, then catches its ROM bootloader with
// 0x55 x10 + the A1 83 handshake. Once caught, the bootloader waits for the
// flashing software. Returns false if it never answered (the user can still
// power-cycle the ASR by hand while the software waits).
bool resetAsrIntoBootloader() {
  static const uint8_t sync[10] = {0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55};
  static const uint8_t req[10] = {0xA5, 0x0F, 0x00, 0x00, 0xA1, 0x83, 0x00, 0x67, 0xC4, 0xFF};
  static const uint8_t ack[7] = {0xA5, 0x0F, 0x00, 0x00, 0xA2, 0x83, 0x00};

  while (Serial1.available()) Serial1.read();
  Serial1.print("<asr_isp>");
  Serial1.flush();
  lastAppAck = waitFor("asr_isp:ok", 300);

  Serial1.end();
  pinMode(APP_RX_PIN, INPUT);
  pinMode(APP_TX_PIN, INPUT);
  Serial1.begin(DL_BAUD, SERIAL_8N1, DL_RX_PIN, DL_TX_PIN);
  bool caught = false;
  const uint32_t start = millis();
  while (!caught && millis() - start < CATCH_WINDOW_MS) {
    while (Serial1.available()) Serial1.read();
    Serial1.write(sync, sizeof(sync));
    delay(15);
    Serial1.write(req, sizeof(req));
    uint8_t got[32];
    size_t len = 0;
    const uint32_t waitStart = millis();
    while (millis() - waitStart < 40 && len < sizeof(got)) {
      if (Serial1.available()) got[len++] = Serial1.read(); else delay(1);
    }
    for (size_t i = 0; i + sizeof(ack) <= len && !caught; ++i) caught = memcmp(got + i, ack, sizeof(ack)) == 0;
    if (!caught) delay(25);
  }
  lastCatchMs = millis() - start;
  Serial1.end();
  pinMode(DL_TX_PIN, INPUT);
  pinMode(DL_RX_PIN, INPUT);
  return caught;
}

String statusJson() {
  return String("{\"active\":") + (flashing ? "true" : "false") + ",\"mode\":\"" + (flashing ? "isp" : "idle") +
         "\",\"lastAppAck\":" + (lastAppAck ? "true" : "false") + ",\"caught\":" + (lastCaught ? "true" : "false") +
         ",\"lastCatchMs\":" + String(lastCatchMs) + ",\"gateway\":" + gateway.statusJson() + "}";
}

void handleEnter() {
  if (server.arg("mode") != "isp") {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"mode_must_be_isp\"}");
    return;
  }
  if (flashing && gateway.status().state == SerialNetworkGatewayState::Connected) {
    server.send(409, "application/json", "{\"ok\":false,\"error\":\"session_active\"}");
    return;
  }
  if (flashing) gateway.end();  // a new download: reset the target again
  lastCaught = resetAsrIntoBootloader();
  const SerialNetworkGateway::Error err = gateway.begin();
  if (err != SerialNetworkGateway::Error::Ok) {
    startAppUart();
    server.send(500, "application/json",
                String("{\"ok\":false,\"error\":\"") + SerialNetworkGateway::errorName(err) + "\"}");
    return;
  }
  flashing = true;
  sessionAtEnter = gateway.status().session;
  idleSinceMs = millis();
  server.send(200, "application/json", String("{\"ok\":true,\"maintenance\":") + statusJson() + "}");
}

void finishFlashing() {
  gateway.end();
  startAppUart();
  flashing = false;
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency during downloads
  if (strlen(APP_WIFI_SSID) == 0) {
    Serial.println("Set APP_WIFI_SSID / APP_WIFI_PASSWORD (build flags or edit this file).");
  } else {
    WiFi.begin(APP_WIFI_SSID, APP_WIFI_PASSWORD);
  }
  startAppUart();

  SerialNetworkGatewayConfig cfg;
  cfg.uartNum = 1;
  cfg.txPin = DL_TX_PIN;
  cfg.rxPin = DL_RX_PIN;
  cfg.defaultBaud = DL_BAUD;
  cfg.queueDepth = 32;              // headroom for 1.5 Mbaud bursts
  cfg.uartRxBufferSize = 8192;
  cfg.idleTimeoutMs = 60000;
  cfg.maintenanceTimeoutMs = 0;     // this sketch decides when to restore the application UART
  cfg.quietMode = true;
  Serial.printf("Gateway configure: %s\n", SerialNetworkGateway::errorName(gateway.configure(cfg)));

  // No authentication: keep this on a trusted network, or add a token check here.
  server.on("/api/maintenance/enter", HTTP_POST, handleEnter);
  server.on("/api/maintenance/status", HTTP_GET, []() { server.send(200, "application/json", statusJson()); });
  server.begin();
}

void loop() {
  server.handleClient();
  gateway.loop();
  if (flashing) {
    const SerialNetworkGatewayStatus st = gateway.status();
    if (st.state == SerialNetworkGatewayState::Connected) {
      idleSinceMs = millis();
    } else if (st.session != sessionAtEnter ? millis() - idleSinceMs > EXIT_AFTER_SESSION_MS
                                            : millis() - idleSinceMs > EXIT_WITHOUT_CLIENT_MS) {
      finishFlashing();
    }
  } else {
    while (Serial1.available()) Serial1.read();  // your application protocol goes here
  }

  static uint32_t lastPrint = 0;
  if (millis() - lastPrint > 10000) {
    lastPrint = millis();
    Serial.printf("WiFi %s  IP %s  flashing=%d\n", WiFi.isConnected() ? "up" : "down",
                  WiFi.localIP().toString().c_str(), flashing);
  }
  delay(2);
}
