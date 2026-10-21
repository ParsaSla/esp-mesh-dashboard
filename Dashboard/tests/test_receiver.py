import json
import time

import pytest
from fastapi.testclient import TestClient

from main import app, receiver
from mesh_protocol import normalize_message
from receiver import MeshReceiver


VALID_MESSAGE = {
    "nodeId": "node-001",
    "timestamp": "2026-10-07T10:15:30Z",
    "sequence": 42,
    "payload": {"temperature": 24.8, "humidity": 48},
}


def test_valid_message_is_normalized():
    message = normalize_message(VALID_MESSAGE)

    assert message["nodeId"] == "node-001"
    assert message["sequence"] == 42
    assert message["payload"]["temperature"] == 24.8
    assert message["payload"]["humidity"] == 48


def test_invalid_json_is_rejected():
    with pytest.raises(ValueError, match="Invalid JSON message"):
        normalize_message('{"nodeId": "node-001"')


def test_missing_payload_is_rejected():
    with pytest.raises(ValueError, match="Missing required field"):
        normalize_message({"nodeId": "node-001", "timestamp": "2026-10-07T10:15:30Z", "sequence": 1})


def test_duplicate_sequence_is_rejected():
    receiver = MeshReceiver()
    receiver.handle_message(VALID_MESSAGE)

    duplicate = {
        **VALID_MESSAGE,
        "payload": {"temperature": 25.1, "humidity": 49},
    }

    with pytest.raises(ValueError, match="Duplicate or out-of-order sequence"):
        receiver.handle_message(duplicate)


def test_latest_value_replaces_previous_state():
    receiver = MeshReceiver()
    receiver.handle_message(VALID_MESSAGE)

    updated = {
        "nodeId": "node-001",
        "timestamp": "2026-10-07T10:16:00Z",
        "sequence": 43,
        "payload": {"temperature": 25.2, "humidity": 50},
    }

    event = receiver.handle_message(updated)

    assert event["sequence"] == 43
    assert receiver.get_node("node-001")["payload"]["temperature"] == 25.2
    assert len(receiver.get_history("node-001")) == 2


