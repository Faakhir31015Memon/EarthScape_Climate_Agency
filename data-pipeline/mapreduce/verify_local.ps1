# Local verification of the Hadoop Streaming mapper/reducer contract (PowerShell).
# Pipes the synthetic example input through mapper -> sort -> reducer and diffs
# the result against the committed expected-output.json.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py - $py @'
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
raise SystemExit(1 if failures else 0)
'@
if ($LASTEXITCODE -ne 0) { throw "local streaming verification failed" }
Write-Output "local streaming verification passed (mapper | sort | reducer on synthetic example input)"
