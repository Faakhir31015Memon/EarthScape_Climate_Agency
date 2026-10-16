#!/usr/bin/env python3
"""Reducer: precipitation count/sum/mean per region+month.

Input: ``region<TAB>YYYY-MM<TAB>value`` (raw mm or combiner partial ``n|sum``).
Output: ``region<TAB>YYYY-MM<TAB>count<TAB>sum<TAB>mean``.
See ``output-schema.precip_region.json``.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mrlib import parse_key


def merge(state, value):
    if state is None:
        state = [0, 0.0]
    if "|" in value:
        n, s = (float(p) for p in value.split("|"))
        state[0] += n
        state[1] += s
        return state
    state[0] += 1
    state[1] += float(value)
    return state


def emit(key, state, combiner=False):
    count, total = state
    if combiner:
        print(f"{key}\t{int(count)}|{total:.6g}")
        return
    mean = total / count if count else 0.0
    print("\t".join([key, str(int(count)), f"{total:.3f}", f"{mean:.3f}"]))


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