def test_mesh_callback_json_is_exposed_as_node_and_packet_metadata():
    receiver = MeshReceiver()
    packet = {
        "sender": "AA:BB:CC:DD:EE:02",
        "rssi": -61,
        "origin": "AA:BB:CC:DD:EE:01",
        "destination": "FF:FF:FF:FF:FF:FF",
        "sequence": 7,
        "ttl": 5,
        "hops": 2,
        "payload": '{"temperature":24.5,"note":"ok"}',
    }

    event = receiver.handle_message(json.dumps(packet))
    node = receiver.get_node("AA:BB:CC:DD:EE:01")

    assert event["nodeId"] == "AA:BB:CC:DD:EE:01"
    assert event["rssi"] == -61.0
    assert event["sender"] == "AA:BB:CC:DD:EE:02"
    assert event["destination"] == "FF:FF:FF:FF:FF:FF"
    assert event["ttl"] == 5
    assert event["packetPayload"] == packet["payload"]
    assert node["payload"] == {"temperature": 24.5}
    assert node["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:02"
    assert node["sender"] == packet["sender"]
    assert node["destination"] == packet["destination"]
    assert node["ttl"] == packet["ttl"]
    assert receiver.get_history(node["nodeId"])[0]["temperature"] == 24.5


def test_mesh_callback_json_accepts_null_rssi_and_text_payload():
    receiver = MeshReceiver()
    packet = {
        "sender": "AA:BB:CC:DD:EE:02",
        "rssi": None,
        "origin": "AA:BB:CC:DD:EE:01",
        "destination": "AA:BB:CC:DD:EE:03",
        "sequence": 8,
        "ttl": 4,
        "hops": 1,
        "payload": "hello mesh",
    }

    event = receiver.handle_message(packet)

    assert event.get("rssi") is None
    assert event["payload"] == {}
    assert event["packetPayload"] == "hello mesh"


def test_api_endpoints_expose_latest_node_state():
    client = TestClient(app)
    receiver.nodes.clear()
    receiver.history.clear()
    receiver.handle_message(VALID_MESSAGE)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert 'id="telemetry-chart"' in dashboard.text
    assert 'id="mesh-controls-title"' in dashboard.text
    assert "BROADCAST TO ALL MESH NODES VIA THE OBSERVER" in dashboard.text
    assert 'id="deep-sleep-form"' in dashboard.text
    assert 'id="deep-sleep-btn"' in dashboard.text
    assert 'id="interval-seconds"' in dashboard.text
    assert 'id="deep-sleep-seconds"' in dashboard.text
    assert 'id="auto-sleep-seconds"' in dashboard.text
    assert 'id="auto-wake-grace-seconds"' in dashboard.text
    assert "REPORT INTERVAL <span>(SECONDS)</span>" in dashboard.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    nodes = client.get("/nodes")
    assert nodes.status_code == 200
    assert nodes.json()["nodes"][0]["nodeId"] == "node-001"

    history = client.get("/nodes/node-001/history?limit=10")
    assert history.status_code == 200
    assert history.json()["samples"][0]["sequence"] == 42


def test_mesh_command_endpoint_writes_observer_serial_commands(monkeypatch):
    class FakeSerial:
        def __init__(self):
            self.writes = []

        def write(self, payload):
            self.writes.append(payload)
            return len(payload)

        def flush(self):
            pass

    serial = FakeSerial()
    monkeypatch.setattr(receiver, "transport", serial)
    monkeypatch.setattr(receiver, "connected", True)

    client = TestClient(app)
    request_data = client.post("/api/mesh/commands", json={"command": "SEND_DATA"})
    change_interval = client.post(
        "/api/mesh/commands",
        json={"command": "CHANGE_INTERVAL", "intervalMs": 5000},
    )
    deep_sleep = client.post(
        "/api/mesh/commands",
        json={"command": "DEEP_SLEEP", "sleepMs": 60000},
    )

    assert request_data.status_code == 200
    assert request_data.json() == {"sent": True, "command": "SEND_DATA"}
    assert change_interval.status_code == 200
    assert change_interval.json() == {"sent": True, "command": "CHANGE_INTERVAL 5000"}
    assert deep_sleep.status_code == 200
    assert deep_sleep.json() == {"sent": True, "command": "DEEP_SLEEP 60000"}
    assert serial.writes == [b"SEND_DATA\n", b"CHANGE_INTERVAL 5000\n", b"DEEP_SLEEP 60000\n"]


def test_mesh_command_endpoint_rejects_invalid_or_offline_commands(monkeypatch):
    monkeypatch.setattr(receiver, "transport", None)
    monkeypatch.setattr(receiver, "connected", False)
    client = TestClient(app)

    assert client.post("/api/mesh/commands", json={"command": "SEND_DATA"}).status_code == 503
    assert client.post(
        "/api/mesh/commands",
        json={"command": "CHANGE_INTERVAL", "intervalMs": 0},
    ).status_code == 422
    assert client.post(
        "/api/mesh/commands",
        json={"command": "CHANGE_INTERVAL"},
    ).status_code == 400
    assert client.post(
        "/api/mesh/commands",
        json={"command": "DEEP_SLEEP", "sleepMs": 0},
    ).status_code == 422
    assert client.post(
        "/api/mesh/commands",
        json={"command": "DEEP_SLEEP"},
    ).status_code == 400


def test_auto_sleep_mode_sends_sleep_and_can_be_stopped(monkeypatch):
    class FakeSerial:
        def __init__(self):
            self.writes = []

        def write(self, payload):
            self.writes.append(payload)
            return len(payload)

        def flush(self):
            pass

    serial = FakeSerial()
    monkeypatch.setattr(receiver, "transport", serial)
    monkeypatch.setattr(receiver, "connected", True)
    client = TestClient(app)

    assert client.get("/api/mesh/auto-sleep").json()["active"] is False

    started = client.post(
        "/api/mesh/auto-sleep",
        json={"sleepMs": 60000, "wakeGraceMs": 5000},
    )
    assert started.status_code == 200
    assert started.json() == {
        "active": True,
        "status": "sleeping",
        "sleepMs": 60000,
        "wakeGraceMs": 5000,
        "error": None,
    }
    assert serial.writes == [b"DEEP_SLEEP 60000\n"]

    stopped = client.delete("/api/mesh/auto-sleep")
    assert stopped.status_code == 200
    assert stopped.json()["active"] is False
    assert stopped.json()["status"] == "stopped"
    assert serial.writes == [b"DEEP_SLEEP 60000\n"]


def test_auto_sleep_mode_validates_settings_and_gateway(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(receiver, "transport", None)
    monkeypatch.setattr(receiver, "connected", False)

    assert client.post(
        "/api/mesh/auto-sleep",
        json={"sleepMs": 0, "wakeGraceMs": 5000},
    ).status_code == 422
    assert client.post(
        "/api/mesh/auto-sleep",
        json={"sleepMs": 60000, "wakeGraceMs": -1},
    ).status_code == 422
    assert client.post(
        "/api/mesh/auto-sleep",
        json={"sleepMs": 60000, "wakeGraceMs": 5000},
    ).status_code == 503


def test_auto_sleep_mode_repeats_after_sleep_and_wake_wait(monkeypatch):
    class FakeSerial:
        def __init__(self):
            self.writes = []

        def write(self, payload):
            self.writes.append(payload)
            return len(payload)

        def flush(self):
            pass

    serial = FakeSerial()
    monkeypatch.setattr(receiver, "transport", serial)
    monkeypatch.setattr(receiver, "connected", True)
    monkeypatch.setenv("MESH_TRANSPORT", "disabled")

    with TestClient(app) as client:
        started = client.post(
            "/api/mesh/auto-sleep",
            json={"sleepMs": 20, "wakeGraceMs": 20},
        )
        assert started.status_code == 200
        time.sleep(0.15)
        stopped = client.delete("/api/mesh/auto-sleep")

        assert stopped.status_code == 200
        assert len(serial.writes) >= 2
        assert all(payload == b"DEEP_SLEEP 20\n" for payload in serial.writes)
