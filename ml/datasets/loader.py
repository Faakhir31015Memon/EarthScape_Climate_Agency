"""Climate data discovery and loading into one tidy observation frame.

Sources, in order of preference (all read-only, all offline):
  (a) ``artifacts/datalake/processed/**`` Parquet  (canonical schema, nested metrics)
  (b) ``artifacts/datalake/raw/**/*.jsonl``        (canonical observation JSON documents)
  (c) ``sample-data/*.csv``                        (flat-column CSVs written by the pipeline)

Every source is normalised into the tidy contract:
    [timestamp, region, station, latitude, longitude,
     variable, value, unit, granularity, dataset_id, provenance_file]

Absent measurements are dropped (never coerced to zero) and counted, per the
observation schema's rule that missing metrics are omitted entirely.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .. import mlconfig

TIDY_COLUMNS = [
    "timestamp", "region", "station", "latitude", "longitude",
    "variable", "value", "unit", "granularity", "dataset_id", "provenance_file",
]

# Canonical variables (data-pipeline/schemas/observation.schema.json).
VARIABLES = [
    "temperature", "temperature_max", "temperature_min", "precipitation",
    "humidity", "wind_speed", "wind_direction", "pressure", "cloud_cover",
    "solar_radiation",
]

# Flat column-name aliases the ingestion pipeline may write (Open-Meteo /
# NASA POWER / NOAA GTD styles). Lower-cased before lookup. If the pipeline
# uses new names, pass an explicit ``column_map`` to override/extend.
DEFAULT_COLUMN_ALIASES: dict[str, list[str]] = {
    "timestamp": ["timestamp", "time", "datetime", "date", "observation_time", "valid_date", "date_utc"],
    "region": ["region", "location", "city", "station_name", "location_name", "name", "district"],
    "station": ["station", "station_id", "id", "wmo", "uswno", "station_code"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "lng", "long"],
    "elevation_m": ["elevation_m", "elevation", "altitude", "elev_m", "surface_elevation_m"],
    "granularity": ["granularity", "freq", "interval", "resolution"],
    "dataset_id": ["dataset_id", "dataset", "source_id"],
    "temperature": ["temperature", "temp", "t2m", "temperature_2m", "air_temperature",
                    "avg_temperature_2m", "tavg", "temp_mean", "meantemp"],
    "temperature_max": ["temperature_max", "t2m_max", "temperature_2m_max", "max_temperature_2m", "tmax"],
    "temperature_min": ["temperature_min", "t2m_min", "temperature_2m_min", "min_temperature_2m", "tmin"],
    "precipitation": ["precipitation", "precip", "prcp", "rain", "rainfall", "precipitation_total"],
    "humidity": ["humidity", "relative_humidity", "relative_humidity_2m", "rh2m"],
    "wind_speed": ["wind_speed", "windspeed", "wind_speed_10m", "wind_speed_10m_max"],
    "wind_direction": ["wind_direction", "wind_dir", "wind_direction_10m"],
    "pressure": ["pressure", "surface_pressure", "pressure_msl", "mslp"],
    "cloud_cover": ["cloud_cover", "total_cloud_cover", "clouds", "avg_cloudcover"],
    "solar_radiation": ["solar_radiation", "shortwave_radiation", "sunshine_duration", "ghi"],
}

UNIT_DEFAULTS = {
    "temperature": "degC", "temperature_max": "degC", "temperature_min": "degC",
    "precipitation": "mm", "humidity": "%", "wind_speed": "km/h",
    "wind_direction": "deg", "pressure": "hPa", "cloud_cover": "%", "solar_radiation": "MJ/m2",
}

_TIDY_VAR_SET = set(VARIABLES)


def _canon_variable(raw_name: str, column_map: dict[str, str]) -> Optional[str]:
    """Map a flat CSV column name to a canonical variable, or None."""
    key = raw_name.strip().lower()
    if key in column_map:
        return column_map[key]
    if key in _TIDY_VAR_SET:
        return key
    for canon, aliases in DEFAULT_COLUMN_ALIASES.items():
        if canon in _TIDY_VAR_SET and key in aliases:
            return canon
    return None


def _build_column_map(column_map: Optional[dict[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for canon, aliases in DEFAULT_COLUMN_ALIASES.items():
        if canon in _TIDY_VAR_SET:
            for a in aliases:
                merged[a] = canon
    if column_map:
        for raw, canon in column_map.items():
            merged[str(raw).strip().lower()] = str(canon)
    return merged


def _infer_granularity(ts: pd.Series) -> str:
    deltas = pd.Series(ts).dropna().sort_values().diff().dropna()
    if deltas.empty:
        return "instantaneous"
    med = float(deltas.median().total_seconds())
    if med <= 0:
        return "instantaneous"
    if med < 10800:            # < 3h -> hourly cadence
        return "hourly"
    if med < 129600:            # < 36h -> daily
        return "daily"
    return "monthly"


def _empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=TIDY_COLUMNS)
    frame["timestamp"] = pd.Series(dtype="datetime64[ns, UTC]")
    frame["value"] = pd.Series(dtype="float64")
    frame["latitude"] = pd.Series(dtype="float64")
    frame["longitude"] = pd.Series(dtype="float64")
    return frame


def _finalize(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_frame()
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    for col in ("latitude", "longitude", "value"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "variable", "value"])
    # Absent/zero-sentinel guard: NaNs already dropped. We do NOT drop 0.0 —
    # 0.0 mm precipitation and 0 degC are both real values.
    frame = frame.drop_duplicates(subset=["dataset_id", "timestamp", "granularity", "station", "variable"],
                                  keep="first")
    return frame[TIDY_COLUMNS].sort_values("timestamp").reset_index(drop=True)


# ---------------------------------------------------------------- canonical JSON
def _flatten_observation_doc(doc: dict[str, Any], provenance_file: str) -> list[dict[str, Any]]:
    """One canonical observation (nested metrics) -> tidy rows."""
    rows: list[dict[str, Any]] = []
    metrics = doc.get("metrics") or {}
    location = doc.get("location") or {}
    source = doc.get("source") or {}
    base = {
        "timestamp": doc.get("timestamp"),
        "region": location.get("region") or location.get("name") or source.get("station_id") or "unknown",
        "station": location.get("station_id") or location.get("name") or "unknown",
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "granularity": doc.get("granularity", "instantaneous"),
        "dataset_id": doc.get("dataset_id", "unknown"),
        "provenance_file": provenance_file,
    }
    for var, measurement in metrics.items():
        if not isinstance(measurement, dict):
            continue
        value = measurement.get("value")
        if value is None:
            continue  # missing stays missing; never stored as zero
        rows.append({**base, "variable": var, "value": value,
                     "unit": measurement.get("unit", UNIT_DEFAULTS.get(var, "1"))})
    return rows


def load_jsonl(path: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rel = str(path)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict) and "metrics" in doc:
                rows.extend(_flatten_observation_doc(doc, rel))
            else:
                rows.extend(_flatten_flat_record(doc, rel))
    return pd.DataFrame(rows)


def _flatten_flat_record(doc: dict[str, Any], provenance_file: str) -> list[dict[str, Any]]:
    """Tolerate flat JSONL records (same column aliases as CSVs)."""
    frame = pd.DataFrame([doc])
    return [] if frame.empty else _flatten_csv_frame(frame, provenance_file, None).to_dict("records")


# ---------------------------------------------------------------- CSV files
def _flatten_csv_frame(frame: pd.DataFrame, provenance_file: str,
                       column_map: Optional[dict[str, str]]) -> pd.DataFrame:
    colmap = _build_column_map(column_map)
    norm = {col: str(col).strip().lower() for col in frame.columns}
    lookup = {norm[col]: col for col in frame.columns}

    def pick(canon: str) -> Optional[str]:
        key = canon.strip().lower()
        if key in DEFAULT_COLUMN_ALIASES:
            for alias in DEFAULT_COLUMN_ALIASES[canon] + DEFAULT_COLUMN_ALIASES[canon]:
                if alias in lookup:
                    return lookup[alias]
        return lookup.get(key)

    ts_col = pick("timestamp")
    if ts_col is None:
        raise ValueError(f"{provenance_file}: no recognisable timestamp column "
                         f"(columns: {list(frame.columns)[:12]})")
    ts = pd.to_datetime(frame[ts_col], errors="coerce", utc=True)
    naive = frame[ts_col][ts.dt.tz.isna() if hasattr(ts.dt, "tz") else slice(None)]
    granularity_col = pick("granularity")
    if granularity_col is not None:
        granularity = frame[granularity_col].astype(str)
    else:
        inferred = _infer_granularity(ts)
        granularity = pd.Series([inferred] * len(frame))
    region_col = pick("region")
    station_col = pick("station")
    lat_col, lon_col = pick("latitude"), pick("longitude")
    dataset_col = pick("dataset_id")
    fallback_region = Path(provenance_file).stem

    base = pd.DataFrame({
        "timestamp": ts,
        "region": frame[region_col].astype(str) if region_col else fallback_region,
        "station": frame[station_col].astype(str) if station_col
                   else (frame[region_col].astype(str) if region_col else fallback_region),
        "latitude": pd.to_numeric(frame[lat_col], errors="coerce") if lat_col else np.nan,
        "longitude": pd.to_numeric(frame[lon_col], errors="coerce") if lon_col else np.nan,
        "granularity": granularity,
        "dataset_id": frame[dataset_col].astype(str) if dataset_col else fallback_region,
        "provenance_file": provenance_file,
    })

    out = []
    for col in frame.columns:
        canon = _canon_variable(str(col), colmap)
        if canon is None:
            continue
        values = pd.to_numeric(frame[col], errors="coerce")
        rows = base.copy()
        rows["variable"] = canon
        rows["value"] = values
        rows["unit"] = UNIT_DEFAULTS.get(canon, "1")
        out.append(rows.dropna(subset=["value"]))
    if not out:
        return _empty_frame()
    return pd.concat(out, ignore_index=True)


def load_csv(path: Path, column_map: Optional[dict[str, str]] = None) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except UnicodeDecodeError:
        frame = pd.read_csv(path, encoding="latin-1")
    return _flatten_csv_frame(frame, str(path), column_map)


# ---------------------------------------------------------------- Parquet
def load_parquet(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_parquet(path)
    except ImportError as exc:  # pyarrow/fastparquet missing
        warnings.warn(f"skipping {path}: parquet engine unavailable ({exc})")
        return _empty_frame()
    except Exception as exc:
        warnings.warn(f"skipping {path}: {exc}")
        return _empty_frame()
    if "metrics" in frame.columns:  # canonical nested rows
        rows: list[dict[str, Any]] = []
        for record in frame.to_dict("records"):
            if isinstance(record.get("metrics"), dict):
                rows.extend(_flatten_observation_doc(record, str(path)))
        return pd.DataFrame(rows) if rows else _empty_frame()
    if {"timestamp", "variable", "value"}.issubset(frame.columns):  # already tidy
        out = frame.copy()
        out["provenance_file"] = str(path)
        return out
    return _flatten_csv_frame(frame, str(path), None)


# ---------------------------------------------------------------- discovery
def discover_files(root: Optional[Path] = None) -> dict[str, list[Path]]:
    root = Path(root) if root else mlconfig.storage_root()
    processed = sorted((root / "processed").glob("**/*.parquet")) if (root / "processed").is_dir() else []
    raw = sorted((root / "raw").glob("**/*.jsonl")) if (root / "raw").is_dir() else []
    sample = sorted(mlconfig.sample_data_root().glob("*.csv")) if mlconfig.sample_data_root().is_dir() else []
    return {"processed_parquet": processed, "raw_jsonl": raw, "sample_csv": sample}


def load_observations(files: Optional[list[Path]] = None,
                      column_map: Optional[dict[str, str]] = None,
                      root: Optional[Path] = None,
                      include: Optional[dict[str, bool]] = None) -> pd.DataFrame:
    """Return the tidy observation frame built from every discoverable source.

    ``include`` toggles source kinds: {"processed": bool, "raw": bool, "sample": bool}.
    """
    include = include or {}
    frames: list[pd.DataFrame] = []
    if files is not None:
        targets = [Path(f) for f in files]
    else:
        found = discover_files(root)
        targets = []
        if include.get("processed", True):
            targets += found["processed_parquet"]
        if include.get("raw", True):
            targets += found["raw_jsonl"]
        if include.get("sample", True):
            targets += found["sample_csv"]
    for path in targets:
        try:
            if path.suffix.lower() == ".parquet":
                frame = load_parquet(path)
            elif path.suffix.lower() in (".jsonl", ".ndjson"):
                frame = load_jsonl(path)
            elif path.suffix.lower() == ".csv":
                frame = load_csv(path, column_map)
            else:
                continue
        except Exception as exc:
            warnings.warn(f"source {path} failed to load: {exc}")
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return _empty_frame()
    combined = pd.concat(frames, ignore_index=True)
    tidy = _finalize(combined)
    for col in ("region", "station", "granularity", "dataset_id", "unit", "variable", "provenance_file"):
        tidy[col] = tidy[col].fillna("unknown").astype(str)
    return tidy


def loading_report(frame: pd.DataFrame) -> dict[str, Any]:
    """Human-readable summary of what actually loaded (row counts, provenance)."""
    if frame.empty:
        return {"rows": 0, "files": {}, "variables": {}, "regions": [], "date_range": None}
    return {
        "rows": int(len(frame)),
        "files": {p: int(n) for p, n in
                  frame.groupby("provenance_file").size().sort_values(ascending=False).items()},
        "variables": {v: int(n) for v, n in frame.groupby("variable").size().items()},
        "regions": sorted(frame["region"].unique().tolist()),
        "granularities": sorted(frame["granularity"].unique().tolist()),
        "date_range": [str(frame["timestamp"].min()), str(frame["timestamp"].max())],
    }
