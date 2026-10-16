#!/usr/bin/env python3
"""Mapper: precipitation by region+month. Emits ``region<TAB>YYYY-MM<TAB>mm``.
Missing precipitation is omitted (never emitted as 0)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mrlib import emit, obs_value, read_observations, region_of, ts_parts


def main():
    for obs in read_observations():
        value = obs_value(obs, "precipitation")
        if value is None:
            continue
        year, month = ts_parts(obs)
        if not (year.isdigit() and month.isdigit()):
            continue
        emit(region_of(obs), f"{year}-{month}", f"{value:.4f}")


if __name__ == "__main__":
    main()
