# ESP-WIFI-MESH root

This ESP-IDF C++ application starts an ESP-WIFI-MESH network with this device
configured as the fixed root. The root connects to a 2.4 GHz Wi-Fi router and
logs mesh child connections, router connectivity, and received mesh payload
sizes to the serial console.

## Configure

Open `idf.py menuconfig` and set the options under **Mesh root settings**:

- **Router SSID** and **Router password**: the root's 2.4 GHz router uplink.
- **Mesh network password**: the WPA2 password mesh nodes use to join.
- **Mesh ID**: exactly 12 hexadecimal characters.
- **Maximum mesh layer**: network depth limit.

The router credentials are stored in the local `sdkconfig` file, which is
ignored by git. Do not commit credentials.

Configure every other node with the same mesh ID, mesh password, and router
details. Other nodes must use ESP-WIFI-MESH and must not be configured as the
fixed root. This project only implements the root side; it does not provide
firmware for the other nodes or an application-level payload protocol.
