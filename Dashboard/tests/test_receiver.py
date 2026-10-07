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


def test_api_endpoints_expose_latest_node_state():
    client = TestClient(app)
    receiver.nodes.clear()
    receiver.history.clear()
    receiver.handle_message(VALID_MESSAGE)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "ESP32 Mesh Dashboard" in dashboard.text
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
