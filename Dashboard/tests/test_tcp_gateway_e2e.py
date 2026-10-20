import json
import os
import socket
import threading
import time
from fastapi.testclient import TestClient

from main import app, receiver


SAMPLE_FRAME = {
    "origin": "BB:CC:DD:EE:FF:00",
    "bootId": "1122334455667788",
    "sequence": 10,
    "hops": 2,
    "rssi": -72,
    "rssiSourceNodeId": "BB:CC:DD:EE:FF:01",
    "readings": [
        {"sensorId": "ldr1", "channel": "raw", "value": 850, "unit": "adc_counts"}
    ],
}


def test_tcp_gateway_end_to_end():
    # Setup mock TCP server
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    old_transport = os.environ.get("MESH_TRANSPORT")
    old_host = os.environ.get("MESH_HOST")
    old_port = os.environ.get("MESH_PORT")

    os.environ["MESH_TRANSPORT"] = "tcp"
    os.environ["MESH_HOST"] = "127.0.0.1"
    os.environ["MESH_PORT"] = str(port)

    receiver.nodes.clear()
    receiver.history.clear()

    accepted_client = []
    accept_thread = threading.Thread(
        target=lambda: accepted_client.append(server_sock.accept()[0]),
        daemon=True,
    )
    accept_thread.start()

    try:
        with TestClient(app) as client:
            accept_thread.join(timeout=3.0)
            assert len(accepted_client) == 1, "TCP gateway worker did not connect to server"
            conn = accepted_client[0]

            with client.websocket_connect("/live") as websocket:
                init_msg = websocket.receive_json()
                assert init_msg["type"] == "status"

                # Send bad noise line then valid NDJSON frame over TCP
                conn.sendall(b"NOISE_PACKET_HEADER\n")
                time.sleep(0.05)
                frame_bytes = (json.dumps(SAMPLE_FRAME) + "\n").encode("utf-8")
                conn.sendall(frame_bytes)

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

                assert ws_msg is not None, "Did not receive telemetry over TCP gateway"
                assert ws_msg["nodeId"] == "BB:CC:DD:EE:FF:00"
                assert ws_msg["sequence"] == 10
                assert ws_msg["bootId"] == "1122334455667788"
                assert ws_msg["rssi"] == -72.0
                assert ws_msg["hops"] == 2
                assert ws_msg["payload"] == {"ldr1.raw": 850.0}

                # Verify /nodes reflects it
                res_nodes = client.get("/nodes")
                assert res_nodes.status_code == 200
                nodes = res_nodes.json()["nodes"]
                assert any(n["nodeId"] == "BB:CC:DD:EE:FF:00" for n in nodes)

            conn.close()
    finally:
        server_sock.close()
        if old_transport is None:
            os.environ.pop("MESH_TRANSPORT", None)
        else:
            os.environ["MESH_TRANSPORT"] = old_transport
        if old_host is None:
            os.environ.pop("MESH_HOST", None)
        else:
            os.environ["MESH_HOST"] = old_host
        if old_port is None:
            os.environ.pop("MESH_PORT", None)
        else:
            os.environ["MESH_PORT"] = old_port
