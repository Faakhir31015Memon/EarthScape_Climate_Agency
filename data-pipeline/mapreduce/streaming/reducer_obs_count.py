#!/usr/bin/env python3
"""Reducer: total observation count per dataset+month.

Input: ``dataset<TAB>YYYY-MM<TAB>count-or-1``. Output: ``dataset<TAB>YYYY-MM<TAB>count``.
See ``output-schema.obs_count.json``.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mrlib import parse_key


def main():
    # counting is trivially combiner-friendly: a partial count has the same
    # key<TAB>number shape as a map record, so one code path serves both.
    current_key = None
    total = 0
    for line in sys.stdin:
        if not line.strip():
            continue
        keys, value = parse_key(line)
        key = "\t".join(keys)
        try:
            n = int(float(value))
        except ValueError:
            continue
        if current_key is not None and key != current_key:
            print(f"{key}\t{total}")
            total = 0
        current_key = key
        total += n
    if current_key is not None:
        print(f"{current_key}\t{total}")


if __name__ == "__main__":
    main()
