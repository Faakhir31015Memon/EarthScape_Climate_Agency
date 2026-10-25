#!/usr/bin/env python3
"""Hadoop Streaming reducer: monthly regional climate aggregates.

Consumes the sorted output of ``temperature_aggregation_mapper.py``
(``region_code|year|month\ttemp,precip,count,flags``) and emits one line per key:

    region_code|year|month\t{json aggregate}

The JSON aggregate carries the monthly mean temperature (over usable values
only - nulls are excluded, never treated as zero), the precipitation sum (null
if no usable precipitation exists), observation counts, temperature min/max and
a data-quality flag tally. Records that contributed nothing are reported on
stderr as Hadoop-style counter lines.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from streaming_common import counter, stdin_lines


def parse_value(value: str) -> Tuple[Optional[float], Optional[float], int, List[str]]:
    parts = value.split(",")
    if len(parts) != 4:
        return None, None, 0, []
    temperature = float(parts[0]) if parts[0] else None
    precipitation = float(parts[1]) if parts[1] else None
    try:
        count = int(parts[2]) if parts[2] else 1
    except ValueError:
        count = 1
    flags = [] if parts[3] in ("", "-") else parts[3].split(";")
    return temperature, precipitation, count, flags


def aggregate(key: str, values: List[str]) -> Dict[str, Any]:
    region, year, month = key.split("|")
    temp_total = 0.0
    temp_count = 0
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    precip_total = 0.0
    precip_count = 0
    observations = 0
    flag_counts: Dict[str, int] = {}
    unusable_within_group = 0
    for raw in values:
        temperature, precipitation, count, flags = parse_value(raw)
        if temperature is None and precipitation is None:
            unusable_within_group += 1
            continue
        observations += count
        if temperature is not None:
            temp_total += temperature
            temp_count += 1
            temp_min = temperature if temp_min is None else min(temp_min, temperature)
            temp_max = temperature if temp_max is None else max(temp_max, temperature)
        if precipitation is not None:
            precip_total += precipitation
            precip_count += 1
        for flag in flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    payload: Dict[str, Any] = {
        "region_code": region,
        "year": int(year),
        "month": int(month),
        "mean_temperature_c": round(temp_total / temp_count, 4) if temp_count else None,
        "precipitation_sum_mm": round(precip_total, 4) if precip_count else None,
        "observation_count": observations,
        "temperature_value_count": temp_count,
        "precipitation_value_count": precip_count,
        "temperature_min_c": round(temp_min, 4) if temp_min is not None else None,
        "temperature_max_c": round(temp_max, 4) if temp_max is not None else None,
        "quality_flag_counts": dict(sorted(flag_counts.items())),
        "skipped_within_group": unusable_within_group,
    }
    return payload


def main() -> int:
    current_key: Optional[str] = None
    bucket: List[str] = []
    groups = 0
    bad_lines = 0

    def flush(key: Optional[str], values: List[str]) -> None:
        nonlocal groups
        if key is None:
            return
        payload = aggregate(key, values)
        if payload["observation_count"] == 0:
            # Entire group was unusable: emit nothing, count it.
            counter("temperature_reducer_empty_groups", 1)
            return
        sys.stdout.write(f"{key}\t{json.dumps(payload, sort_keys=True, separators=(',', ':'))}\n")
        groups += 1

    for line in stdin_lines():
        text = line.rstrip("\n")
        if not text or "\t" not in text:
            if text and not text.startswith("reporter:"):
                bad_lines += 1
            continue
        key, value = text.split("\t", 1)
        if current_key is None:
            current_key = key
        if key != current_key:
            flush(current_key, bucket)
            current_key, bucket = key, [value]
        else:
            bucket.append(value)
    flush(current_key, bucket)
    counter("temperature_reducer_groups", groups)
    counter("temperature_reducer_bad_lines", bad_lines)
    sys.stderr.write(f"# reducer summary: groups={groups} bad_lines={bad_lines}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
