import json
import os
import pty
import time
import pytest
from fastapi.testclient import TestClient

from main import app, receiver


SAMPLE_FRAME = {
    "origin": "AA:BB:CC:DD:EE:FF",
    "bootId": "0123456789abcdef",
    "sequence": 42,
    "hops": 1,
    "rssi": -61,
    "rssiSourceNodeId": "AA:BB:CC:DD:EE:FF",
    "readings": [
        {"sensorId": "ldr1", "channel": "raw", "value": 1234, "unit": "adc_counts"}
    ],
}


def test_serial_pty_end_to_end():
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    old_transport = os.environ.get("MESH_TRANSPORT")
    old_port = os.environ.get("MESH_SERIAL_PORT")

    os.environ["MESH_TRANSPORT"] = "serial"
    os.environ["MESH_SERIAL_PORT"] = slave_name

    receiver.nodes.clear()
    receiver.history.clear()

    try:
        with TestClient(app) as client:
            with client.websocket_connect("/live") as websocket:
                # Initial status from connect
                init_msg = websocket.receive_json()
                assert init_msg["type"] == "status"

                # Send an invalid non-JSON boot line first to test recovery
                os.write(master_fd, b"ESP32 ROM BOOT NOISE ...\n")
                time.sleep(0.1)

                # Send concrete gateway frame
                frame_bytes = (json.dumps(SAMPLE_FRAME) + "\n").encode("utf-8")
                os.write(master_fd, frame_bytes)

                # Receive live websocket telemetry
                ws_msg = None
                start_wait = time.time()
                while time.time() - start_wait < 3.0:
                    try:
                        msg = websocket.receive_json()
                        if msg.get("type") == "telemetry":
                            ws_msg = msg
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)

                assert ws_msg is not None, "Did not receive telemetry over WebSocket"
                assert ws_msg["nodeId"] == "AA:BB:CC:DD:EE:FF"
                assert ws_msg["sequence"] == 42
                assert ws_msg["bootId"] == "0123456789abcdef"
                assert ws_msg["rssi"] == -61.0
                assert ws_msg["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:FF"
                assert ws_msg["hops"] == 1
                assert ws_msg["units"] == {"ldr1.raw": "adc_counts"}
                assert ws_msg["payload"] == {"ldr1.raw": 1234.0}

                # Verify REST /nodes exposes identical fields
                res_nodes = client.get("/nodes")
                assert res_nodes.status_code == 200
                node_data = res_nodes.json()["nodes"][0]
                assert node_data["nodeId"] == "AA:BB:CC:DD:EE:FF"
                assert node_data["sequence"] == 42
                assert node_data["bootId"] == "0123456789abcdef"
                assert node_data["rssi"] == -61.0
                assert node_data["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:FF"
                assert node_data["hops"] == 1
                assert node_data["units"] == {"ldr1.raw": "adc_counts"}

                # Verify /nodes/{id}/history exposes identical fields
                res_hist = client.get("/nodes/AA:BB:CC:DD:EE:FF/history")
                assert res_hist.status_code == 200
                samples = res_hist.json()["samples"]
                assert len(samples) >= 1
                sample = samples[0]
                assert sample["sequence"] == 42
                assert sample["bootId"] == "0123456789abcdef"
                assert sample["rssi"] == -61.0
                assert sample["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:FF"
                assert sample["hops"] == 1
                assert sample["units"] == {"ldr1.raw": "adc_counts"}
                assert sample["ldr1.raw"] == 1234.0

    finally:
        os.close(master_fd)
        os.close(slave_fd)
        if old_transport is None:
            os.environ.pop("MESH_TRANSPORT", None)
        else:
            os.environ["MESH_TRANSPORT"] = old_transport
        if old_port is None:
            os.environ.pop("MESH_SERIAL_PORT", None)
        else:
            os.environ["MESH_SERIAL_PORT"] = old_port
