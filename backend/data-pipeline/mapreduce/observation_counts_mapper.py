#!/usr/bin/env python3
"""Hadoop Streaming mapper: observation counts by dataset + period.

Emits ``dataset_id|year|month\t<region_code>`` for every record that has a
dataset, region and period (records missing any of them are counted on stderr,
not emitted). The reducer totals the lines and collects the distinct regions,
which gives per-dataset monthly coverage.
"""
from __future__ import annotations

import sys

from streaming_common import counter, derive_period, emit, split_line, stdin_lines


def main() -> int:
    headers = None
    emitted = 0
    unusable = 0
    malformed = 0
    for line in stdin_lines():
        record, kind, headers = split_line(line, headers)
        if kind in ("blank", "header"):
            continue
        if kind == "malformed" or record is None:
            malformed += 1
            continue
        region, year, month = derive_period(record)
        dataset = str(record.get("dataset_id") or "").strip()
        if region is None or not dataset:
            unusable += 1
            continue
        emit(f"{dataset}|{year}|{month}", region)
        emitted += 1
    counter("counts_mapper_input", emitted + unusable + malformed)
    counter("counts_mapper_emitted", emitted)
    counter("counts_mapper_unusable", unusable)
    counter("counts_mapper_malformed", malformed)
    sys.stderr.write(f"# counts mapper summary: emitted={emitted} unusable={unusable} "
                     f"malformed={malformed}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
