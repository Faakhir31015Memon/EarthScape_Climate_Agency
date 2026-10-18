"""Request validation for the datasets API (DRF serializers, no ORM).

MongoDB documents are validated by ``common/validators.py`` and the repository
layer, so these serializers only shape *incoming requests* - they never touch the
database directly.
"""
from __future__ import annotations

from rest_framework import serializers

from common import validators

# A column mapping may target any canonical field the importer understands.
MAPPABLE_FIELDS = list(validators.RANGE_LIMITS) + [
    "observation_date", "latitude", "longitude", "location_code",
    "location_name", "region_code", "country", "provider_record_id",
]

SOURCE_TYPES = ("nasa_power", "noaa", "live_api", "university_upload", "sample")
DATASET_STATUSES = ("draft", "validating", "validated", "ingesting", "ready", "failed", "archived")


class ColumnSuggestionSerializer(serializers.Serializer):
    """Used by the upload wizard before a file exists (``column-suggestion``)."""

    headers = serializers.ListField(child=serializers.CharField(max_length=200),
                                    min_length=1, max_length=400, allow_empty=False)
    source_hint = serializers.CharField(required=False, allow_blank=True, max_length=60)

    def validate_headers(self, value):
        cleaned = []
        seen = set()
        for header in value:
            text = str(header).strip()[:200]
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if not cleaned:
            raise serializers.ValidationError("Provide at least one non-empty header.")
        return cleaned


class ColumnMappingSerializer(serializers.Serializer):
    """Persists a user-confirmed ``{canonical_field: source_header}`` mapping."""

    mapping = serializers.DictField(child=serializers.CharField(max_length=200))
    units = serializers.DictField(child=serializers.CharField(max_length=40), required=False)
    location_defaults = serializers.DictField(required=False)

    def validate_mapping(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("mapping must be an object.")
        cleaned = {}
        for field_name, header in value.items():
            if field_name not in MAPPABLE_FIELDS:
                raise serializers.ValidationError(f"Unknown canonical field '{field_name}'.")
            text = str(header).strip()
            if not text:
                continue
            cleaned[field_name] = text
        if "observation_date" not in cleaned:
            raise serializers.ValidationError(
                "A mapping must target 'observation_date'; observations cannot be stored "
                "without a date column.")
        return cleaned


class DatasetUpdateSerializer(serializers.Serializer):
    """PATCH fields for a dataset document."""

    name = serializers.CharField(required=False, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False,
                                 max_length=40)
    is_public = serializers.BooleanField(required=False)
    units = serializers.DictField(child=serializers.CharField(max_length=40), required=False)
    geographic_coverage = serializers.DictField(required=False)


class DatasetUploadMetadataSerializer(serializers.Serializer):
    """Validated metadata supplied alongside the multipart upload."""

    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    source_type = serializers.ChoiceField(choices=SOURCE_TYPES, required=False,
                                          default="university_upload")
    source_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    source_url = serializers.CharField(required=False, allow_blank=True, max_length=500)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False,
                                 max_length=40)
    units = serializers.DictField(child=serializers.CharField(max_length=40), required=False)
    mapping = serializers.DictField(child=serializers.CharField(max_length=200), required=False)
    is_public = serializers.BooleanField(required=False, default=False)
    default_location_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    default_region_code = serializers.CharField(required=False, allow_blank=True, max_length=80)
    default_country = serializers.CharField(required=False, allow_blank=True, max_length=80)
    default_latitude = serializers.FloatField(required=False)
    default_longitude = serializers.FloatField(required=False)


def coerce_list(value) -> list:
    """Accept a repeated form field, a JSON list, or a comma-separated string."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        import json

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except (ValueError, TypeError):
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


def coerce_json_object(value):
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    import json

    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}
