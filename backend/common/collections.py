"""MongoDB collection contracts: names, indexes, validators and the data dictionary.

``earthscape_climate`` is the application database. This module is the single
source of truth for collection structure; ``backend/scripts/migrate_schema.py``
applies it idempotently and records the applied schema version.
"""
from __future__ import annotations

from typing import Any, Dict, List

SCHEMA_VERSION = 3

COLLECTION_NAMES: List[str] = [
    "users",
    "refresh_tokens",
    "regions",
    "datasets",
    "ingestion_jobs",
    "climate_observations",
    "analytics_summaries",
    "processing_jobs",
    "model_registry",
    "predictions",
    "alerts",
    "notifications",
    "reports",
    "saved_analyses",
    "audit_logs",
    "support_tickets",
    "feedback",
    "platform_settings",
]

ROLES: List[str] = ["administrator", "climate_analyst", "viewer"]

# Canonical observation measurement fields. Units are encoded in the field name so
# that a value can never be ambiguous. A measurement that was not supplied by the
# source is stored as ``None`` - never as zero.
MEASUREMENT_FIELDS: Dict[str, str] = {
    "temperature_c": "celsius (2 m air temperature)",
    "temperature_min_c": "celsius",
    "temperature_max_c": "celsius",
    "precipitation_mm": "millimetres",
    "relative_humidity_pct": "percent",
    "wind_speed_ms": "metres per second",
    "wind_direction_deg": "degrees from true north",
    "surface_pressure_hpa": "hectopascal",
    "cloud_coverage_pct": "percent",
    "solar_radiation_kwh_m2": "kilowatt hours per square metre",
}

INDEX_SPECS: Dict[str, List[Dict[str, Any]]] = {
    "users": [
        {"keys": [("email_normalized", 1)], "unique": True, "name": "ux_users_email"},
        {"keys": [("role", 1), ("is_active", 1)], "name": "ix_users_role_active"},
        {"keys": [("created_at", -1)], "name": "ix_users_created"},
    ],
    "refresh_tokens": [
        {"keys": [("token_hash", 1)], "unique": True, "name": "ux_refresh_hash"},
        {"keys": [("user_id", 1), ("revoked_at", 1)], "name": "ix_refresh_user"},
        # Only genuinely expiring records use a TTL index.
        {"keys": [("expires_at", 1)], "expireAfterSeconds": 0, "name": "ttl_refresh_expiry"},
    ],
    "regions": [
        {"keys": [("code", 1)], "unique": True, "name": "ux_regions_code"},
        {"keys": [("country", 1)], "name": "ix_regions_country"},
    ],
    "datasets": [
        {"keys": [("name", 1), ("source_type", 1)], "unique": True, "name": "ux_datasets_name_source"},
        {"keys": [("status", 1), ("created_at", -1)], "name": "ix_datasets_status"},
        {"keys": [("source_type", 1)], "name": "ix_datasets_source_type"},
        {"keys": [("uploaded_by", 1)], "name": "ix_datasets_uploader"},
    ],
    "ingestion_jobs": [
        {"keys": [("status", 1), ("started_at", -1)], "name": "ix_ingestion_status"},
        {"keys": [("dataset_id", 1)], "name": "ix_ingestion_dataset"},
        {"keys": [("source_name", 1), ("completed_at", -1)], "name": "ix_ingestion_source"},
    ],
    "climate_observations": [
        # Idempotent ingestion: one document per dataset + location + calendar day.
        {"keys": [("dedupe_key", 1)], "unique": True, "name": "ux_obs_dedupe"},
        {"keys": [("observation_date", -1), ("region_code", 1)], "name": "ix_obs_date_region"},
        {"keys": [("region_code", 1), ("year", 1), ("month", 1)], "name": "ix_obs_region_year_month"},
        {"keys": [("location_code", 1), ("observation_date", -1)], "name": "ix_obs_location_date"},
        {"keys": [("dataset_id", 1)], "name": "ix_obs_dataset"},
        {"keys": [("coordinates", "2dsphere")], "name": "ix_obs_geospatial", "sparse": True},
        {"keys": [("quality_flags", 1)], "name": "ix_obs_quality", "sparse": True},
    ],
    "analytics_summaries": [
        {"keys": [("summary_key", 1)], "unique": True, "name": "ux_summary_key"},
        {"keys": [("metric", 1), ("computed_at", -1)], "name": "ix_summary_metric"},
    ],
    "processing_jobs": [
        {"keys": [("status", 1), ("submitted_at", -1)], "name": "ix_processing_status"},
        {"keys": [("dataset_id", 1)], "name": "ix_processing_dataset"},
        {"keys": [("workflow", 1)], "name": "ix_processing_workflow"},
    ],
    "model_registry": [
        {"keys": [("name", 1), ("version", 1)], "unique": True, "name": "ux_model_name_version"},
        {"keys": [("status", 1), ("target_variable", 1)], "name": "ix_model_status_target"},
    ],
    "predictions": [
        {"keys": [("model_id", 1), ("generated_at", -1)], "name": "ix_predictions_model"},
        {"keys": [("target_variable", 1), ("region_code", 1), ("forecast_date", 1)], "name": "ix_pred_scope"},
    ],
    "alerts": [
        {"keys": [("rule_key", 1), ("triggered_at", -1)], "name": "ix_alerts_rule"},
        {"keys": [("status", 1), ("severity", 1)], "name": "ix_alerts_status"},
    ],
    "notifications": [
        {"keys": [("recipient_user_id", 1), ("read_at", 1)], "name": "ix_notifications_recipient"},
        {"keys": [("dedupe_key", 1), ("created_at", -1)], "name": "ix_notifications_dedupe"},
        {"keys": [("delivery_status", 1), ("next_retry_at", 1)], "name": "ix_notifications_retry", "sparse": True},
    ],
    "reports": [
        {"keys": [("requested_by", 1), ("created_at", -1)], "name": "ix_reports_requester"},
        {"keys": [("status", 1)], "name": "ix_reports_status"},
    ],
    "saved_analyses": [
        {"keys": [("owner_user_id", 1), ("updated_at", -1)], "name": "ix_saved_owner"},
    ],
    "audit_logs": [
        {"keys": [("created_at", -1)], "name": "ix_audit_created"},
        {"keys": [("actor_user_id", 1), ("created_at", -1)], "name": "ix_audit_actor"},
        {"keys": [("resource_type", 1), ("resource_id", 1)], "name": "ix_audit_resource"},
    ],
    "support_tickets": [
        {"keys": [("status", 1), ("priority", 1)], "name": "ix_tickets_status_priority"},
        {"keys": [("created_by", 1)], "name": "ix_tickets_creator"},
        {"keys": [("assigned_to", 1)], "name": "ix_tickets_assignee", "sparse": True},
    ],
    "feedback": [
        {"keys": [("created_by", 1), ("created_at", -1)], "name": "ix_feedback_creator"},
        {"keys": [("status", 1)], "name": "ix_feedback_status"},
    ],
    "platform_settings": [
        {"keys": [("key", 1)], "unique": True, "name": "ux_settings_key"},
    ],
}

