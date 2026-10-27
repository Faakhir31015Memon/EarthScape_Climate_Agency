"""Write-payload validation for anomaly scans and recompute.
Reads use bounded query params in common.views instead.
"""
from __future__ import annotations

from rest_framework import serializers

from common import analytics, validators

METRIC_CHOICES = tuple(analytics.METRICS)
FREQUENCY_CHOICES = ("daily", "weekly", "monthly", "annual")
GROUP_BY_CHOICES = ("none", "region", "location", "country")
ANOMALY_METHODS = ("statistical_baseline", "threshold", "isolation_forest")
DIRECTIONS = ("above", "below", "both")
QUALITY_CHOICES = ("clean", "flagged", "any")


def _clean_metric(value: str) -> str:
    metric = (value or "").strip()
    if metric not in analytics.METRICS:
        raise serializers.ValidationError(
            f"Unknown metric '{metric}'. Supported: {', '.join(analytics.METRICS)}."
        )
    return metric


class AnomalyScanSerializer(serializers.Serializer):
    """Body for POST /api/v1/climate/anomalies/scan."""

    metric = serializers.CharField(max_length=40, default="temperature_c", required=False)
    method = serializers.ChoiceField(choices=ANOMALY_METHODS, default="statistical_baseline",
                                     required=False)
    threshold_sigma = serializers.FloatField(default=2.5, min_value=1.0, max_value=10.0,
                                             required=False)
    absolute_threshold = serializers.FloatField(required=False, allow_null=True, min_value=0.0)
    direction = serializers.ChoiceField(choices=DIRECTIONS, default="both", required=False)
    region = serializers.CharField(max_length=80, required=False, allow_blank=True)
    location = serializers.CharField(max_length=20, required=False, allow_blank=True)
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    limit_rows = serializers.IntegerField(required=False, min_value=100,
                                          max_value=analytics.MAX_QUERY_ROWS)
    persist_limit = serializers.IntegerField(default=200, min_value=1, max_value=1000,
                                             required=False)
    dry_run = serializers.BooleanField(default=False, required=False)

    def validate_metric(self, value):
        return _clean_metric(value)

    def validate(self, attrs):
        if attrs.get("method") == "threshold" and attrs.get("absolute_threshold") is None:
            raise serializers.ValidationError(
                {"absolute_threshold": "Threshold detection needs an absolute_threshold value."}
            )
        start, end = attrs.get("start"), attrs.get("end")
        if start and end and start > end:
            raise serializers.ValidationError({"start": "start must be on or before end."})
        return attrs


class RecomputeSerializer(serializers.Serializer):
    """Optional body for POST /api/v1/analytics/recompute."""

    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    metric = serializers.CharField(max_length=40, required=False)
    limit_rows = serializers.IntegerField(required=False, min_value=100,
                                          max_value=analytics.MAX_QUERY_ROWS)
    processing_mode = serializers.ChoiceField(choices=("mongodb", "pyspark", "mapreduce"),
                                              default="mongodb", required=False)

    def validate_metric(self, value):
        return value if not value else _clean_metric(value)


def _bounded_mapping(value, *, max_keys: int, label: str):
    if not isinstance(value, dict):
        raise serializers.ValidationError(f"{label} must be an object.")
    if len(value) > max_keys:
        raise serializers.ValidationError(f"{label} may hold at most {max_keys} keys.")
    cleaned = {}
    for key, item in value.items():
        key_text = str(key)[:60]
        cleaned[key_text] = _bounded_value(item, label=key_text)
    return cleaned


def _bounded_value(item, *, label: str, depth: int = 0):
    if isinstance(item, str):
        return item[:400]
    if isinstance(item, (int, float, bool)) or item is None:
        return item
    if depth >= 3:
        raise serializers.ValidationError(f"'{label}' is nested too deeply (max 3 levels).")
    if isinstance(item, (list, tuple)):
        if len(item) > 50:
            raise serializers.ValidationError(f"'{label}' may hold at most 50 items.")
        return [_bounded_value(entry, label=label, depth=depth + 1) for entry in item]
    if isinstance(item, dict):
        if len(item) > 30:
            raise serializers.ValidationError(f"'{label}' may hold at most 30 keys.")
        return {str(key)[:60]: _bounded_value(value, label=str(key), depth=depth + 1)
                for key, value in item.items()}
    raise serializers.ValidationError(f"'{label}' must contain JSON-compatible values only.")


def metric_or_400(metric: str):
    """Validate a metric name from a query string; raises ApiError."""
    from common.api import ApiError

    cleaned = (metric or "").strip()
    if cleaned not in analytics.METRICS:
        raise ApiError("validation_failed",
                       f"metric must be one of: {', '.join(analytics.METRICS)}.")
    return cleaned


def range_limits(metric: str):
    low, high = validators.RANGE_LIMITS[metric]
    return {"min": low, "max": high}
