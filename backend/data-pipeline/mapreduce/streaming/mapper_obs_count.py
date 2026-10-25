#!/usr/bin/env python3
"""Mapper: observation counts by dataset+month. Emits ``dataset<TAB>YYYY-MM<TAB>1``."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mrlib import emit, read_observations, ts_parts


def main():
    for obs in read_observations():
        dataset = str(obs.get("dataset_id", "unknown")).strip().replace("\t", " ")
        year, month = ts_parts(obs)
        if not (year.isdigit() and month.isdigit()):
            continue
        emit(dataset, f"{year}-{month}", 1)


if __name__ == "__main__":
    main()
