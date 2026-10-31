idf_component_register(SRCS "gateway.c" "wifi.c"
    INCLUDE_DIRS "."
    REQUIRES rfc2217_server esp_driver_uart esp_driver_gpio esp_wifi esp_event esp_netif nvs_flash esp_timer)
