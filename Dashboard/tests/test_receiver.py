import json

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
