import os
import socket
from collections.abc import MutableSet

from history import HistoryBuffer
from mesh_protocol import normalize_message


class MeshReceiver:
    def __init__(self, max_history=1000):
        self.history = HistoryBuffer(limit=max_history)
        self.nodes = {}
        self.connected = False
        self.gateway_message = "Gateway not configured"
        self.transport = None
        self.websocket_clients = set()

    def add_client(self, websocket):
        self.websocket_clients.add(websocket)

    def remove_client(self, websocket):
        self.websocket_clients.discard(websocket)

    async def broadcast_event(self, event):
        for client in list(self.websocket_clients):
            try:
                await client.send_json(event)
            except Exception:
                self.websocket_clients.discard(client)

    def connect_gateway(self):
        transport = os.getenv("MESH_TRANSPORT", "tcp").lower()

        if transport == "tcp":
            host = os.getenv("MESH_HOST")
            port = os.getenv("MESH_PORT")
            if not host or not port:
                self.connected = False
                self.gateway_message = "Gateway transport configured but no host/port provided."
                return False

            try:
                port_value = int(port)
                sock = socket.create_connection((host, port_value), timeout=5)
                self.transport = sock
                self.connected = True
                self.gateway_message = f"Connected to gateway at {host}:{port_value}"
                return True
            except (OSError, ValueError):
                self.connected = False
                self.gateway_message = f"Unable to connect to {host}:{port}"
                return False

        if transport == "serial":
            serial_port = os.getenv("MESH_SERIAL_PORT")
            if not serial_port:
                self.connected = False
                self.gateway_message = "Serial transport selected but no serial port configured."
                return False

            try:
                import serial

                self.transport = serial.Serial(serial_port, 115200, timeout=1)
                self.connected = True
                self.gateway_message = f"Connected to serial gateway {serial_port}"
                return True
            except Exception:
                self.connected = False
                self.gateway_message = f"Unable to open serial port {serial_port}"
                return False

        self.connected = False
        self.gateway_message = "Gateway not configured for this prototype."
        return False

    def handle_message(self, raw_message):
        message = normalize_message(raw_message)
        node_id = message["nodeId"]
        sequence = message["sequence"]

        current = self.nodes.get(node_id)
        if current and current.get("sequence") is not None and sequence <= current["sequence"]:
            raise ValueError(f"Duplicate or out-of-order sequence for {node_id}: {sequence}")

        state = {
            "nodeId": node_id,
            "timestamp": message["timestamp"],
            "sequence": sequence,
            "payload": message["payload"],
            "meshId": message.get("meshId"),
            "rssi": message.get("rssi"),
            "batteryLevel": message.get("batteryLevel"),
            "firmwareVersion": message.get("firmwareVersion"),
            "role": message.get("role"),
        }

        self.nodes[node_id] = state

        sample = {
            "timestamp": message["timestamp"],
            "sequence": sequence,
            **message["payload"],
        }
        self.history.add(node_id, sample)
        self.connected = True
        self.gateway_message = f"Received telemetry from {node_id}"

        event = {
            "type": "telemetry",
            "nodeId": node_id,
            "timestamp": message["timestamp"],
            "sequence": sequence,
            "payload": message["payload"],
        }
        return event

    def get_health(self):
        return {
            "status": "ok",
            "gatewayConnected": self.connected,
            "nodes": len(self.nodes),
            "gatewayMessage": self.gateway_message,
        }

    def get_node(self, node_id):
        node = self.nodes.get(node_id)
        if node is None:
            return None
        return {
            "nodeId": node["nodeId"],
            "timestamp": node["timestamp"],
            "sequence": node["sequence"],
            "payload": node["payload"],
            **({"meshId": node["meshId"]} if node.get("meshId") is not None else {}),
            **({"rssi": node["rssi"]} if node.get("rssi") is not None else {}),
            **({"batteryLevel": node["batteryLevel"]} if node.get("batteryLevel") is not None else {}),
            **({"firmwareVersion": node["firmwareVersion"]} if node.get("firmwareVersion") is not None else {}),
            **({"role": node["role"]} if node.get("role") is not None else {}),
        }

    def list_nodes(self):
        return [self.get_node(node_id) for node_id in sorted(self.nodes)]

    def get_history(self, node_id, since=None, limit=100):
        return self.history.get(node_id, since=since, limit=limit)
