# ESP-WIFI-MESH node

This ESP-IDF C++ application joins the fixed-root mesh implemented in
`mesh-root`. It starts in ESP-WIFI-MESH's self-organizing `MESH_IDLE` state
(which is the API's setting for a node joining a mesh), tracks mesh events, and
sends a small heartbeat to the root every 10 seconds after connecting to a
parent. It does not designate itself as root.

## Configure

From this project directory, run `idf.py menuconfig` and set the values under
**Mesh node settings**. Copy the exact values from **Mesh root settings** in
the root project:

- Mesh ID (12 hexadecimal characters)
- Mesh network/AP password
- 2.4 GHz router SSID and password

The node's root-router credentials are part of the mesh configuration as well
as the root's settings, so they must match. The root remains the designated
root; this project does not enable fixed-root mode or select itself as root.
The local `sdkconfig` stores menuconfig values and is ignored by git. Do not
commit it or put credentials in source files. Configure the root and node
locally; this project does not read or modify the root project's `sdkconfig`.

## Build and flash

Use an ESP-IDF 6.1 environment with the ESP32 target:

```text
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p PORT flash monitor
```

Set `PORT` to the serial port for this ESP32. In menuconfig, the heartbeat
interval can also be adjusted (minimum 1000 ms).

The heartbeat contains the node's station MAC address and an incrementing
sequence number. It uses `MESH_DATA_TODS` with a null destination, the ESP-IDF
6.1 node-to-root route, and `MESH_TOS_P2P` for reliable delivery. The fixed
root's existing `esp_mesh_recv()` loop receives these packets and logs their
payload sizes. Parent connect/disconnect events report mesh-path connectivity.
The node also handles `MESH_EVENT_TODS_STATE` for root-router uplink changes;
the current root application must call `esp_mesh_post_toDS_state()` for those
uplink changes to be propagated to nodes.
