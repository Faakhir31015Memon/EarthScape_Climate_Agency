# MapReduce (Hadoop Streaming) jobs

Genuine Hadoop Streaming mappers/reducers for EarthScape climate aggregation.
Every script reads stdin and writes `key<TAB>value` lines to stdout, with
counters on stderr in Hadoop's `reporter:counter:Group,Name,Value` format, so
the *same files* run inside a container and in a local pipe.

## Jobs

| Mapper | Reducer | Emits |
|---|---|---|
| `temperature_aggregation_mapper.py` | `temperature_aggregation_reducer.py` | `region_code\|year\|month` → monthly mean temperature, precipitation sum, observation counts, min/max, quality-flag tally |
| `observation_counts_mapper.py` | `observation_counts_reducer.py` | `dataset_id\|year\|month` → observation counts with per-region breakdown |

Input: JSON-lines observation records (the MongoDB export format used by
`run_streaming_job.py`) or CSV with a header row. Missing measurements are
empty fields - **never zeroed**. Records without a region/period or without any
usable temperature/precipitation emit nothing and are counted on stderr.

## Submission on a real cluster

```bash
hadoop jar $HADOOP_HOME/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar \
  -D mapreduce.job.reduce.slowstart.completedmaps=0.8 \
  -files temperature_aggregation_mapper.py,temperature_aggregation_reducer.py,streaming_common.py \
  -mapper  "python3 temperature_aggregation_mapper.py" \
  -reducer "python3 temperature_aggregation_reducer.py" \
  -input  /user/earthscape/validated/dataset=<id>/run-<ts>-observations.jsonl \
  -output /user/earthscape/analytics/_mapreduce/run-<ts>/temperature_aggregation
```

`run_streaming_job.py` builds and executes this command when `HADOOP_BIN` (or a
`hadoop` on `PATH`) is configured *and* HDFS is reachable, and captures the real
return code and counters.

## Running the same pipeline locally (no cluster)

```bash
python run_streaming_job.py --force-local
```

`run_streaming_job.py --force-local` executes the identical mapper/sort/reducer
subprocess pipeline against the MongoDB export and labels the run
`processing_mode: "local-streaming-simulated"` - it never reports that Hadoop
ran when it did not. When the collection is empty it falls back to
`example-input.jsonl`, which is a clearly labelled **synthetic contract example,
not climate truth**, and the run is tagged with that source so it cannot be
mistaken for production data.

The platform's admin pipeline page runs this job through
`backend/apps/processing/runner.py`; the workflow's output lands in the local
data lake under `analytics/` and is upserted into `analytics_summaries`.

## Counters

Both mappers/reducers emit stderr counters (`input`, `usable`, `unusable`,
`malformed`, `groups`, `bad_lines`); `run_streaming_job.py` harvests them into
the job summary so metrics like `rows_in`/`rows_out` come from observed output.
