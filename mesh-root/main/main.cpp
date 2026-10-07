#include <cstdlib>
#include <cstring>

extern "C" {
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_mesh.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
}

namespace {

constexpr char TAG[] = "mesh_root";
constexpr size_t MESH_ID_SIZE = 6;
constexpr size_t MESH_RX_BUFFER_SIZE = 1500;

esp_netif_t *s_mesh_sta_netif = nullptr;

int hex_digit(char value)
{
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    return -1;
}

bool parse_mesh_id(uint8_t mesh_id[MESH_ID_SIZE])
{
    constexpr char configured_id[] = CONFIG_MESH_ROOT_MESH_ID;
    if (sizeof(configured_id) != MESH_ID_SIZE * 2 + 1) {
        return false;
    }

    for (size_t i = 0; i < MESH_ID_SIZE; ++i) {
        const int high = hex_digit(configured_id[i * 2]);
        const int low = hex_digit(configured_id[i * 2 + 1]);
        if (high < 0 || low < 0) {
            return false;
        }
        mesh_id[i] = static_cast<uint8_t>((high << 4) | low);
    }
    return true;
}

void receive_mesh_data(void *arg)
{
    uint8_t buffer[MESH_RX_BUFFER_SIZE];
    mesh_addr_t source{};
    mesh_data_t data{};
    data.data = buffer;
    data.size = sizeof(buffer);
    int flags = 0;

    while (true) {
        data.size = sizeof(buffer);
        const esp_err_t err = esp_mesh_recv(&source, &data, portMAX_DELAY, &flags, nullptr, 0);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_mesh_recv failed: %s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        if (data.size == 0) {
            continue;
        }

        ESP_LOGI(TAG, "received %u bytes from " MACSTR " (proto=%d, tos=%d)",
                 static_cast<unsigned>(data.size), MAC2STR(source.addr), data.proto, data.tos);
    }
}

void mesh_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    switch (event_id) {
    case MESH_EVENT_STARTED:
        ESP_LOGI(TAG, "mesh started; root fixed=%s", esp_mesh_is_root_fixed() ? "yes" : "no");
        break;
    case MESH_EVENT_PARENT_CONNECTED: {
        const auto *event = static_cast<const mesh_event_connected_t *>(event_data);
        ESP_LOGI(TAG, "root connected to router; layer=%d", event->self_layer);

        esp_err_t err = esp_netif_dhcpc_stop(s_mesh_sta_netif);
        if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
            ESP_LOGE(TAG, "failed to stop DHCP client: %s", esp_err_to_name(err));
            break;
        }
        err = esp_netif_dhcpc_start(s_mesh_sta_netif);
        if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED) {
            ESP_LOGE(TAG, "failed to start DHCP client: %s", esp_err_to_name(err));
        }
        break;
    }
    case MESH_EVENT_PARENT_DISCONNECTED:
        ESP_LOGW(TAG, "root disconnected from its router");
        break;
    case MESH_EVENT_CHILD_CONNECTED: {
        const auto *event = static_cast<const mesh_event_child_connected_t *>(event_data);
        ESP_LOGI(TAG, "child connected: " MACSTR, MAC2STR(event->mac));
        break;
    }
    case MESH_EVENT_CHILD_DISCONNECTED: {
        const auto *event = static_cast<const mesh_event_child_disconnected_t *>(event_data);
        ESP_LOGW(TAG, "child disconnected: " MACSTR, MAC2STR(event->mac));
        break;
    }
    case MESH_EVENT_ROOT_FIXED: {
        const auto *event = static_cast<const mesh_event_root_fixed_t *>(event_data);
        ESP_LOGI(TAG, "fixed-root mode %s", event->is_fixed ? "enabled" : "disabled");
        break;
    }
    case MESH_EVENT_TODS_STATE: {
        const auto *event = static_cast<const mesh_event_toDS_state_t *>(event_data);
        ESP_LOGI(TAG, "router uplink %s", *event ? "available" : "unavailable");
        break;
    }
    default:
        break;
    }
}

