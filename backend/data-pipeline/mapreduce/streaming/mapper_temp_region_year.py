#!/usr/bin/env python3
"""Mapper: temperature by region+year. Emits ``region<TAB>year<TAB>value``.

Runs under Hadoop Streaming (stdin JSONL observations -> stdout TSV)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mrlib import emit, mean_temperature, read_observations, region_of, ts_parts


def main():
    for obs in read_observations():
        value = mean_temperature(obs)
        if value is None:
            continue
        year, _month = ts_parts(obs)
        if not year.isdigit():
            continue
        emit(region_of(obs), year, f"{value:.4f}")


if __name__ == "__main__":
    main()
