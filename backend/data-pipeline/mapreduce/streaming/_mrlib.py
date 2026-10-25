"""Shared helpers for the Hadoop Streaming jobs in this directory.

Input: one canonical observation JSON object per stdin line
(``schemas/observation.schema.json``). Output: tab-delimited
``key<TAB>subkey<TAB>value`` map records; reducer output formats are documented
in each job's ``output-schema.json``.

The reducers are combiner-friendly: each can consume either raw numeric values
or its own partial-aggregate token (``n|sum|min|max`` style), so the same script
works as reducer and combiner on a real cluster.
"""

from __future__ import annotations

import json
import sys


def read_observations(stream=None):
    stream = stream or sys.stdin
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # malformed input line: skip, but never crash the pipeline stage
            continue


def obs_value(obs: dict, metric: str):
    meas = (obs.get("metrics") or {}).get(metric)
    if not meas or meas.get("value") is None:
        return None
    try:
        return float(meas["value"])
    except (TypeError, ValueError):
        return None


def mean_temperature(obs: dict):
    """Prefer reported mean; else midpoint of max/min; else None (omitted, not 0)."""
    t = obs_value(obs, "temperature")
    if t is not None:
        return t
    tmax, tmin = obs_value(obs, "temperature_max"), obs_value(obs, "temperature_min")
    if tmax is not None and tmin is not None:
        return (tmax + tmin) / 2.0
    return tmax if tmax is not None else tmin


def region_of(obs: dict) -> str:
    loc = obs.get("location") or {}
    for key in ("region", "name", "station_id"):
        value = loc.get(key)
        if value:
            return str(value).strip().replace("\t", " ").replace("|", "/")
    return "unassigned"


def ts_parts(obs: dict):
    ts = str(obs.get("timestamp", ""))
    year = ts[:4]
    month = ts[5:7]
    return year, month


def emit(*fields) -> None:
    sys.stdout.write("\t".join(str(f) for f in fields) + "\n")


def parse_key(line: str):
    parts = line.rstrip("\n").split("\t")
    return parts[:-1], parts[-1]
