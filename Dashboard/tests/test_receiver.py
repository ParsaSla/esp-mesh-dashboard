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


def test_soil_moisture_is_scaled_to_percentage():
    message = normalize_message(
        {
            **VALID_MESSAGE,
            "payload": {"S": 2048, "soil1.S": 4096, "soil0.S": 0, "humidity": 48},
            "units": {"S": "adc"},
        }
    )

    assert message["payload"] == {"S": 50.0, "soil1.S": 0.0, "soil0.S": 100.0, "humidity": 48.0}
    assert message["units"] == {"S": "%", "soil1.S": "%", "soil0.S": "%"}


def test_mesh_soil_moisture_is_scaled_to_percentage():
    packet = {
        "sender": "AA:BB:CC:DD:EE:02",
        "origin": "AA:BB:CC:DD:EE:01",
        "destination": "FF:FF:FF:FF:FF:FF",
        "sequence": 7,
        "ttl": 5,
        "hops": 1,
        "payload": '{"S":1024}',
    }

    message = normalize_message(packet)

    assert message["payload"] == {"S": 75.0}
    assert message["units"] == {"S": "%"}


def test_uv_adc_reading_uses_calibration_table():
    adc_for_mv = lambda millivolts: millivolts * 4095 / 3300
    message = normalize_message(
        {
            **VALID_MESSAGE,
            "payload": {
                "uv1.U": adc_for_mv(49),
                "U": adc_for_mv(227),
                "uv2.U": adc_for_mv((227 + 318) / 2),
                "uv3.U": 4095,
            },
        }
    )

    assert message["payload"]["uv1.U"] == 0.0
    assert message["payload"]["U"] == pytest.approx(1.0, abs=0.01)
    assert message["payload"]["uv2.U"] == pytest.approx(1.5, abs=0.01)
    assert message["payload"]["uv3.U"] == 11.0
    assert message["units"] == {"uv1.U": "UVI", "U": "UVI", "uv2.U": "UVI", "uv3.U": "UVI"}


def test_invalid_json_is_rejected():
    with pytest.raises(ValueError, match="Invalid JSON message"):
        normalize_message('{"nodeId": "node-001"')


def test_missing_payload_is_rejected():
    with pytest.raises(ValueError, match="Missing required field"):
        normalize_message({"nodeId": "node-001", "timestamp": "2026-10-07T10:15:30Z", "sequence": 1})


def test_duplicate_sequence_is_rejected():
    receiver = MeshReceiver()
    receiver.handle_message(VALID_MESSAGE)

    duplicate = {**VALID_MESSAGE}

    with pytest.raises(ValueError, match="Duplicate sequence and payload"):
        receiver.handle_message(duplicate)


def test_same_sequence_with_different_payload_is_accepted():
    receiver = MeshReceiver()
    receiver.handle_message(VALID_MESSAGE)

    updated = {
        **VALID_MESSAGE,
        "payload": {"temperature": 25.1, "humidity": 49},
    }
    event = receiver.handle_message(updated)

    assert event["sequence"] == VALID_MESSAGE["sequence"]
    assert event["payload"] == updated["payload"]
    assert receiver.get_node(VALID_MESSAGE["nodeId"])["payload"] == updated["payload"]
    assert len(receiver.get_history(VALID_MESSAGE["nodeId"])) == 2


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


def test_mesh_same_sequence_with_different_raw_payload_is_accepted():
    receiver = MeshReceiver()
    packet = {
        "sender": "AA:BB:CC:DD:EE:02",
        "origin": "AA:BB:CC:DD:EE:01",
        "destination": "FF:FF:FF:FF:FF:FF",
        "sequence": 8,
        "ttl": 5,
        "hops": 1,
        "payload": '{"U":2000}',
    }
    first = receiver.handle_message(packet)
    changed_payload = {**packet, "payload": '{"U":3000}'}
    second = receiver.handle_message(changed_payload)

    assert first["payload"] == second["payload"] == {"U": 11.0}
    assert second["packetPayload"] == changed_payload["payload"]
    assert len(receiver.get_history(packet["origin"])) == 2


