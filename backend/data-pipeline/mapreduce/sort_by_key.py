#!/usr/bin/env python3
"""Deterministic key sort for the local Hadoop-Streaming simulation.

Reads ``key<TAB>value`` lines from stdin and writes them stably sorted by key
(then by value), matching the grouping guarantee Hadoop's shuffle gives the
reducer. Used when the platform ``sort`` binary is unavailable or behaves
differently (Windows ``sort.exe``).
"""
from __future__ import annotations

import sys


def main() -> int:
    lines = [line for line in sys.stdin if line.strip()]
    keyed = []
    for line in lines:
        key, _, value = line.partition("\t")
        keyed.append((key, value, line))
    keyed.sort(key=lambda item: (item[0], item[1], item[2]))
    sys.stdout.write("".join(raw if raw.endswith("\n") else raw + "\n"
                              for _, _, raw in keyed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
