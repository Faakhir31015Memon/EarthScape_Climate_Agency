#!/usr/bin/env python3
"""Reducer: temperature count/mean/min/max per region+year.

Input (sorted by key): ``region<TAB>year<TAB>value`` where value is either a
raw temperature or a combiner partial ``n|sum|min|max``.
Output: ``region<TAB>year<TAB>count<TAB>mean<TAB>min<TAB>max``.
See ``output-schema.temp_region_year.json``.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mrlib import parse_key


def merge(state, value):
    if state is None:
        state = [0, 0.0, float("inf"), float("-inf")]
    if "|" in value:
        n, s, mn, mx = (float(p) for p in value.split("|"))
        state[0] += n
        state[1] += s
        state[2] = min(state[2], mn)
        state[3] = max(state[3], mx)
        return state
    v = float(value)
    state[0] += 1
    state[1] += v
    state[2] = min(state[2], v)
    state[3] = max(state[3], v)
    return state


def emit(key, state, combiner=False):
    count, total, mn, mx = state
    if combiner:
        # combiners must reproduce the map-side value format so the reduce
        # task can merge partials: n|sum|min|max
        print(f"{key}\t{int(count)}|{total:.6g}|{mn:.6g}|{mx:.6g}")
        return
    mean = total / count if count else 0.0
    print("\t".join([key, str(int(count)), f"{mean:.3f}", f"{mn:.3f}", f"{mx:.3f}"]))


def main():
    combiner = "--combiner" in sys.argv
    current_key = None
    state = None
    for line in sys.stdin:
        if not line.strip():
            continue
        keys, value = parse_key(line)
        key = "\t".join(keys)
        if current_key is not None and key != current_key:
            emit(current_key, state, combiner)
            state = None
        current_key = key
        try:
            state = merge(state, value)
        except ValueError:
            continue
    if current_key is not None and state is not None:
        emit(current_key, state, combiner)


if __name__ == "__main__":
    main()
