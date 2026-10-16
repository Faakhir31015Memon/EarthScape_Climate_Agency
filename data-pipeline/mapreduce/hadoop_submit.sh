#!/usr/bin/env bash
# Submit the EarthScape Hadoop Streaming jobs to a real cluster.
# Usage: ./hadoop_submit.sh <local-input-jsonl-path> <hdfs-output-root> [temp_region_year|precip_region|obs_count|all]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STREAMING_DIR="${SCRIPT_DIR}/streaming"

# ---------------- preflight: fail loudly, never pretend ----------------
if [[ -z "${HADOOP_HOME:-}" ]]; then
  echo "ERROR: HADOOP_HOME is not set. This script only targets a real Hadoop" >&2
  echo "cluster; for verification without a cluster use:" >&2
  echo "  python mapreduce/local_runner.py <input.jsonl>" >&2
  exit 1
fi
if [[ ! -d "${HADOOP_HOME}" ]]; then
  echo "ERROR: HADOOP_HOME=${HADOOP_HOME} does not exist" >&2
  exit 1
fi
STREAMING_JAR="$(ls "${HADOOP_HOME}"/share/hadoop/tools/lib/hadoop-streaming-*.jar 2>/dev/null | head -n 1 || true)"
if [[ -z "${STREAMING_JAR}" ]]; then
  echo "ERROR: no hadoop-streaming-*.jar under ${HADOOP_HOME}/share/hadoop/tools/lib" >&2
  exit 1
fi
if ! command -v hadoop >/dev/null 2>&1; then
  echo "ERROR: 'hadoop' is not on PATH" >&2
  exit 1
fi

INPUT_LOCAL="${1:?usage: hadoop_submit.sh <input.jsonl> <hdfs-output-root> [job]}"
OUTPUT_ROOT="${2:?usage: hadoop_submit.sh <input.jsonl> <hdfs-output-root> [job]}"
JOB="${3:-all}"

HDFS_INPUT="${OUTPUT_ROOT}/input/observations.jsonl"
echo "==> uploading ${INPUT_LOCAL} to ${HDFS_INPUT}"
hadoop fs -mkdir -p "${OUTPUT_ROOT}/input"
hadoop fs -put -f "${INPUT_LOCAL}" "${HDFS_INPUT}"

submit() {
  local job="$1" mapper="$2" reducer="$3"
  local out="${OUTPUT_ROOT}/${job}"
  echo "==> submitting ${job}"
  hadoop jar "${STREAMING_JAR}" org.apache.hadoop.streaming.HadoopStreaming \
    -files "${STREAMING_DIR}/_mrlib.py,${STREAMING_DIR}/${mapper},${STREAMING_DIR}/${reducer}" \
    -input "${HDFS_INPUT}" \
    -output "${out}" \
    -mapper "python3 ${mapper}" \
    -reducer "python3 ${reducer}" \
    -jobconf stream.num.map.output.key.fields=2 \
    -jobconf mapreduce.job.reduces=2 \
    -jobconf mapreduce.job.reduce.slowstart.completedmaps=0.8
}

case "${JOB}" in
  temp_region_year) submit temp_region_year mapper_temp_region_year.py reducer_temp_region_year.py ;;
  precip_region)    submit precip_region    mapper_precip_region.py    reducer_precip_region.py ;;
  obs_count)        submit obs_count       mapper_obs_count.py        reducer_obs_count.py ;;
  all)
    submit temp_region_year mapper_temp_region_year.py reducer_temp_region_year.py
    submit precip_region    mapper_precip_region.py    reducer_precip_region.py
    submit obs_count        mapper_obs_count.py        reducer_obs_count.py
    ;;
  *) echo "unknown job ${JOB}" >&2; exit 2 ;;
esac

echo "==> done; fetch results with: hadoop fs -getmerge ${OUTPUT_ROOT}/<job> <job>.tsv"
