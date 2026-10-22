import json
import math
import re
from datetime import datetime, timezone


REQUIRED_FIELDS = {"nodeId", "timestamp", "sequence", "payload"}
BOOT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
ADC_MAX_COUNT = 4095.0
ADC_REFERENCE_MV = 3300.0
UV_CALIBRATION_MV = (
    (50.0, 0.0),
    (227.0, 1.0),
    (318.0, 2.0),
    (408.0, 3.0),
    (503.0, 4.0),
    (606.0, 5.0),
    (696.0, 6.0),
    (795.0, 7.0),
    (881.0, 8.0),
    (976.0, 9.0),
    (1079.0, 10.0),
    (1170.0, 11.0),
)


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


def _normalize_reading(key, value):
    numeric = _coerce_float(value, key)
    code = re.split(r"[._\s-]", str(key))[-1].upper()
    if code == "S":
        return (4096.0 - min(max(numeric, 0.0), 4096.0)) * (100.0 / 4096.0)
    if code == "U":
        millivolts = min(max(numeric, 0.0), ADC_MAX_COUNT) * ADC_REFERENCE_MV / ADC_MAX_COUNT
        if millivolts < UV_CALIBRATION_MV[0][0]:
            return 0.0
        for (lower_mv, lower_index), (upper_mv, upper_index) in zip(
            UV_CALIBRATION_MV, UV_CALIBRATION_MV[1:]
        ):
            if millivolts <= upper_mv:
                fraction = (millivolts - lower_mv) / (upper_mv - lower_mv)
                return lower_index + fraction * (upper_index - lower_index)
        return UV_CALIBRATION_MV[-1][1]
    return numeric


def _reading_unit(key):
    code = re.split(r"[._\s-]", str(key))[-1].upper()
    if code == "S":
        return "%"
    if code == "U":
        return "UVI"
    return None


def _validate_timestamp(timestamp_value):
    if not isinstance(timestamp_value, str) or not timestamp_value.strip():
        raise ValueError("Timestamp is required and must be a valid ISO 8601 string.")

    normalized = timestamp_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Timestamp is not a valid ISO 8601 string.") from exc

    return parsed.isoformat().replace("+00:00", "Z")


def _validate_boot_id(boot_id):
    if not isinstance(boot_id, str) or not BOOT_ID_RE.match(boot_id):
        raise ValueError("bootId must be 16 lowercase hex characters.")
    return boot_id


def _validate_rssi(rssi_value):
    if rssi_value is None:
        return None
    if isinstance(rssi_value, bool):
        raise ValueError("rssi must be numeric.")
    if isinstance(rssi_value, (int, float)) and rssi_value == -128:
        return None
    if isinstance(rssi_value, str) and rssi_value.strip() == "-128":
        return None
    return _coerce_float(rssi_value, "rssi")


