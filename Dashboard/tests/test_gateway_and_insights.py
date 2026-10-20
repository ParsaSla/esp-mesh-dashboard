import math
import pytest
from fastapi.testclient import TestClient

from insights import calculate_insights
from main import app, receiver
from mesh_protocol import convert_gateway_frame, normalize_message
from receiver import MeshReceiver


SAMPLE_GATEWAY_FRAME = {
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


def test_gateway_frame_conversion_and_normalization():
    envelope = convert_gateway_frame(SAMPLE_GATEWAY_FRAME)
    assert envelope["nodeId"] == "AA:BB:CC:DD:EE:FF"
    assert envelope["sequence"] == 42
    assert envelope["bootId"] == "0123456789abcdef"
    assert envelope["hops"] == 1
    assert envelope["rssi"] == -61.0
    assert envelope["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:FF"
    assert envelope["payload"] == {"ldr1.raw": 1234.0}
    assert envelope["units"] == {"ldr1.raw": "adc_counts"}
    assert "timestamp" in envelope

    # Also test normalize_message directly accepts gateway frame dict
    normalized = normalize_message(SAMPLE_GATEWAY_FRAME)
    assert normalized["nodeId"] == "AA:BB:CC:DD:EE:FF"
    assert normalized["payload"]["ldr1.raw"] == 1234.0


def test_gateway_frame_rssi_unavailable_when_minus_128():
    frame = {
        **SAMPLE_GATEWAY_FRAME,
        "rssi": -128,
    }
    envelope = convert_gateway_frame(frame)
    assert "rssi" not in envelope or envelope.get("rssi") is None


def test_gateway_frame_rejects_duplicate_metric_keys():
    frame = {
        **SAMPLE_GATEWAY_FRAME,
        "readings": [
            {"sensorId": "ldr1", "channel": "raw", "value": 100, "unit": "adc_counts"},
            {"sensorId": "ldr1", "channel": "raw", "value": 200, "unit": "adc_counts"},
        ],
    }
    with pytest.raises(ValueError, match="Duplicate metric key"):
        convert_gateway_frame(frame)


def test_gateway_frame_skips_non_finite_and_rejects_empty():
    frame_null = {
        **SAMPLE_GATEWAY_FRAME,
        "readings": [
            {"sensorId": "ldr1", "channel": "raw", "value": None},
            {"sensorId": "ldr2", "channel": "raw", "value": float("nan")},
        ],
    }
    with pytest.raises(ValueError, match="No valid numeric readings"):
        convert_gateway_frame(frame_null)

    # If at least one is valid, non-finite is skipped and valid is retained
    frame_partial = {
        **SAMPLE_GATEWAY_FRAME,
        "readings": [
            {"sensorId": "ldr1", "channel": "raw", "value": None},
            {"sensorId": "ldr2", "channel": "raw", "value": 456},
        ],
    }
    converted = convert_gateway_frame(frame_partial)
    assert converted["payload"] == {"ldr2.raw": 456.0}


def test_same_boot_duplicate_and_out_of_order_rejection():
    rx = MeshReceiver()
    rx.handle_message(SAMPLE_GATEWAY_FRAME)

    # Same bootId, duplicate sequence
    dup = {**SAMPLE_GATEWAY_FRAME, "sequence": 42}
    with pytest.raises(ValueError, match="Duplicate or out-of-order sequence"):
        rx.handle_message(dup)

    # Same bootId, lower sequence
    lower = {**SAMPLE_GATEWAY_FRAME, "sequence": 41}
    with pytest.raises(ValueError, match="Duplicate or out-of-order sequence"):
        rx.handle_message(lower)


def test_new_boot_sequence_reset_allowed():
    rx = MeshReceiver()
    rx.handle_message(SAMPLE_GATEWAY_FRAME)  # sequence 42, bootId 0123456789abcdef

    # Node rebooted with new bootId, sequence restarts at 0
    reboot = {
        **SAMPLE_GATEWAY_FRAME,
        "bootId": "fedcba9876543210",
        "sequence": 0,
    }
    event = rx.handle_message(reboot)
    assert event["sequence"] == 0
    assert event["bootId"] == "fedcba9876543210"
    node = rx.get_node("AA:BB:CC:DD:EE:FF")
    assert node["sequence"] == 0
    assert node["bootId"] == "fedcba9876543210"


def test_optional_metadata_propagation():
    rx = MeshReceiver()
    event = rx.handle_message(SAMPLE_GATEWAY_FRAME)

    assert event["bootId"] == "0123456789abcdef"
    assert event["rssi"] == -61.0
    assert event["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:FF"
    assert event["hops"] == 1
    assert event["units"] == {"ldr1.raw": "adc_counts"}

    history = rx.get_history("AA:BB:CC:DD:EE:FF")
    assert len(history) == 1
    sample = history[0]
    assert sample["bootId"] == "0123456789abcdef"
    assert sample["rssi"] == -61.0
    assert sample["rssiSourceNodeId"] == "AA:BB:CC:DD:EE:FF"
    assert sample["hops"] == 1
    assert sample["units"] == {"ldr1.raw": "adc_counts"}
    assert sample["ldr1.raw"] == 1234.0


def test_insight_algorithm_requirements():
    # 1. 19 finite values => no insight
    history_19 = [{"timestamp": f"2026-10-16T12:{i:02d}:00Z", "ldr1.raw": 100.0} for i in range(19)]
    res_19 = calculate_insights("node-01", history_19)
    assert res_19["sampleCount"] == 19
    assert res_19["insights"] == []

    # 2. 10 values at 100 followed by 10 at 120 => rising trend delta 20
    history_trend = (
        [{"timestamp": f"2026-10-16T12:{i:02d}:00Z", "ldr1.raw": 100.0} for i in range(10)]
        + [{"timestamp": f"2026-10-16T12:{i + 10:02d}:00Z", "ldr1.raw": 120.0} for i in range(10)]
    )
    res_trend = calculate_insights("node-01", history_trend)
    assert res_trend["sampleCount"] == 20
    trends = [ins for ins in res_trend["insights"] if ins["kind"] == "trend"]
    assert len(trends) == 1
    trend = trends[0]
    assert trend["metric"] == "ldr1.raw"
    assert trend["direction"] == "rising"
    assert trend["delta"] == 20.0
    assert trend["comparisonSamples"] == 20

    # 3. Constant 20-sample baseline followed by 500 => anomaly warning
    history_anomaly = (
        [{"timestamp": f"2026-10-16T12:{i:02d}:00Z", "ldr1.raw": 100.0} for i in range(20)]
        + [{"timestamp": "2026-10-16T12:20:00Z", "ldr1.raw": 500.0}]
    )
    res_anomaly = calculate_insights("node-01", history_anomaly)
    anomalies = [ins for ins in res_anomaly["insights"] if ins["kind"] == "anomaly"]
    assert len(anomalies) == 1
    anom = anomalies[0]
    assert anom["kind"] == "anomaly"
    assert anom["metric"] == "ldr1.raw"
    assert anom["severity"] == "warning"
    assert anom["value"] == 500.0
    assert anom["mean"] == 100.0
    assert anom["stddev"] == 0.0
    assert anom["baselineSamples"] == 20


def test_api_insights_endpoint():
    client = TestClient(app)
    receiver.nodes.clear()
    receiver.history.clear()

    # 404 for unknown node
    res_404 = client.get("/nodes/unknown-node/insights")
    assert res_404.status_code == 404
    assert res_404.json()["detail"] == "Node not found"

    # Known node with fewer than 20 samples returns 200 with empty insights and actual sample count
    receiver.handle_message(SAMPLE_GATEWAY_FRAME)
    res_200 = client.get("/nodes/AA:BB:CC:DD:EE:FF/insights")
    assert res_200.status_code == 200
    data = res_200.json()
    assert data["nodeId"] == "AA:BB:CC:DD:EE:FF"
    assert "generatedAt" in data
    assert data["sampleCount"] == 1
    assert data["insights"] == []
