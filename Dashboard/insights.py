import math
from datetime import datetime, timezone


METADATA_KEYS = {
    "timestamp",
    "sequence",
    "bootId",
    "rssi",
    "rssiSourceNodeId",
    "hops",
    "units",
    "meshId",
    "batteryLevel",
    "firmwareVersion",
    "role",
    "sender",
    "destination",
    "ttl",
    "packetPayload",
    "__t",
}


def _extract_metric_keys(history):
    keys = set()
    for sample in history:
        for k in sample:
            if k not in METADATA_KEYS:
                keys.add(k)
    return sorted(keys)


def _find_unit(history, metric, node=None):
    for sample in reversed(history):
        units = sample.get("units")
        if isinstance(units, dict) and metric in units:
            return str(units[metric])
    if node and isinstance(node.get("units"), dict) and metric in node["units"]:
        return str(node["units"][metric])
    return None


def calculate_insights(node_id: str, history: list, node: dict | None = None) -> dict:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_count = len(history) if history else 0

    if not history:
        return {
            "nodeId": node_id,
            "generatedAt": generated_at,
            "sampleCount": 0,
            "insights": [],
        }

    metric_keys = _extract_metric_keys(history)
    insights = []

    for metric in metric_keys:
        values = []
        for s in history:
            val = s.get(metric)
            if val is not None and not isinstance(val, bool) and isinstance(val, (int, float)):
                if math.isfinite(val):
                    values.append(float(val))

        if not values:
            continue

        unit = _find_unit(history, metric, node)
        unit_str = f" {unit}" if unit else ""

        # --- Anomaly Calculation ---
        # Compare newest finite value against up to preceding 100 finite samples;
        # require at least 20 baseline samples.
        if len(values) >= 21:  # at least 1 newest + 20 baseline
            newest = values[-1]
            baseline = values[max(0, len(values) - 1 - 100) : len(values) - 1]
            if len(baseline) >= 20:
                mean = sum(baseline) / len(baseline)
                variance = sum((x - mean) ** 2 for x in baseline) / len(baseline)
                stddev = math.sqrt(variance)
                deviation = abs(newest - mean)

                flagged = False
                if stddev > 0:
                    flagged = deviation > 2.0 * stddev
                else:
                    flagged = deviation > 1e-6
                if flagged:
                    direction_text = "unusually high" if newest > mean else "unusually low"
                    msg = (
                        f"{metric} reading ({newest:g}{unit_str}) is {direction_text} "
                        f"relative to baseline mean of {mean:g}{unit_str}."
                    )
                    insights.append({
                        "kind": "anomaly",
                        "metric": metric,
                        "unit": unit,
                        "severity": "warning",
                        "value": round(newest, 4),
                        "mean": round(mean, 4),
                        "stddev": round(stddev, 4),
                        "baselineSamples": len(baseline),
                        "message": msg,
                    })

        # --- Trend Calculation ---
        # Require 20 finite samples; compare mean of preceding 10 to mean of latest 10.
        if len(values) >= 20:
            window = values[-20:]
            prior = window[:10]
            recent = window[10:]
            prior_mean = sum(prior) / 10.0
            recent_mean = sum(recent) / 10.0
            delta = recent_mean - prior_mean
            threshold = max(1e-9, 0.01 * max(abs(prior_mean), abs(recent_mean)))

            if delta > threshold:
                direction = "rising"
                msg = f"{metric} trend is rising (mean changed by +{delta:g}{unit_str} over the last 20 samples)."
            elif delta < -threshold:
                direction = "falling"
                msg = f"{metric} trend is falling (mean changed by {delta:g}{unit_str} over the last 20 samples)."
            else:
                direction = "steady"
                msg = f"{metric} is steady over the last 20 samples."

            insights.append({
                "kind": "trend",
                "metric": metric,
                "unit": unit,
                "direction": direction,
                "delta": round(delta, 4),
                "comparisonSamples": 20,
                "message": msg,
            })

    return {
        "nodeId": node_id,
        "generatedAt": generated_at,
        "sampleCount": sample_count,
        "insights": insights,
    }