void ip_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    const auto *event = static_cast<const ip_event_got_ip_t *>(event_data);
    ESP_LOGI(TAG, "router assigned IP " IPSTR, IP2STR(&event->ip_info.ip));
}

void validate_configuration()
{
    const size_t ssid_length = strlen(CONFIG_MESH_ROOT_ROUTER_SSID);
    const size_t router_password_length = strlen(CONFIG_MESH_ROOT_ROUTER_PASSWORD);
    const size_t mesh_password_length = strlen(CONFIG_MESH_ROOT_AP_PASSWORD);

    if (ssid_length == 0 || ssid_length > 32) {
        ESP_LOGE(TAG, "configure a router SSID of 1-32 characters in menuconfig");
        abort();
    }
    if (router_password_length != 0 &&
        (router_password_length < 8 || router_password_length > 63)) {
        ESP_LOGE(TAG, "router password must be empty for an open network or 8-63 characters");
        abort();
    }
    if (mesh_password_length < 8 || mesh_password_length > 63) {
        ESP_LOGE(TAG, "mesh AP password must be 8-63 characters");
        abort();
    }
}

} // namespace

extern "C" void app_main(void)
{
    validate_configuration();

    uint8_t mesh_id[MESH_ID_SIZE]{};
    if (!parse_mesh_id(mesh_id)) {
        ESP_LOGE(TAG, "mesh ID must contain exactly 12 hexadecimal characters");
        abort();
    }

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    } else {
        ESP_ERROR_CHECK(err);
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_create_default_wifi_mesh_netifs(&s_mesh_sta_netif, nullptr));

    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_config));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, ip_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_ERROR_CHECK(esp_mesh_init());
    ESP_ERROR_CHECK(esp_event_handler_register(MESH_EVENT, ESP_EVENT_ANY_ID, mesh_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_mesh_set_max_layer(CONFIG_MESH_ROOT_MAX_LAYER));
    ESP_ERROR_CHECK(esp_mesh_set_type(MESH_ROOT));
    ESP_ERROR_CHECK(esp_mesh_fix_root(true));

    mesh_cfg_t mesh_config{};
    mesh_config.crypto_funcs = &g_wifi_default_mesh_crypto_funcs;
    memcpy(mesh_config.mesh_id.addr, mesh_id, sizeof(mesh_id));
    mesh_config.channel = 0;

    const size_t ssid_length = strlen(CONFIG_MESH_ROOT_ROUTER_SSID);
    mesh_config.router.ssid_len = static_cast<uint8_t>(ssid_length);
    memcpy(mesh_config.router.ssid, CONFIG_MESH_ROOT_ROUTER_SSID, ssid_length);
    memcpy(mesh_config.router.password, CONFIG_MESH_ROOT_ROUTER_PASSWORD,
           strlen(CONFIG_MESH_ROOT_ROUTER_PASSWORD));

    const size_t mesh_password_length = strlen(CONFIG_MESH_ROOT_AP_PASSWORD);
    memcpy(mesh_config.mesh_ap.password, CONFIG_MESH_ROOT_AP_PASSWORD, mesh_password_length);
    mesh_config.mesh_ap.max_connection = 6;
    ESP_ERROR_CHECK(esp_mesh_set_ap_authmode(WIFI_AUTH_WPA2_PSK));
    ESP_ERROR_CHECK(esp_mesh_set_config(&mesh_config));
    ESP_ERROR_CHECK(esp_mesh_start());

    BaseType_t task_result = xTaskCreate(receive_mesh_data, "mesh_rx", 4096, nullptr, 5, nullptr);
    if (task_result != pdPASS) {
        ESP_LOGE(TAG, "failed to create mesh receive task");
        abort();
    }

    ESP_LOGI(TAG, "fixed mesh root started; mesh ID=" CONFIG_MESH_ROOT_MESH_ID);
}
