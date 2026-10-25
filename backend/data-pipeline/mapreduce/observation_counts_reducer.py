#!/usr/bin/env python3
"""Hadoop Streaming reducer: observation counts per dataset + month.

Consumes ``dataset_id|year|month\tregion_code`` lines and emits one JSON
aggregate per key: total observations, distinct regions covered and a per-region
breakdown (sorted for deterministic output).
"""
from __future__ import annotations

import json
import sys
from typing import Dict, List, Optional

from streaming_common import counter, stdin_lines


def main() -> int:
    current_key: Optional[str] = None
    regions: Dict[str, int] = {}
    total = 0
    groups = 0

    def flush(key: Optional[str]) -> None:
        nonlocal regions, total, groups
        if key is None or total == 0:
            regions, total = {}, 0
            return
        dataset, year, month = key.split("|")
        payload = {
            "dataset_id": dataset,
            "year": int(year),
            "month": int(month),
            "observation_count": total,
            "region_count": len(regions),
            "regions": {name: count for name, count in sorted(regions.items())},
        }
        sys.stdout.write(f"{key}\t{json.dumps(payload, sort_keys=True, separators=(',', ':'))}\n")
        groups += 1
        regions, total = {}, 0

    for line in stdin_lines():
        text = line.rstrip("\n")
        if not text or "\t" not in text:
            continue
        key, region = text.split("\t", 1)
        if current_key is None:
            current_key = key
        if key != current_key:
            flush(current_key)
            current_key = key
        total += 1
        if region:
            regions[region] = regions.get(region, 0) + 1
    flush(current_key)
    counter("counts_reducer_groups", groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
