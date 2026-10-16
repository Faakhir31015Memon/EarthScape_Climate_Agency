"""Provider ingestion job: pull, normalise, validate and store observations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apps.datasets import repository as datasets
from apps.ingestion import service as ingestion
from apps.ingestion import source_clients
from common.db import find_one, oid
from common.exceptions import ValidationError
from common.logging_config import get_logger

logger = get_logger("earthscape.ingestion.api")


def run_provider_ingest(*, provider: str, dataset_id: str, job_id_str: str, coordinates: dict,
                        start: str, end: str, variables: list, granularity: str,
                        station_id: str = "", dataset_code: str = "", location_code: str = "",
                        location_name: str = "") -> dict:
    job = find_one("ingestion_jobs", {"_id": oid(job_id_str)})
    if not job:
        raise RuntimeError("ingestion job record is missing")
    dataset = datasets.get(dataset_id)

    def parse_date(value: str, default: datetime) -> datetime:
        if not value:
            return default
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError(f"Invalid date '{value}'; expected YYYY-MM-DD.") from exc
        return parsed.replace(tzinfo=timezone.utc)

    end_default = datetime.now(timezone.utc) - timedelta(days=1)
    start_dt = parse_date(start, end_default - timedelta(days=365))
    end_dt = parse_date(end, end_default)
    if end_dt < start_dt:
        raise ValidationError("The end date is before the start date.")

    latitude = float(coordinates.get("latitude"))
    longitude = float(coordinates.get("longitude"))

    try:
        if provider == "nasa_power":
            documents, meta = source_clients.fetch_nasa_power(
                dataset_id=str(dataset["_id"]), latitude=latitude, longitude=longitude,
                start=start_dt, end=end_dt, variables=variables or None,
                granularity=granularity, ingestion_job_id=str(job["_id"]),
                location_name=location_name)
        elif provider == "open_meteo":
            documents, meta = source_clients.fetch_open_meteo(
                dataset_id=str(dataset["_id"]), latitude=latitude, longitude=longitude,
                start=start_dt, end=end_dt, variables=variables or None,
                granularity=granularity, ingestion_job_id=str(job["_id"]),
                location_name=location_name)
        elif provider == "noaa_cdc":
            if not (station_id or location_code):
                raise ValidationError("NOAA CDC needs either a station_id or a location_code.")
            documents, meta = source_clients.fetch_noaa_cdc(
                dataset_id=str(dataset["_id"]), start=start_dt, end=end_dt,
                data_types=variables or None, station_id=station_id, location_id=location_code,
                dataset_code=dataset_code or "GCCN", ingestion_job_id=str(job["_id"]),
                latitude=latitude or None, longitude=longitude or None)
        else:
            raise ValidationError(f"Unknown provider '{provider}'.")
    except source_clients.ConnectorNotConfigured as exc:
        ingestion.finish_job(job["_id"], status="failed",
                             error_message=str(exc), counts={"records_fetched": 0})
        datasets.patch(dataset_id, {"status": "failed"})
        return {"status": "failed", "reason": str(exc), "records_fetched": 0}

    outcomes = ingestion.write_observations(documents)
    accepted = int(outcomes["records_accepted"])
    duplicates = int(outcomes["records_duplicate"])
    rejected = int(outcomes["records_rejected"])
    # A pull that re-fetches rows already stored is a successful, idempotent sync.
    status = "completed" if (accepted or duplicates) else "failed"
    coverage = {
        "temporal_start": min((d["timestamp"] for d in documents), default=None),
        "temporal_end": max((d["timestamp"] for d in documents), default=None),
        "regions": [location_name] if location_name else [],
        "bounding_box": {"min_lat": latitude, "max_lat": latitude, "min_lon": longitude,
                         "max_lon": longitude},
    }
    if coverage["temporal_start"]:
        coverage["temporal_start"] = coverage["temporal_start"].isoformat()
        coverage["temporal_end"] = coverage["temporal_end"].isoformat()

    ingestion.finish_job(
        job["_id"], status=status,
        counts={"records_fetched": int(meta.get("requested_rows") or meta.get("requested_points")
                                       or meta.get("fetched") or len(documents)),
                "records_accepted": accepted,
                "records_rejected": rejected,
                "records_duplicate": duplicates},
        errors=outcomes["errors"], coverage=coverage,
        error_message=None if accepted or duplicates
        else "The provider returned no usable observations for this request.",
    )
    datasets.record_ingestion_result(
        dataset_id, records_added=accepted, coverage=coverage,
        status="ready" if (accepted or duplicates) else "failed",
        rejected=rejected, duplicates=duplicates,
    )
    return {
        "dataset_id": dataset_id,
        "provider": provider,
        "records_accepted": accepted,
        "records_duplicate": duplicates,
        "records_rejected": rejected,
        "provider_meta": {k: v for k, v in meta.items() if k != "unit_map"},
        "coverage": coverage,
        "status": status,
    }


PROVIDER_LABELS = {"nasa_power": "NASA POWER", "noaa_cdc": "NOAA Climate Data Online",
                   "open_meteo": "Open-Meteo"}


def coord_label(payload) -> str:
    return f"({payload.coordinates['latitude']:.2f}, {payload.coordinates['longitude']:.2f})"


def dataset_for_pull(payload, user_id: str) -> dict:
    """Reuse an identical provider dataset so repeated pulls accumulate into one dataset."""
    label = PROVIDER_LABELS.get(payload.provider, payload.provider)
    found = datasets.find_by_name(payload.dataset_name, label)
    if found:
        return found
    return datasets.create(
        name=payload.dataset_name,
        description=f"Pulled from {label} for "
                    f"({payload.coordinates['latitude']:.2f}, {payload.coordinates['longitude']:.2f}).",
        source_type="api", source_name=label, fmt="api", uploaded_by=user_id,
        geographic_coverage={"bounding_box": {"min_lat": payload.coordinates["latitude"],
                                              "max_lat": payload.coordinates["latitude"],
                                              "min_lon": payload.coordinates["longitude"],
                                              "max_lon": payload.coordinates["longitude"]}},
        temporal_coverage=payload.date_range or {},
        tags=[payload.provider, payload.granularity],
    )


def pull_kwargs(*, payload, dataset_id: str, job_id: str) -> dict:
    """The single argument set both the API and the scheduler pass to run_provider_ingest."""
    return {
        "provider": payload.provider, "dataset_id": dataset_id, "job_id_str": job_id,
        "coordinates": payload.coordinates,
        "start": (payload.date_range or {}).get("start", ""),
        "end": (payload.date_range or {}).get("end", ""),
        "variables": payload.variables, "granularity": payload.granularity,
        "station_id": payload.station_id or "", "dataset_code": payload.dataset_code or "",
        "location_code": payload.location_code or "",
        "location_name": payload.location_name or coord_label(payload),
    }


def create_pull_job(payload, dataset_id: str, user_id: str) -> dict:
    return ingestion.create_job(
        kind="provider_pull", source=payload.provider, dataset_id=dataset_id,
        params={"coordinates": payload.coordinates, "date_range": payload.date_range,
                "variables": payload.variables, "granularity": payload.granularity,
                "station_id": payload.station_id or "", "dataset_code": payload.dataset_code or "",
                "location_code": payload.location_code or "",
                "location_name": payload.location_name or ""},
        created_by=user_id,
        label=f"{PROVIDER_LABELS.get(payload.provider)} pull ({payload.coordinates['latitude']:.2f}, "
              f"{payload.coordinates['longitude']:.2f})",
    )
