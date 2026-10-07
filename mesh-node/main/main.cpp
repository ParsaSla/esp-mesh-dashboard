#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <inttypes.h>

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

constexpr char TAG[] = "mesh_node";
constexpr size_t MESH_ID_SIZE = 6;
constexpr uint32_t HEARTBEAT_INTERVAL_MS = CONFIG_MESH_NODE_HEARTBEAT_INTERVAL_MS;

std::atomic<bool> s_parent_connected{false};
uint8_t s_station_mac[6]{};

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
    constexpr char configured_id[] = CONFIG_MESH_NODE_MESH_ID;
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

void heartbeat_task(void *arg)
{
    uint32_t sequence = 0;

    while (true) {
        if (s_parent_connected.load()) {
            char payload[48]{};
            const int length = snprintf(payload, sizeof(payload),
                                        "heartbeat mac=" MACSTR " seq=%" PRIu32,
                                        MAC2STR(s_station_mac), sequence);
            if (length < 0 || static_cast<size_t>(length) >= sizeof(payload)) {
                ESP_LOGE(TAG, "failed to format heartbeat payload");
            } else {
                mesh_data_t data{};
                data.data = reinterpret_cast<uint8_t *>(payload);
                data.size = static_cast<int>(length);
                data.proto = MESH_PROTO_BIN;
                data.tos = MESH_TOS_P2P;

                const esp_err_t err = esp_mesh_send(nullptr, &data, MESH_DATA_TODS, nullptr, 0);
                if (err != ESP_OK) {
                    ESP_LOGE(TAG, "heartbeat #%" PRIu32 " send to root failed: %s (0x%x)",
                             sequence, esp_err_to_name(err), static_cast<unsigned>(err));
                } else {
                    ESP_LOGI(TAG, "heartbeat #%" PRIu32 " sent to root (%d bytes)",
                             sequence, length);
                }
                ++sequence;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(HEARTBEAT_INTERVAL_MS));
    }
}

void mesh_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    switch (event_id) {
    case MESH_EVENT_STARTED:
        ESP_LOGI(TAG, "mesh started as a regular node");
        break;
    case MESH_EVENT_PARENT_CONNECTED: {
        const auto *event = static_cast<const mesh_event_connected_t *>(event_data);
        s_parent_connected.store(true);
        ESP_LOGI(TAG, "connected to mesh parent; layer=%u",
                 static_cast<unsigned>(event->self_layer));
        break;
    }
    case MESH_EVENT_PARENT_DISCONNECTED:
        s_parent_connected.store(false);
        ESP_LOGW(TAG, "disconnected from mesh parent; root connectivity unavailable");
        break;
    case MESH_EVENT_LAYER_CHANGE: {
        const auto *event = static_cast<const mesh_event_layer_change_t *>(event_data);
        ESP_LOGI(TAG, "mesh layer changed to %u", static_cast<unsigned>(event->new_layer));
        break;
    }
    case MESH_EVENT_TODS_STATE: {
        const auto state = *static_cast<const mesh_event_toDS_state_t *>(event_data);
        ESP_LOGI(TAG, "root router uplink %s",
                 state == MESH_TODS_REACHABLE ? "available" : "unavailable");
        break;
    }
    default:
        break;
    }
}

void validate_configuration()
{
    const size_t ssid_length = strlen(CONFIG_MESH_NODE_ROUTER_SSID);
    const size_t router_password_length = strlen(CONFIG_MESH_NODE_ROUTER_PASSWORD);
    const size_t mesh_password_length = strlen(CONFIG_MESH_NODE_AP_PASSWORD);

    if (ssid_length == 0 || ssid_length > 32) {
        ESP_LOGE(TAG, "configure the root's router SSID in menuconfig (1-32 characters)");
        abort();
    }
    if (router_password_length != 0 &&
        (router_password_length < 8 || router_password_length > 63)) {
        ESP_LOGE(TAG, "router password must be empty for an open network or 8-63 characters");
        abort();
    }
    if (mesh_password_length < 8 || mesh_password_length > 63) {
        ESP_LOGE(TAG, "configure the root's mesh AP password in menuconfig (8-63 characters)");
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
    esp_netif_t *mesh_sta_netif = nullptr;
    esp_netif_t *mesh_ap_netif = nullptr;
    ESP_ERROR_CHECK(esp_netif_create_default_wifi_mesh_netifs(&mesh_sta_netif, &mesh_ap_netif));

    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_config));
    ESP_ERROR_CHECK(esp_read_mac(s_station_mac, ESP_MAC_WIFI_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_ERROR_CHECK(esp_mesh_init());
    ESP_ERROR_CHECK(esp_event_handler_register(MESH_EVENT, ESP_EVENT_ANY_ID,
                                                mesh_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_mesh_set_max_layer(CONFIG_MESH_NODE_MAX_LAYER));
    ESP_ERROR_CHECK(esp_mesh_set_type(MESH_IDLE));

    mesh_cfg_t mesh_config{};
    mesh_config.crypto_funcs = &g_wifi_default_mesh_crypto_funcs;
    memcpy(mesh_config.mesh_id.addr, mesh_id, sizeof(mesh_id));
    mesh_config.channel = 0;

    const size_t ssid_length = strlen(CONFIG_MESH_NODE_ROUTER_SSID);
    mesh_config.router.ssid_len = static_cast<uint8_t>(ssid_length);
    memcpy(mesh_config.router.ssid, CONFIG_MESH_NODE_ROUTER_SSID, ssid_length);
    memcpy(mesh_config.router.password, CONFIG_MESH_NODE_ROUTER_PASSWORD,
           strlen(CONFIG_MESH_NODE_ROUTER_PASSWORD));

    const size_t mesh_password_length = strlen(CONFIG_MESH_NODE_AP_PASSWORD);
    memcpy(mesh_config.mesh_ap.password, CONFIG_MESH_NODE_AP_PASSWORD, mesh_password_length);
    mesh_config.mesh_ap.max_connection = 6;

    ESP_ERROR_CHECK(esp_mesh_set_ap_authmode(WIFI_AUTH_WPA2_PSK));
    ESP_ERROR_CHECK(esp_mesh_set_config(&mesh_config));
    ESP_ERROR_CHECK(esp_mesh_start());

    const BaseType_t task_result = xTaskCreate(heartbeat_task, "mesh_heartbeat", 3072,
                                               nullptr, 5, nullptr);
    if (task_result != pdPASS) {
        ESP_LOGE(TAG, "failed to create heartbeat task");
        abort();
    }

    ESP_LOGI(TAG, "regular mesh node started; mesh ID=" CONFIG_MESH_NODE_MESH_ID
                   ", heartbeat interval=%" PRIu32 " ms",
             HEARTBEAT_INTERVAL_MS);
}
