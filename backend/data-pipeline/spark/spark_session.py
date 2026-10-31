"""SparkSession factory driven by environment configuration.

``SPARK_MASTER`` (default ``local[*]``), ``SPARK_APP_NAME``, ``SPARK_DRIVER_MEMORY``,
``SPARK_EXECUTOR_MEMORY`` and ``SPARK_MIN_PARALLELISM`` come from ``.env`` via
``backend/config/env.py``. If PySpark cannot start (missing JVM, out of memory),
this raises :class:`SparkUnavailable` carrying the *real* error text - callers
must surface it as a job failure rather than fake success.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class SparkUnavailable(RuntimeError):
    def __init__(self, message: str, *, cause: Optional[str] = None):
        super().__init__(message)
        self.cause = cause


def spark_available() -> bool:
    try:
        import pyspark  # noqa: F401

        return True
    except ImportError:
        return False


def spark_version() -> Optional[str]:
    try:
        import pyspark

        return getattr(pyspark, "__version__", None)
    except ImportError:
        return None


def build_spark_session(app_name: Optional[str] = None, *, local_cores: Optional[int] = None,
                        driver_memory: Optional[str] = None):
    from config import env

    if not env.get_bool("SPARK_ENABLED", True):
        raise SparkUnavailable("SPARK_ENABLED=false in configuration; Spark jobs are disabled.")
    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise SparkUnavailable("PySpark is not installed in this environment.", cause=str(exc)) from exc

    # Spark spawns workers via PYSPARK_PYTHON. Without pinning, Windows can pick a
    # Microsoft Store Python that PySpark does not support (observed on this
    # machine: 3.13 workers dying with "Connection reset" socket errors). Pin both
    # driver and executor interpreters to the running venv.
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

    master = env.get_str("SPARK_MASTER", "local[*]")
    builder = (
        SparkSession.builder
        .master(master)
        .appName(app_name or env.get_str("SPARK_APP_NAME", "earthscape-climate-processing"))
        .config("spark.driver.memory", driver_memory or env.get_str("SPARK_DRIVER_MEMORY", "2g"))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .config("spark.sql.shuffle.partitions", str(env.get_int("SPARK_MIN_PARALLELISM", 4)))
    )
    # Windows dev box has no winutils.exe/hadoop.dll, so Hadoop's directory listing
    # crashes the JVM. Where the pure-Java shim (data-pipeline/streaming/java/, built
    # by build_localfs.ps1 and installed on the JVM classpath) exists, register it for
    # the local "file" scheme; it overrides only listStatus(). Real clusters use hdfs://.
    if sys.platform == "win32":
        shim = (Path(__file__).resolve().parents[1] / "streaming" / "jars"
                / "earthscape-localfs.jar")
        if shim.exists():
            builder = builder.config("spark.hadoop.fs.file.impl",
                                     "com.earthscape.hadoop.PureListLocalFileSystem")
    if master.startswith("local"):
        os.makedirs(str(_BACKEND / ".data" / "processing" / "spark-tmp"), exist_ok=True)
        builder = builder.config("spark.local.dir",
                                 str(_BACKEND / ".data" / "processing" / "spark-tmp"))
        builder = builder.config("spark.driver.extraJavaOptions",
                                 "-Dio.netty.tryReflectionSetAccessible=true")
    try:
        spark = builder.getOrCreate()
    except Exception as exc:  # noqa: BLE001 - surface the real JVM/Spark failure
        raise SparkUnavailable(
            f"Spark session could not start on master '{master}'.", cause=str(exc)[:800]
        ) from exc
    spark.sparkContext.setLogLevel("WARN")
    return spark


def session_summary(spark) -> dict:
    return {
        "master": spark.sparkContext.master,
        "version": spark.sparkContext.version,
        "app_id": spark.sparkContext.applicationId,
        "default_parallelism": spark.sparkContext.defaultParallelism,
    }
