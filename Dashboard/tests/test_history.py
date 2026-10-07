from history import HistoryBuffer


def test_history_keeps_latest_samples_by_limit():
    history = HistoryBuffer(limit=3)

    for index in range(5):
        history.add("node-001", {"timestamp": f"2026-10-07T10:00:{index:02d}Z", "sequence": index, "temperature": 20 + index})

    samples = history.get("node-001")
    assert len(samples) == 3
    assert samples[0]["sequence"] == 2
    assert samples[-1]["sequence"] == 4


def test_history_filters_by_since_and_limit():
    history = HistoryBuffer(limit=10)

    for index in range(4):
        history.add("node-001", {"timestamp": f"2026-10-07T10:00:{index:02d}Z", "sequence": index, "humidity": 30 + index})

    samples = history.get("node-001", since="2026-10-07T10:00:01Z", limit=2)
    assert len(samples) == 2
    assert samples[0]["sequence"] == 2
    assert samples[1]["sequence"] == 3
