import json
import math
from datetime import datetime


REQUIRED_FIELDS = {"nodeId", "timestamp", "sequence", "payload"}


def _parse_message(raw_message):
    if isinstance(raw_message, dict):
        return raw_message

    if isinstance(raw_message, str):
        text = raw_message.strip()
        if not text:
            raise ValueError("Empty message received.")

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON message.") from exc

    raise ValueError("Unsupported message format.")


def _coerce_float(value, field_name):
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric.")

    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be numeric.") from exc
    else:
        raise ValueError(f"{field_name} must be numeric.")

    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite.")

    return numeric


def _validate_timestamp(timestamp_value):
    if not isinstance(timestamp_value, str) or not timestamp_value.strip():
        raise ValueError("Timestamp is required and must be a valid ISO 8601 string.")

    normalized = timestamp_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Timestamp is not a valid ISO 8601 string.") from exc

    return parsed.isoformat().replace("+00:00", "Z")


def normalize_message(raw_message):
    message = _parse_message(raw_message)

    if not isinstance(message, dict):
        raise ValueError("Message must be a JSON object.")

    missing = REQUIRED_FIELDS - set(message)
    if missing:
        missing_fields = ", ".join(sorted(missing))
        raise ValueError(f"Missing required field(s): {missing_fields}")

    node_id = str(message["nodeId"]).strip()
    if not node_id:
        raise ValueError("nodeId must be a non-empty string.")

    normalized = {
        "nodeId": node_id,
        "timestamp": _validate_timestamp(message["timestamp"]),
        "sequence": message["sequence"],
        "payload": message["payload"],
    }

    try:
        sequence = int(message["sequence"])
    except (TypeError, ValueError) as exc:
        raise ValueError("sequence must be a non-negative integer.") from exc

    if sequence < 0 or str(sequence) != str(message["sequence"]).strip():
        if not isinstance(message["sequence"], (int, float)) or float(message["sequence"]) % 1 != 0:
            raise ValueError("sequence must be a non-negative integer.")

    normalized["sequence"] = sequence

    if not isinstance(message["payload"], dict) or not message["payload"]:
        raise ValueError("payload must be a non-empty object.")

    normalized_payload = {}
    for key, value in message["payload"].items():
        normalized_payload[str(key)] = _coerce_float(value, key)

    normalized["payload"] = normalized_payload

    for extra_key in ("meshId", "rssi", "batteryLevel", "firmwareVersion", "role"):
        if extra_key in message:
            normalized[extra_key] = message[extra_key]

    return normalized