# Server-side document validation. MongoDB Atlas enforces these on insert/update;
# the local-development store does not implement validators, so the same rules are
# additionally enforced in application code (backend/common/validators.py).
VALIDATORS: Dict[str, Dict[str, Any]] = {
    "users": {
        "level": "strict",
        "action": "error",
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["full_name", "email_normalized", "password_hash", "role", "created_at"],
                "properties": {
                    "email_normalized": {"bsonType": "string", "pattern": "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"},
                    "password_hash": {"bsonType": "string", "description": "bcrypt digest; never plaintext"},
                    "role": {"enum": ROLES},
                    "is_active": {"bsonType": "bool"},
                    "email_verified": {"bsonType": "bool"},
                },
            }
        },
    },
    "climate_observations": {
        "level": "strict",
        "action": "error",
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["dedupe_key", "dataset_id", "observation_date", "year", "month",
                             "latitude", "longitude", "region_code", "schema_version", "ingested_at"],
                "properties": {
                    "latitude": {"bsonType": "double", "minimum": -90, "maximum": 90},
                    "longitude": {"bsonType": "double", "minimum": -180, "maximum": 180},
                    "temperature_c": {"bsonType": ["double", "null"]},
                    "precipitation_mm": {"bsonType": ["double", "null"], "minimum": 0},
                    "relative_humidity_pct": {"bsonType": ["double", "null"], "minimum": 0, "maximum": 100},
                    "schema_version": {"bsonType": "int"},
                },
            }
        },
    },
    "datasets": {
        "level": "moderate",
        "action": "error",
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["name", "source_type", "format", "status", "schema_version", "created_at"],
                "properties": {
                    "source_type": {"enum": ["nasa_power", "noaa", "live_api", "university_upload", "sample"]},
                    "format": {"enum": ["csv", "json", "xlsx", "parquet", "api"]},
                    "status": {"enum": ["draft", "validating", "validated", "ingesting", "ready", "failed", "archived"]},
                },
            }
        },
    },
    "ingestion_jobs": {
        "level": "moderate",
        "action": "error",
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["source_name", "status", "started_at"],
                "properties": {
                    "status": {"enum": ["queued", "running", "completed", "completed_with_errors", "failed", "cancelled"]},
                    "records_fetched": {"bsonType": "int"},
                    "records_accepted": {"bsonType": "int"},
                    "records_rejected": {"bsonType": "int"},
                    "records_skipped": {"bsonType": "int"},
                },
            }
        },
    },
    "model_registry": {
        "level": "moderate",
        "action": "error",
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["name", "version", "algorithm", "status", "created_at"],
                "properties": {
                    "status": {"enum": ["training", "candidate", "active", "retired", "failed"]},
                    "evaluation_metrics": {"bsonType": "object"},
                },
            }
        },
    },
    "audit_logs": {
        "level": "strict",
        "action": "error",
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["action", "resource_type", "outcome", "created_at"],
                "properties": {"outcome": {"enum": ["success", "failure", "denied"]}},
            }
        },
    },
}


def observation_dedupe_key(dataset_id: str, location_code: str, observation_date: str) -> str:
    return f"{dataset_id}|{location_code}|{observation_date}"


def index_commands(collection: str) -> List[Dict[str, Any]]:
    return INDEX_SPECS.get(collection, [])
