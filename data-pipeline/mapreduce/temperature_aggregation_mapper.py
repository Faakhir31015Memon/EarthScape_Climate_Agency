#!/usr/bin/env python3
"""Hadoop Streaming mapper: regional monthly temperature/precipitation keys.

Reads observation records (JSON-lines, or CSV with a header row) from stdin and
emits, for every *usable* record:

    region_code|year|month\t<temperature_c>,<precipitation_mm>,<count>,<quality_flags>

where a missing measurement is an empty field (never ``0``) and ``count`` is
always ``1`` (one record per line). Unusable records (no region/period, or no
temperature *and* no precipitation usable) emit nothing but are reported on
stderr as ``reporter:counter:EarthScape,<name>,<n>`` lines, exactly matching the
format Hadoop's streaming counters expect.

Submission on a real cluster (see README.md)::

    hadoop jar $HADOOP_HOME/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar \
      -files temperature_aggregation_mapper.py,streaming_common.py \
      -mapper "python3 temperature_aggregation_mapper.py" ...

Local verification::

    python3 temperature_aggregation_mapper.py < example-input.jsonl | sort | \\
      python3 temperature_aggregation_reducer.py
"""
from __future__ import annotations

import sys

from streaming_common import bounded_metric, counter, derive_period, emit, split_line, stdin_lines


def main() -> int:
    headers = None
    usable = 0
    unusable = 0
    malformed = 0
    for line in stdin_lines():
        record, kind, headers = split_line(line, headers)
        if kind == "blank" or kind == "header":
            continue
        if kind == "malformed" or record is None:
            malformed += 1
            continue
        region, year, month = derive_period(record)
        if region is None:
            unusable += 1
            continue
        temperature, temp_oob = bounded_metric(record, "temperature_c")
        precipitation, precip_oob = bounded_metric(record, "precipitation_mm")
        if temperature is None and precipitation is None:
            # No usable measurement for this aggregation - counted, never emitted.
            unusable += 1
            continue
        flags = []
        raw_flags = record.get("quality_flags") or ""
        if isinstance(raw_flags, list):
            flags.extend(str(flag) for flag in raw_flags if str(flag).strip())
        elif isinstance(raw_flags, str) and raw_flags.strip():
            flags.extend(part.strip() for part in raw_flags.replace(";", ",").split(",") if part.strip())
        if temp_oob:
            flags.append("temperature_c_out_of_range")
        if precip_oob:
            flags.append("precipitation_mm_out_of_range")
        key = f"{region}|{year}|{month}"
        value = ",".join([
            "" if temperature is None else f"{temperature:.4f}",
            "" if precipitation is None else f"{precipitation:.4f}",
            "1",
            ";".join(sorted(set(flags))) or "-",
        ])
        emit(key, value)
        usable += 1
    counter("temperature_mapper_input", usable + unusable + malformed)
    counter("temperature_mapper_usable", usable)
    counter("temperature_mapper_unusable", unusable)
    counter("temperature_mapper_malformed", malformed)
    sys.stderr.write(f"# mapper summary: usable={usable} unusable={unusable} malformed={malformed}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
