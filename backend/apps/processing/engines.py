"""Engine availability probes for the processing and health endpoints.

The composite shape mirrors EngineHealth in frontend/src/types/api.ts.
Probes never start a SparkSession; the job reports its own session outcome.
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


def _spark_installed() -> Tuple[bool, Optional[str], Optional[str]]:
    """Checks pyspark importable + reported version; never starts a session."""
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
    """shutil.which on the configured HADOOP_BIN, never a client-supplied path."""
    configured = (env.get_raw("HADOOP_BIN") or "").strip()
    candidate = configured or "hadoop"
    resolved = shutil.which(candidate)
    if not resolved:
        if configured:
            return None, f"HADOOP_BIN='{candidate}' is set but not present on PATH"
        return None, "HADOOP_BIN is empty and no 'hadoop' executable is on PATH"
    return resolved, None


def _hadoop_version(resolved: str) -> Optional[str]:
    """Best-effort version read; returns None when the probe fails."""
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
            # mode "hdfs" means the NameNode already answered inside get_data_lake
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


def _hdfs_entry() -> Dict[str, Any]:
    endpoint = (env.get_raw("HDFS_ENDPOINT") or "").strip()
    if not endpoint:
        return {"configured": False, "reachable": False, "mode": "local-data-lake",
                "endpoint": None,
                "detail": "HDFS_ENDPOINT is empty; the platform uses the local data-lake directory."}
    configured = True
    if importlib.util.find_spec("hdfs") is None:
        return {"configured": configured, "reachable": False, "mode": "unknown",
                "endpoint": endpoint,
                "detail": "the `hdfs` python package is not installed; the "
                          "data lake falls back to the local directory"}
    try:
        from common.data_lake import get_data_lake

        lake = get_data_lake()
        # lake.mode is what the platform uses after probing; don't re-implement here
        reachable = lake.mode == "hdfs"
        detail = getattr(lake, "_mode_error", None) or f"mode resolved to {lake.mode_label}"
        return {"configured": configured, "reachable": reachable, "mode": lake.mode_label,
                "endpoint": endpoint, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"configured": configured, "reachable": False, "mode": "unknown",
                "endpoint": endpoint,
                "detail": f"HDFS probe failed: {type(exc).__name__}"}


def _spark_entry() -> Dict[str, Any]:
    installed, version, error = _spark_installed()
    enabled = env.get_bool("SPARK_ENABLED", True)
    if not installed:
        return {"installed": False, "version": None, "session_startable": False,
                "error": error or "PySpark is not importable"}
    if not enabled:
        return {"installed": True, "version": version, "session_startable": False,
                "error": "SPARK_ENABLED is false in .env; Spark jobs are disabled"}
    return {"installed": True, "version": version, "session_startable": None,
            "error": None,
            "note": "session start is verified when the Spark job actually runs; "
                    "this probe only confirms the PySpark install and SPARK_ENABLED flag"}


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


def _current_processing_mode(hdfs_entry: Dict[str, Any], spark_entry: Dict[str, Any],
                             hadoop_entry: Dict[str, Any]) -> str:
    """Pick the mode the platform will use for the next job."""
    if spark_entry.get("installed") and spark_entry.get("session_startable") is not False \
            and env.get_bool("SPARK_ENABLED", True):
        return "pyspark"
    if hadoop_entry.get("bin_configured") and hadoop_entry.get("cluster_configured"):
        return "hadoop-streaming"
    if hdfs_entry.get("reachable"):
        return "local-streaming-simulated"
    return "mongodb"


def engine_health() -> Dict[str, Any]:
    hdfs = _hdfs_entry()
    spark = _spark_entry()
    hadoop = _hadoop_entry()
    lake = _data_lake_entry()
    mode = _current_processing_mode(hdfs, spark, hadoop)
    notes: List[str] = []
    if not spark["installed"]:
        notes.append(f"PySpark unavailable: {spark['error']}")
    elif spark["session_startable"] is None:
        notes.append("Spark session start is verified at job execution time, not here")
    if not hadoop["bin_configured"]:
        notes.append(f"Hadoop CLI unavailable: {hadoop['detail']}")
    if not hdfs["reachable"]:
        notes.append(f"HDFS not reachable: {hdfs['detail']}")
    return {
        "hdfs": hdfs,
        "spark": spark,
        "hadoop": hadoop,
        "data_lake": lake,
        "current_processing_mode": mode,
        "note": "; ".join(notes) if notes else "all primary engines reported healthy",
    }


def workflow_availability(engine: str, workflow: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
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
        # the streaming orchestrator falls back to running the mapper/reducer
        # as subprocesses of sys.executable, so hadoop isn't required
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


def external_command_probe(binaries: List[str]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for name in binaries:
        out[name] = shutil.which(name)
    return out


# re-export so the monitoring views don't import config.env directly
def env_raw(name: str) -> Optional[str]:
    return env.get_raw(name)


__all__ = ["engine_health", "workflow_availability", "external_command_probe",
           "env_raw", "os", "sys"]
