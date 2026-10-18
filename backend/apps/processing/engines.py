"""Engine availability probes used by processing endpoints and health checks.

Design rules (enforced in code):

* Never claim an engine is available without actually probing it.
* Every unavailable result must name the specific missing env key, package or
  service so an operator knows what to fix.
* Probes are cheap: ``importlib`` checks, ``shutil.which`` for CLI binaries,
  one ``subprocess.run`` for ``hadoop version`` and one socket ``connect_ex`` for
  the Kafka broker, with a short timeout. Starting a real SparkSession is
  deliberately not performed here - the workflow itself reports the honest
  session outcome when the job actually runs.
* The composite return shape mirrors :class:`EngineHealth` in
  ``frontend/src/types/api.ts``.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from config import env  # noqa: E402


# --------------------------------------------------------------------------- probes
def _spark_installed() -> Tuple[bool, Optional[str], Optional[str]]:
    """Cheap check: ``pyspark`` importable + reported version. Never starts a session."""
    spec = importlib.util.find_spec("pyspark")
    if spec is None:
        return False, None, "pyspark is not importable in this interpreter"
    try:
        import pyspark  # type: ignore

        version = getattr(pyspark, "__version__", None)
    except Exception as exc:  # noqa: BLE001 - report the real import failure
        return False, None, f"pyspark import failed: {type(exc).__name__}: {str(exc)[:150]}"
    return True, version, None


def _hadoop_bin_probe() -> Tuple[Optional[str], Optional[str]]:
    """Return ``(resolved_binary_path, detail)``.

    ``shutil.which`` on the configured ``HADOOP_BIN`` (default ``hadoop``) —
    never a client-supplied path.
    """
    configured = (env.get_raw("HADOOP_BIN") or "").strip()
    candidate = configured or "hadoop"
    resolved = shutil.which(candidate)
    if not resolved:
        if configured:
            return None, f"HADOOP_BIN='{candidate}' is set but not present on PATH"
        return None, "HADOOP_BIN is empty and no 'hadoop' executable is on PATH"
    return resolved, None


def _hadoop_version(resolved: str) -> Optional[str]:
    """Best-effort version read; returns ``None`` when the probe fails.

    Never trusts a client-supplied path: ``resolved`` comes from
    :func:`_hadoop_bin_probe` (which uses ``shutil.which`` on the env value).
    """
    try:
        result = subprocess.run([resolved, "version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    first_line = (result.stdout or "").splitlines()
    if not first_line:
        return None
    return first_line[0].strip()[:120]


def _probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _parse_broker(raw: Optional[str]) -> Optional[Tuple[str, int]]:
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    if not first:
        return None
    host, _, port_text = first.partition(":")
    try:
        port = int(port_text or "9092")
    except ValueError:
        return None
    return host, port


# ---------------------------------------------------------------------- data lake
def _data_lake_entry() -> Dict[str, Any]:
    try:
        from common.data_lake import get_data_lake

        lake = get_data_lake()
        mode_label = lake.mode_label
        root = str(lake.local_root) if lake.mode == "local" else lake.endpoint
        reachable = True
        free_bytes: Optional[int] = None
        if lake.mode == "local":
            try:
                usage = shutil.disk_usage(str(lake.local_root))
                free_bytes = int(usage.free)
            except OSError:
                free_bytes = None
        else:
            # HDFS mode implies the WebHDFS NameNode answered for ``_probe_hdfs``.
            reachable = lake.mode == "hdfs"
        detail = getattr(lake, "_mode_error", None)
        if detail:
            reachable = False
        return {
            "mode": mode_label,
            "root": root,
            "reachable": bool(reachable),
            "free_bytes": free_bytes,
            "detail": detail or (f"backend reports {mode_label}"),
        }
    except Exception as exc:  # noqa: BLE001 - a broken lake must not break the probe
        return {"mode": "unavailable", "root": None, "reachable": False,
                "free_bytes": None, "detail": f"data lake probe failed: {type(exc).__name__}"}


# --------------------------------------------------------------------------- hdfs
def _hdfs_entry() -> Dict[str, Any]:
    endpoint = (env.get_raw("HDFS_ENDPOINT") or "").strip()
    if not endpoint:
        return {"configured": False, "reachable": False, "mode": "local-data-lake",
                "endpoint": None,
                "detail": "HDFS_ENDPOINT is empty; the platform uses the local data-lake directory."}
    configured = True
    # Only attempt a client probe when the ``hdfs`` package is importable.
    if importlib.util.find_spec("hdfs") is None:
        return {"configured": configured, "reachable": False, "mode": "unknown",
                "endpoint": endpoint,
                "detail": "the `hdfs` python package is not installed; the "
                          "data lake falls back to the local directory"}
    try:
        from common.data_lake import get_data_lake

        lake = get_data_lake()
        # ``lake.mode`` is what the platform itself will actually use after
        # probing; never re-implement the probe differently here.
        reachable = lake.mode == "hdfs"
        detail = getattr(lake, "_mode_error", None) or f"mode resolved to {lake.mode_label}"
        return {"configured": configured, "reachable": reachable, "mode": lake.mode_label,
                "endpoint": endpoint, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"configured": configured, "reachable": False, "mode": "unknown",
                "endpoint": endpoint,
                "detail": f"HDFS probe failed: {type(exc).__name__}"}


# --------------------------------------------------------------------------- spark
def _spark_entry() -> Dict[str, Any]:
    installed, version, error = _spark_installed()
    enabled = env.get_bool("SPARK_ENABLED", True)
    if not installed:
        return {"installed": False, "version": None, "session_startable": False,
                "error": error or "PySpark is not importable"}
    if not enabled:
        return {"installed": True, "version": version, "session_startable": False,
                "error": "SPARK_ENABLED is false in .env; Spark jobs are disabled"}
    # We have not verified an actual session start; the workflow runner does that.
    return {"installed": True, "version": version, "session_startable": None,
            "error": None,
            "note": "session start is verified when the Spark job actually runs; "
                    "this probe only confirms the PySpark install and SPARK_ENABLED flag"}


# -------------------------------------------------------------------------- hadoop
def _hadoop_entry() -> Dict[str, Any]:
    resolved, missing_detail = _hadoop_bin_probe()
    cluster_configured = bool(env.get_raw("HADOOP_HOME") or env.get_raw("HDFS_ENDPOINT"))
    if resolved is None:
        return {"bin_configured": False, "version": None,
                "cluster_configured": cluster_configured,
                "detail": missing_detail or "hadoop binary not found on PATH"}
    version = _hadoop_version(resolved)
    if not cluster_configured:
        return {"bin_configured": True, "version": version,
                "cluster_configured": False,
                "detail": "hadoop binary present but neither HADOOP_HOME nor "
                          "HDFS_ENDPOINT is set - no cluster to submit to"}
    detail = f"hadoop binary resolved to {resolved}"
    if version:
        detail += f" ({version})"
    return {"bin_configured": True, "version": version,
            "cluster_configured": True, "detail": detail}


# --------------------------------------------------------------------------- kafka
def _kafka_entry() -> Dict[str, Any]:
    enabled = env.get_bool("KAFKA_ENABLED", False)
    brokers = (env.get_raw("KAFKA_BROKERS") or "").strip() or None
    if not enabled:
        return {"enabled": False, "brokers": brokers, "reachable": False,
                "detail": "KAFKA_ENABLED is false; the streaming workflow uses the "
                          "local spool directory instead of a live broker"}
    if not brokers:
        return {"enabled": True, "brokers": None, "reachable": False,
                "detail": "KAFKA_BROKERS is empty"}
    parsed = _parse_broker(brokers)
    if parsed is None:
        return {"enabled": True, "brokers": brokers, "reachable": False,
                "detail": "KAFKA_BROKERS is not in host:port form"}
    host, port = parsed
    reachable = _probe_tcp(host, port)
    detail = "broker TCP reachable" if reachable else f"could not TCP-connect to {host}:{port}"
    return {"enabled": True, "brokers": brokers, "reachable": reachable, "detail": detail}


# -------------------------------------------------------------------------- airflow
def _airflow_entry() -> Dict[str, Any]:
    enabled = env.get_bool("AIRFLOW_ENABLED", False)
    if not enabled:
        return {"enabled": False,
                "detail": "AIRFLOW_ENABLED is false; scheduled tasks use the in-process "
                          "thread pool or Celery beat instead"}
    base = env.get_raw("AIRFLOW_BASE_URL") or "not configured"
    return {"enabled": True, "detail": f"Airflow base URL reported as {base}; "
                                       "DAG reachability is not probed at request time"}


# ------------------------------------------------------------------------ composite
def _current_processing_mode(hdfs_entry: Dict[str, Any], spark_entry: Dict[str, Any],
                             hadoop_entry: Dict[str, Any], kafka_entry: Dict[str, Any]) -> str:
    """Pick the mode the platform *will* actually use for the next job."""
    if spark_entry.get("installed") and spark_entry.get("session_startable") is not False \
            and env.get_bool("SPARK_ENABLED", True):
        return "pyspark"
    if hadoop_entry.get("bin_configured") and hadoop_entry.get("cluster_configured"):
        return "hadoop-streaming"
    if hdfs_entry.get("reachable"):
        return "local-streaming-simulated"
    if kafka_entry.get("reachable"):
        return "streaming"
    return "mongodb"


def engine_health() -> Dict[str, Any]:
    """Composite EngineHealth shape (see frontend/src/types/api.ts)."""
    hdfs = _hdfs_entry()
    spark = _spark_entry()
    hadoop = _hadoop_entry()
    kafka = _kafka_entry()
    airflow = _airflow_entry()
    lake = _data_lake_entry()
    mode = _current_processing_mode(hdfs, spark, hadoop, kafka)
    notes: List[str] = []
    if not spark["installed"]:
        notes.append(f"PySpark unavailable: {spark['error']}")
    elif spark["session_startable"] is None:
        notes.append("Spark session start is verified at job execution time, not here")
    if not hadoop["bin_configured"]:
        notes.append(f"Hadoop CLI unavailable: {hadoop['detail']}")
    if not hdfs["reachable"]:
        notes.append(f"HDFS not reachable: {hdfs['detail']}")
    if not kafka["reachable"]:
        notes.append(f"Kafka broker not reachable: {kafka['detail']}")
    if not airflow["enabled"]:
        notes.append("Airflow integration disabled")
    return {
        "hdfs": hdfs,
        "spark": spark,
        "hadoop": hadoop,
        "kafka": kafka,
        "airflow": airflow,
        "data_lake": lake,
        "current_processing_mode": mode,
        "note": "; ".join(notes) if notes else "all primary engines reported healthy",
    }


def workflow_availability(engine: str, workflow: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Decide ``available`` + ``unavailable_reason`` for one workflow entry."""
    health = engine_health()
    if engine == "pyspark":
        spark = health["spark"]
        if not spark["installed"]:
            return False, spark.get("error") or "PySpark is not importable"
        if not env.get_bool("SPARK_ENABLED", True):
            return False, "SPARK_ENABLED is false in configuration"
        return True, None
    if engine == "streaming":
        spark = health["spark"]
        if not spark["installed"]:
            return False, spark.get("error") or "PySpark is not importable (structured streaming needs it)"
        if not env.get_bool("SPARK_ENABLED", True):
            return False, "SPARK_ENABLED is false in configuration"
        return True, None
    if engine == "mapreduce":
        # The streaming orchestrator has a *documented* local fallback that
        # runs the same mapper/reducer code as subprocesses of ``sys.executable``,
        # so this workflow is genuinely available even without hadoop.
        if shutil.which("python3") is None and shutil.which("python") is None \
                and not Path(sys.executable).is_file():
            return False, "no python interpreter available to run the mapper/reducer subprocesses"
        return True, None
    if engine == "mongodb":
        try:
            from common.database import get_database

            db = get_database()
            if db.mode == "uninitialised":
                return False, "the document store is not initialised"
            return True, None
        except Exception as exc:  # noqa: BLE001
            return False, f"MongoDB probe failed: {type(exc).__name__}"
    return False, f"unknown engine '{engine}'"


# ------------------------------------------------------------------- dependency CLI
def external_command_probe(binaries: List[str]) -> Dict[str, Optional[str]]:
    """Return ``{binary: resolved_path or None}`` for ``shutil.which`` on each name."""
    out: Dict[str, Optional[str]] = {}
    for name in binaries:
        out[name] = shutil.which(name)
    return out


# Re-export a cheap env snapshot so the monitoring views do not have to
# import ``config.env`` directly.
def env_raw(name: str) -> Optional[str]:
    return env.get_raw(name)


__all__ = ["engine_health", "workflow_availability", "external_command_probe",
           "env_raw", "os", "sys"]
