# MapReduce — Hadoop Streaming climate aggregations

Three genuine Hadoop Streaming jobs over canonical observations (one JSON
object per line, see `../schemas/observation.schema.json`). Each program is a
plain stdin→stdout Python script, so the *same files* run on a cluster and are
verifiable on a laptop through `local_runner.py`, which executes them as
subprocesses piped mapper → sort → reducer (the real streaming protocol).

## Jobs and output schemas

| job | key | reducer output (TSV) | schema |
|-----|-----|----------------------|--------|
| `temp_region_year` | region + year | `region  year  count  mean_c  min_c  max_c` | `streaming/output-schema.temp_region_year.json` |
| `precip_region` | region + month | `region  YYYY-MM  count  sum_mm  mean_mm` | `streaming/output-schema.precip_region.json` |
| `obs_count` | dataset + month | `dataset_id  YYYY-MM  count` | `streaming/output-schema.obs_count.json` |

Missing measurements are omitted upstream by the mappers (a record without
precipitation simply produces no map row — never a 0 contribution). The
reducers are combiner-friendly: with `--combiner` they emit partial tokens
(`n|sum|min|max` / `n|sum`) that the reduce step merges, so `-combiner` can be
added on a cluster without changing results.

## Example input (one line)

```json
{"timestamp":"2024-06-01T00:00:00Z","granularity":"daily","dataset_id":"open_meteo_daily_karachi","source":{"provider":"open_meteo","kind":"api"},"location":{"latitude":24.86,"longitude":67.0,"region":"Sindh"},"metrics":{"temperature_max":{"value":40.1,"unit":"C"},"precipitation":{"value":0.0,"unit":"mm"}},"dedupe_key":"…"}
```

## Local execution (no cluster needed)

```bash
# from the repository root, with data-pipeline on PYTHONPATH
python mapreduce/local_runner.py artifacts/datalake/validated/dataset=open_meteo_daily_karachi/observations.jsonl \
    --dataset open_meteo_daily_karachi
```

Outputs land in `artifacts/datalake/processed/dataset=<name>/<job>/part-00000.tsv`
with a `manifest.json` (`{job, input_path, output_path, mapper, reducer,
num_mappers, started_at, finished_at, engine: "hadoop-streaming-local",
record_in, record_out}`).

## Cluster execution

```bash
export HADOOP_HOME=/opt/hadoop
mapreduce/hadoop_submit.sh observations.jsonl hdfs:///user/earthscape/mr/temp_region_year all
```

`hadoop_submit.sh` refuses to run (loudly, exit 1) when `HADOOP_HOME` is unset
or the streaming jar is missing; it never falls back to "simulated" cluster
results.

## Checking results independently

`mapreduce/../spark/unit_reference.py` recomputes `temp_region_year` counts and
means with pandas from the same JSONL input and compares to the streaming TSV;
`tests/test_mapreduce_correctness.py` checks the same scripts against
hand-computed references. Cross-check by hand with e.g.:

```bash
python - <<'EOF'
import json, collections
agg = collections.defaultdict(list)
for line in open("observations.jsonl", encoding="utf-8"):
    r = json.loads(line)
    t = r["metrics"].get("temperature") or r["metrics"].get("temperature_max")
    if t: agg[(r["location"].get("region","unassigned"), r["timestamp"][:4])].append(t["value"])
for k, v in sorted(agg.items()): print(*k, len(v), round(sum(v)/len(v),3), sep="\t")
EOF
```