def _validate_hops(hops_value):
    if isinstance(hops_value, bool):
        raise ValueError("hops must be an integer between 0 and 255.")
    try:
        hops = int(hops_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("hops must be an integer between 0 and 255.") from exc
    if hops < 0 or hops > 255:
        raise ValueError("hops must be an integer between 0 and 255.")
    if isinstance(hops_value, float) and hops_value != hops:
        raise ValueError("hops must be an integer between 0 and 255.")
    return hops


def _validate_units(units_value):
    if not isinstance(units_value, dict):
        raise ValueError("units must be a dictionary mapping keys to unit strings.")
    return {str(k): str(v) for k, v in units_value.items()}


def _validate_mesh_address(value, field_name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
        raise ValueError(f"{field_name} must be a six-byte MAC address.")
    return value.upper()


def _decode_rssi_probe_payload(payload_text):
    if not payload_text.startswith("RSSI"):
        return None

    try:
        payload = payload_text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError("RSSI probe payload contains a non-byte character.") from exc

    marker_size = 4
    hop_size = 7
    if len(payload) < marker_size + hop_size or (len(payload) - marker_size) % hop_size:
        raise ValueError("RSSI probe payload contains incomplete hop records.")

    route = []
    for offset in range(marker_size, len(payload), hop_size):
        mac = ":".join(f"{byte:02X}" for byte in payload[offset : offset + 6])
        rssi = payload[offset + 6]
        if rssi >= 128:
            rssi -= 256
        route.append({"nodeId": mac, "rssi": None if rssi == -128 else rssi})
    return route


def convert_mesh_message(raw_message):
    message = _parse_message(raw_message)
    required_fields = {"sender", "origin", "destination", "sequence", "ttl", "hops", "payload"}
    missing = required_fields - set(message)
    if missing:
        raise ValueError(f"Missing required mesh field(s): {', '.join(sorted(missing))}")

    try:
        sequence = int(message["sequence"])
    except (TypeError, ValueError) as exc:
        raise ValueError("sequence must be a non-negative integer.") from exc
    if sequence < 0 or (isinstance(message["sequence"], float) and message["sequence"] != sequence):
        raise ValueError("sequence must be a non-negative integer.")

    payload_text = message["payload"]
    if not isinstance(payload_text, str):
        raise ValueError("payload must be a string.")

    route = _decode_rssi_probe_payload(payload_text)
    if route is not None:
        _validate_mesh_address(message["sender"], "sender")
        origin = _validate_mesh_address(message["origin"], "origin")
        _validate_mesh_address(message["destination"], "destination")
        _validate_hops(message["ttl"])
        _validate_hops(message["hops"])
        if route[0]["nodeId"] != origin:
            raise ValueError("RSSI probe origin does not match its first hop.")
        return {
            "type": "rssi_route",
            "nodeId": origin,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sequence": sequence,
            "route": route,
        }

    payload = {}
    try:
        decoded_payload = json.loads(payload_text)
    except json.JSONDecodeError:
        decoded_payload = None
    if isinstance(decoded_payload, dict):
        for key, value in decoded_payload.items():
            try:
                payload[str(key)] = _normalize_reading(key, value)
            except ValueError:
                continue

    envelope = {
        "nodeId": _validate_mesh_address(message["origin"], "origin"),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sequence": sequence,
        "payload": payload,
        "sender": _validate_mesh_address(message["sender"], "sender"),
        "destination": _validate_mesh_address(message["destination"], "destination"),
        "ttl": _validate_hops(message["ttl"]),
        "hops": _validate_hops(message["hops"]),
        "packetPayload": payload_text,
        "rssiSourceNodeId": _validate_mesh_address(message["sender"], "sender"),
        "rssi": _validate_rssi(message.get("rssi")),
    }
    units = {key: _reading_unit(key) for key in payload}
    units = {key: unit for key, unit in units.items() if unit is not None}
    if units:
        envelope["units"] = units
    return envelope


def convert_gateway_frame(raw_frame):
    frame = _parse_message(raw_frame)
    if not isinstance(frame, dict):
        raise ValueError("Gateway frame must be a JSON object.")

    if "origin" not in frame:
        raise ValueError("Missing required origin in gateway frame.")
    origin = str(frame["origin"]).strip()
    if not origin:
        raise ValueError("origin must be a non-empty string.")

    if "sequence" not in frame:
        raise ValueError("Missing required sequence in gateway frame.")
    try:
        sequence = int(frame["sequence"])
    except (TypeError, ValueError) as exc:
        raise ValueError("sequence must be a non-negative integer.") from exc
    if sequence < 0 or (isinstance(frame["sequence"], float) and frame["sequence"] != sequence):
        raise ValueError("sequence must be a non-negative integer.")

    readings = frame.get("readings")
    if not isinstance(readings, list):
        raise ValueError("Gateway frame readings must be a list.")

    payload = {}
    units = {}
    seen_keys = set()

    for item in readings:
        if not isinstance(item, dict):
            continue
        sensor_id = str(item.get("sensorId", "")).strip()
        channel = str(item.get("channel", "")).strip()
        if not sensor_id or not channel:
            continue
        key = f"{sensor_id}.{channel}"
        if key in seen_keys:
            raise ValueError(f"Duplicate metric key in gateway frame: {key}")
        seen_keys.add(key)

        val = item.get("value")
        if val is None or isinstance(val, bool):
            continue
        try:
            numeric_val = _normalize_reading(key, val)
            if not math.isfinite(numeric_val):
                continue
            payload[key] = numeric_val
            unit_val = item.get("unit")
            if unit_val is not None:
                units[key] = str(unit_val)
            unit = _reading_unit(key)
            if unit is not None:
                units[key] = unit
        except (ValueError, TypeError):
            continue

    if not payload:
        raise ValueError("No valid numeric readings in gateway frame.")

    envelope = {
        "nodeId": origin,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sequence": sequence,
        "payload": payload,
    }
    if units:
        envelope["units"] = units

    if "bootId" in frame and frame["bootId"] is not None:
        envelope["bootId"] = _validate_boot_id(frame["bootId"])

    if "rssi" in frame:
        rssi = _validate_rssi(frame["rssi"])
        if rssi is not None:
            envelope["rssi"] = rssi

    if "rssiSourceNodeId" in frame and frame["rssiSourceNodeId"] is not None:
        source_id = str(frame["rssiSourceNodeId"]).strip()
        if not source_id:
            raise ValueError("rssiSourceNodeId must be a non-empty string.")
        envelope["rssiSourceNodeId"] = source_id

    if "hops" in frame and frame["hops"] is not None:
        envelope["hops"] = _validate_hops(frame["hops"])

    return envelope


def normalize_message(raw_message):
    message = _parse_message(raw_message)

    if not isinstance(message, dict):
        raise ValueError("Message must be a JSON object.")

    if "sender" in message or "destination" in message or "ttl" in message:
        return convert_mesh_message(message)

    # Detect gateway NDJSON frame
    if ("origin" in message or "readings" in message) and "nodeId" not in message:
        return convert_gateway_frame(message)

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
        normalized_payload[str(key)] = _normalize_reading(key, value)

    normalized["payload"] = normalized_payload

    if "bootId" in message and message["bootId"] is not None:
        normalized["bootId"] = _validate_boot_id(message["bootId"])

    if "rssi" in message:
        rssi = _validate_rssi(message["rssi"])
        if rssi is not None:
            normalized["rssi"] = rssi

    if "rssiSourceNodeId" in message and message["rssiSourceNodeId"] is not None:
        source_id = str(message["rssiSourceNodeId"]).strip()
        if not source_id:
            raise ValueError("rssiSourceNodeId must be a non-empty string.")
        normalized["rssiSourceNodeId"] = source_id

    if "hops" in message and message["hops"] is not None:
        normalized["hops"] = _validate_hops(message["hops"])

    if "units" in message and message["units"] is not None:
        normalized["units"] = _validate_units(message["units"])
    reading_units = {
        key: unit
        for key in normalized_payload
        if (unit := _reading_unit(key)) is not None
    }
    if reading_units:
        normalized.setdefault("units", {}).update(reading_units)

    for extra_key in ("meshId", "batteryLevel", "firmwareVersion", "role"):
        if extra_key in message and extra_key not in normalized:
            normalized[extra_key] = message[extra_key]

    return normalized
