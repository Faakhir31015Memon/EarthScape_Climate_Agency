"""MongoDB schema migration: collections, indexes, validators and seeded settings.

Versioned and idempotent - `python scripts/migrate_mongo.py` can run repeatedly. Every
applied version is recorded in `platform_settings` so drift is visible.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from pymongo.errors import OperationFailure  # noqa: E402

from common.db import COLLECTIONS, client, database_name, now_utc  # noqa: E402

SCHEMA_VERSION = "1.0.0"

INDEXES: dict[str, list[dict]] = {
    "users": [
        {"keys": [("email", 1)], "unique": True, "name": "ux_users_email"},
        {"keys": [("role", 1), ("is_active", 1)], "name": "ix_users_role_active"},
    ],
    "refresh_tokens": [
        {"keys": [("token_hash", 1)], "unique": True, "name": "ux_refresh_hash"},
        {"keys": [("user_id", 1), ("revoked_at", 1)], "name": "ix_refresh_user"},
        {"keys": [("expires_at", 1)], "name": "ix_refresh_expiry",
         "expireAfterSeconds": 60 * 60 * 24 * 30},
    ],
    "password_resets": [
        {"keys": [("token_hash", 1)], "unique": True, "name": "ux_reset_hash"},
        {"keys": [("expires_at", 1)], "name": "ix_reset_expiry", "expireAfterSeconds": 60 * 90},
    ],
    "datasets": [
        {"keys": [("name", 1), ("source_name", 1)], "unique": True, "name": "ux_dataset_name_source"},
        {"keys": [("status", 1), ("created_at", -1)], "name": "ix_dataset_status"},
        {"keys": [("source_type", 1)], "name": "ix_dataset_source_type"},
    ],
    "ingestion_jobs": [
        {"keys": [("job_ref", 1)], "unique": True, "name": "ux_ingestion_ref"},
        {"keys": [("status", 1), ("started_at", -1)], "name": "ix_ingestion_status"},
        {"keys": [("dataset_id", 1)], "name": "ix_ingestion_dataset"},
    ],
    "climate_observations": [
        {"keys": [("dedupe_key", 1)], "unique": True, "name": "ux_obs_dedupe"},
        {"keys": [("dataset_id", 1), ("timestamp", -1)], "name": "ix_obs_dataset_time"},
        {"keys": [("timestamp", -1)], "name": "ix_obs_time"},
        {"keys": [("location.region", 1), ("timestamp", -1)], "name": "ix_obs_region_time"},
        {"keys": [("location.latitude", 1), ("location.longitude", 1)], "name": "ix_obs_coords"},
        {"keys": [("granularity", 1, "timestamp", -1)], "name": "ix_obs_granularity"},
    ],
    "analytics_summaries": [
        {"keys": [("summary_type", 1), ("dataset_version", 1)], "name": "ix_summary_type"},
        {"keys": [("computed_at", -1)], "name": "ix_summary_computed"},
    ],
    "processing_jobs": [
        {"keys": [("job_ref", 1)], "unique": True, "name": "ux_processing_ref"},
        {"keys": [("status", 1), ("queued_at", -1)], "name": "ix_processing_status"},
    ],
    "model_registry": [
        {"keys": [("model_name", 1), ("model_version", 1)], "unique": True, "name": "ux_model_version"},
        {"keys": [("status", 1)], "name": "ix_model_status"},
    ],
    "predictions": [
        {"keys": [("model_version_id", 1), ("generated_at", -1)], "name": "ix_predictions_model"},
        {"keys": [("target_variable", 1, "geographic_scope.region", 1)], "name": "ix_predictions_scope"},
    ],
    "alert_rules": [
        {"keys": [("name", 1)], "unique": True, "name": "ux_alert_name"},
        {"keys": [("enabled", 1)], "name": "ix_alert_enabled"},
    ],
    "alert_events": [
        {"keys": [("dedupe_key", 1)], "unique": True, "name": "ux_alert_event_dedupe"},
        {"keys": [("status", 1), ("detected_at", -1)], "name": "ix_alert_event_status"},
    ],
    "notifications": [
        {"keys": [("recipient", 1, "read_at", 1)], "name": "ix_notifications_recipient"},
        {"keys": [("created_at", -1)], "name": "ix_notifications_created"},
    ],
    "reports": [
        {"keys": [("requested_by", 1, "created_at", -1)], "name": "ix_reports_owner"},
        {"keys": [("status", 1)], "name": "ix_reports_status"},
    ],
    "saved_analyses": [
        {"keys": [("owner", 1, "name", 1)], "unique": True, "name": "ux_saved_analysis_owner_name"},
    ],
    "audit_logs": [
        {"keys": [("occurred_at", -1)], "name": "ix_audit_time"},
        {"keys": [("identity", 1, "occurred_at", -1)], "name": "ix_audit_identity"},
        {"keys": [("action", 1, "outcome", 1)], "name": "ix_audit_action"},
    ],
    "support_tickets": [
        {"keys": [("ticket_number", 1)], "unique": True, "name": "ux_ticket_number"},
        {"keys": [("status", 1), ("priority", -1), ("created_at", -1)], "name": "ix_ticket_queue"},
        {"keys": [("created_by", 1)], "name": "ix_ticket_creator"},
    ],
    "ticket_messages": [
        {"keys": [("ticket_id", 1, "created_at", 1)], "name": "ix_ticket_messages"},
    ],
    "feedback": [
        {"keys": [("created_at", -1)], "name": "ix_feedback_time"},
    ],
    "platform_settings": [
        {"keys": [("key", 1)], "unique": True, "name": "ux_setting_key"},
    ],
    "rate_limits": [
        {"keys": [("key", 1)], "unique": True, "name": "ux_rate_key"},
        {"keys": [("updated_at", 1)], "name": "ix_rate_ttl", "expireAfterSeconds": 60 * 60 * 25},
    ],
    "stream_checkpoints": [
        {"keys": [("stream", 1)], "unique": True, "name": "ux_checkpoint_stream"},
    ],
}

OBSERVATION_VALIDATOR = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["dedupe_key", "dataset_id", "timestamp", "granularity", "location", "metrics"],
            "properties": {
                "dedupe_key": {"bsonType": "string", "description": "unique identity of the measurement"},
                "dataset_id": {"bsonType": "string"},
                "timestamp": {"bsonType": "date"},
                "granularity": {"enum": ["instantaneous", "hourly", "daily", "monthly"]},
                "location": {
                    "bsonType": "object",
                    "required": ["latitude", "longitude"],
                    "properties": {
                        "latitude": {"bsonType": "double", "minimum": -90, "maximum": 90},
                        "longitude": {"bsonType": "double", "minimum": -180, "maximum": 180},
                    },
                },
                "metrics": {"bsonType": "object"},
                "quality": {"bsonType": "object"},
            },
        }
    },
    "validationLevel": "strict",
    "validationAction": "error",
}


def _normalize_keys(keys: list) -> list[tuple[str, int]]:
    """Accept flat (`field, dir, field, dir`) tuples and expand them into pairs."""
    normalized: list[tuple[str, int]] = []
    for item in keys:
        if len(item) == 2:
            normalized.append((str(item[0]), int(item[1])))
            continue
        if len(item) % 2:
            raise ValueError(f"Malformed index key: {item}")
        for i in range(0, len(item), 2):
            normalized.append((str(item[i]), int(item[i + 1])))
    return normalized


def apply_indexes(database) -> list[str]:
    applied: list[str] = []
    for name, specs in INDEXES.items():
        table = database[name]
        for spec in specs:
            kwargs = {"name": spec["name"]}
            if spec.get("unique"):
                kwargs["unique"] = True
            if "expireAfterSeconds" in spec:
                kwargs["expireAfterSeconds"] = spec["expireAfterSeconds"]
            try:
                table.create_index(_normalize_keys(spec["keys"]), **kwargs)
                applied.append(f"{name}.{spec['name']}")
            except OperationFailure as exc:
                print(f"  ! index {name}.{spec['name']} skipped: {exc.codeName}")
    return applied


def apply_validators(database) -> list[str]:
    done: list[str] = []
    try:
        database.command({"collMod": "climate_observations",
                          "validator": OBSERVATION_VALIDATOR["validator"],
                          "validationLevel": "strict", "validationAction": "error"})
        done.append("climate_observations.validator")
    except OperationFailure as exc:
        print(f"  ! validator not applied ({exc.codeName}); creating collection first")
        database.create_collection("climate_observations", validator=OBSERVATION_VALIDATOR["validator"],
                                   validationLevel="strict", validationAction="error")
        done.append("climate_observations.created_with_validator")
    return done


def seed_settings(database) -> list[str]:
    defaults = {
        "schema_version": SCHEMA_VERSION,
        "platform": {
            "name": "EarthScape Climate Agency",
            "organisation": "EarthScape Climate Agency",
        },
        "units": {"temperature": "C", "precipitation": "mm", "wind_speed": "m/s", "pressure": "hPa"},
        "map": {"default_center": [30.3753, 69.3451], "default_zoom": 5},
    }
    written: list[str] = []
    for key, value in defaults.items():
        database["platform_settings"].update_one(
            {"key": key},
            {"$set": {"key": key, "value": value, "updated_at": now_utc()},
             "$setOnInsert": {"created_at": now_utc()}},
            upsert=True,
        )
        written.append(key)
    return written


def main() -> int:
    mongo = client()
    database = mongo[database_name()]
    print(f"Database: {database_name()}")

    created_collections = []
    existing = set(database.list_collection_names())
    for logical, physical in COLLECTIONS.items():
        if physical not in existing:
            database.create_collection(physical)
            created_collections.append(physical)
    print(f"Collections created: {len(created_collections)} -> {', '.join(created_collections) or 'none'}")

    indexes = apply_indexes(database)
    print(f"Indexes ensured: {len(indexes)}")

    validators = apply_validators(database)
    print(f"Validators: {', '.join(validators)}")

    settings_written = seed_settings(database)
    print(f"Seeded settings: {', '.join(settings_written)}")

    database["platform_settings"].update_one(
        {"key": "schema_version"},
        {"$set": {"value": SCHEMA_VERSION, "migrated_at": now_utc()}},
    )
    print("Migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