def test_rssi_probe_payload_decodes_ordered_hops_and_unavailable_rssi():
    origin = bytes.fromhex("AABBCCDDEE01")
    relay = bytes.fromhex("AABBCCDDEE02")
    observer = bytes.fromhex("AABBCCDDEE03")
    payload = b"RSSI" + origin + bytes([0x80]) + relay + bytes([0xC4]) + observer + bytes([0xD0])
    packet = {
        "sender": "AA:BB:CC:DD:EE:02",
        "rssi": -60,
        "origin": "AA:BB:CC:DD:EE:01",
        "destination": "FF:FF:FF:FF:FF:FF",
        "sequence": 12,
        "ttl": 5,
        "hops": 2,
        "payload": payload.decode("latin-1"),
    }

    message = normalize_message(json.dumps(packet))

    assert message["type"] == "rssi_route"
    assert message["nodeId"] == "AA:BB:CC:DD:EE:01"
    assert message["route"] == [
        {"nodeId": "AA:BB:CC:DD:EE:01", "rssi": None},
        {"nodeId": "AA:BB:CC:DD:EE:02", "rssi": -60},
        {"nodeId": "AA:BB:CC:DD:EE:03", "rssi": -48},
    ]


def test_rssi_probe_payload_rejects_incomplete_hop_record():
    packet = {
        "sender": "AA:BB:CC:DD:EE:02",
        "rssi": -60,
        "origin": "AA:BB:CC:DD:EE:01",
        "destination": "FF:FF:FF:FF:FF:FF",
        "sequence": 12,
        "ttl": 5,
        "hops": 1,
        "payload": "RSSI" + "A" * 6,
    }

    with pytest.raises(ValueError, match="incomplete hop records"):
        normalize_message(packet)


def test_rssi_probe_origins_group_in_one_measurement_without_telemetry():
    mesh_receiver = MeshReceiver()
    measurement_id = mesh_receiver.begin_rssi_measurement()

    def probe(origin, relay):
        payload = b"RSSI" + bytes.fromhex(origin.replace(":", "")) + b"\x80"
        payload += bytes.fromhex(relay.replace(":", "")) + bytes([0xC0])
        return {
            "sender": relay,
            "rssi": -64,
            "origin": origin,
            "destination": "FF:FF:FF:FF:FF:FF",
            "sequence": 1,
            "ttl": 5,
            "hops": 1,
            "payload": payload.decode("latin-1"),
        }

    first = mesh_receiver.handle_message(probe("AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:10"))
    second = mesh_receiver.handle_message(probe("AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:11"))

    assert first["measurementId"] == measurement_id
    assert second["measurementId"] == measurement_id
    assert {first["origin"], second["origin"]} == {
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    }
    assert mesh_receiver.nodes == {}
    assert mesh_receiver.history.get("AA:BB:CC:DD:EE:01") == []
    assert len(mesh_receiver.active_rssi_measurement["routes"]) == 2


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
    assert 'id="measure-rssi-btn"' in dashboard.text
    assert 'id="rssi-route-results"' in dashboard.text
    assert 'id="hide-repeated-observer-rssi"' not in dashboard.text
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
    measure_rssi = client.post(
        "/api/mesh/commands",
        json={"command": "MEASURE_RSSI"},
    )

    assert request_data.status_code == 200
    assert request_data.json() == {"sent": True, "command": "SEND_DATA"}
    assert change_interval.status_code == 200
    assert change_interval.json() == {"sent": True, "command": "CHANGE_INTERVAL 5000"}
    assert deep_sleep.status_code == 200
    assert deep_sleep.json() == {"sent": True, "command": "DEEP_SLEEP 60000"}
    assert measure_rssi.status_code == 200
    assert measure_rssi.json()["sent"] is True
    assert measure_rssi.json()["command"] == "MEASURE_RSSI"
    assert isinstance(measure_rssi.json()["measurementId"], int)
    assert serial.writes == [
        b"SEND_DATA\n",
        b"CHANGE_INTERVAL 5000\n",
        b"DEEP_SLEEP 60000\n",
        b"MEASURE_RSSI\n",
    ]


def test_mesh_command_endpoint_rejects_invalid_or_offline_commands(monkeypatch):
    monkeypatch.setattr(receiver, "transport", None)
    monkeypatch.setattr(receiver, "connected", False)
    monkeypatch.setattr(receiver, "active_rssi_measurement", None)
    client = TestClient(app)

    assert client.post("/api/mesh/commands", json={"command": "SEND_DATA"}).status_code == 503
    assert client.post("/api/mesh/commands", json={"command": "MEASURE_RSSI"}).status_code == 503
    assert receiver.active_rssi_measurement is None
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
    assert client.post(
        "/api/mesh/commands",
        json={"command": "MEASURE_RSSI", "intervalMs": 1000},
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
