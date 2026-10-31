// SPDX-License-Identifier: Apache-2.0
#include <stdbool.h>
#include <string.h>
#include "sdkconfig.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/timers.h"

static EventGroupHandle_t events;
static TimerHandle_t reconnect;
static const char *TAG = "gateway_wifi";
#define CONNECTED BIT0

static void retry(TimerHandle_t timer)
{
    (void)timer;
    esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Reconnect deferred: %s", esp_err_to_name(err));
        xTimerStart(reconnect, 0);
    }
}

static void event(void *ctx, esp_event_base_t base, int32_t id, void *data)
{
    (void)ctx; (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        xTimerStart(reconnect, 0);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(events, CONNECTED);
        xTimerReset(reconnect, 0); /* Fixed 3 s retry, no queue replay. */
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xTimerStop(reconnect, 0);
        xEventGroupSetBits(events, CONNECTED);
        ESP_LOGI(TAG, "Wi-Fi ready / Wi-Fi 已连接");
    }
}

bool gateway_wifi_ready(void)
{
    return events && (xEventGroupGetBits(events) & CONNECTED);
}

bool gateway_wifi_start(void)
{
#ifndef CONFIG_GW_WIFI_ENABLE
    ESP_LOGW(TAG, "Wi-Fi disabled; gateway idle / Wi-Fi 未启用，网关停用");
    return false;
#else
    if (!strlen(CONFIG_GW_WIFI_SSID) || strlen(CONFIG_GW_WIFI_SSID) > 32 ||
        strlen(CONFIG_GW_WIFI_PASSWORD) < 8 || strlen(CONFIG_GW_WIFI_PASSWORD) > 63) {
        ESP_LOGE(TAG, "Wi-Fi credentials missing/invalid; gateway idle / 凭据未配置或无效");
        return false;
    }
    /* Fail closed on NVS errors. Never auto-erase someone else's NVS. */
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    events = xEventGroupCreate();
    reconnect = xTimerCreate("wifi_retry", pdMS_TO_TICKS(3000), pdFALSE, NULL, retry);
    if (!events || !reconnect) { return false; }
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, event, NULL));
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    wifi_config_t config = {0};
    memcpy(config.sta.ssid, CONFIG_GW_WIFI_SSID, strlen(CONFIG_GW_WIFI_SSID));
    memcpy(config.sta.password, CONFIG_GW_WIFI_PASSWORD, strlen(CONFIG_GW_WIFI_PASSWORD));
    config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    return true;
#endif
}
