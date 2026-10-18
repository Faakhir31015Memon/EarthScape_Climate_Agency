#!/usr/bin/env bash
# Local verification of the Hadoop Streaming mapper/reducer contract.
# Pipes the synthetic example input through mapper -> sort -> reducer for both
# jobs and compares the result against the committed expected-output.json.
# Run from anywhere: `bash verify_local.sh` (Git Bash on Windows, or any Unix).
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
# Prefer the repository venv when present.
if [ -x "../../.venv/Scripts/python.exe" ]; then PY="../../.venv/Scripts/python.exe"; fi
if [ -x "../../.venv/bin/python" ]; then PY="../../.venv/bin/python"; fi

"$PY" - "$PY" <<'EOF'
import json, subprocess, sys

py = sys.argv[1]

def run(mapper, reducer):
    m = subprocess.run([py, mapper], stdin=open("example-input.jsonl", encoding="utf-8"),
                       capture_output=True, text=True)
    s = subprocess.run([py, "sort_by_key.py"], input=m.stdout, capture_output=True, text=True)
    r = subprocess.run([py, reducer], input=s.stdout, capture_output=True, text=True)
    if m.returncode or s.returncode or r.returncode:
        raise SystemExit(f"pipeline exited non-zero: map={m.returncode} sort={s.returncode} reduce={r.returncode}")
    lines = []
    for line in r.stdout.splitlines():
        key, payload = line.split("\t", 1)
        lines.append({"key": key, "aggregate": json.loads(payload)})
    return lines

expected = json.load(open("expected-output.json", encoding="utf-8"))
failures = 0
for job, spec in expected["jobs"].items():
    actual = run(spec["mapper"], spec["reducer"])
    if actual == spec["lines"]:
        print(f"OK   {job}: {len(actual)} reducer lines match expected-output.json")
    else:
        failures += 1
        print(f"FAIL {job}: output diverged ({len(actual)} lines vs {len(spec['lines'])} expected)")
        for a, e in zip(actual, spec["lines"]):
            if a != e:
                print("  first difference:", a, "!=", e)
                break
raise SystemExit(1 if failures else 0)
EOF
echo "local streaming verification passed (mapper | sort | reducer on synthetic example input)"
