"""Kafka producer stub with an honest spool-directory fallback.

When ``KAFKA_ENABLED=false`` (the state of this development machine) there is no
broker to talk to, and kafka-python is deliberately NOT a dependency. In that
case events are appended as JSON files to a spool directory under
``PROCESSING_SCRATCH_DIR/streaming/spool`` - the same JSON event schema that the
real producer would publish to ``KAFKA_TOPIC_CLIMATE``. The Spark Structured
Streaming reader consumes that spool unchanged, so the only thing that differs
between modes is transport, not data contract.

When ``KAFKA_ENABLED=true`` this module tries to import ``kafka-python`` and
connect to ``KAFKA_BROKERS``. If either fails it records the *real* error on the
producer status and refuses to pretend events were sent to a broker (they are
still spooled, and the status says so).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


def _env():
    import sys

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from config import env

    return env


class KafkaProducerStub:
    def __init__(self, spool_dir: Optional[Path] = None, batch_size: int = 200):
        env = _env()
        self.enabled = env.get_bool("KAFKA_ENABLED", False)
        self.brokers = env.get_str("KAFKA_BROKERS", "localhost:9092")
        self.topic = env.get_str("KAFKA_TOPIC_CLIMATE", "climate.observations.v1")
        base = env.get_str("PROCESSING_SCRATCH_DIR", str(BACKEND_DIR / ".data" / "processing"))
        self.spool_dir = Path(spool_dir or (Path(base) / "streaming" / "spool"))
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self._lock = threading.Lock()
        self._buffer: List[Dict[str, Any]] = []
        self._producer = None
        self._broker_error: Optional[str] = None
        self.sent_to_broker = 0
        self.spooled = 0
        if self.enabled:
            self._try_real_producer()

    # ----------------------------------------------------------------- broker
    def _try_real_producer(self) -> None:
        try:
            from kafka import KafkaProducer  # kafka-python
        except ImportError as exc:
            self._broker_error = f"kafka-python not installed: {exc}"
            logger.warning("Kafka enabled but kafka-python missing; events will be spooled only")
            return
        try:
            self._producer = KafkaProducer(
                bootstrap_servers=[b.strip() for b in self.brokers.split(",")],
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                acks="all",
                linger_ms=50,
            )
            self._producer.flush(timeout=10)
        except Exception as exc:  # noqa: BLE001 - broker unreachable is a status, not a crash
            self._producer = None
            self._broker_error = str(exc)[:200]

    def broker_status(self) -> Dict[str, Any]:
        return {"kafka_enabled": self.enabled, "brokers": self.brokers, "topic": self.topic,
                "connected": self._producer is not None,
                "error": self._broker_error,
                "delivery": "kafka-broker" if self._producer else "spool-directory (local)"}

    # ------------------------------------------------------------------- send
    def send_observation(self, observation: Dict[str, Any], *, source: str) -> Dict[str, Any]:
        # ``payload`` is the observation JSON-ENCODED as a string. Keeping it a
        # string means one malformed observation can never break the Spark
        # Structured Streaming schema of the whole event stream; the processor
        # decodes (and validates) it per event.
        event = {
            "event_id": str(uuid.uuid4()),
            "event_time": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "topic": self.topic,
            "payload": json.dumps(observation, default=str),
        }
        self._buffer.append(event)
        if len(self._buffer) >= self.batch_size:
            self.flush()
        return event

    def send_many(self, observations: Iterable[Dict[str, Any]], *, source: str) -> int:
        count = 0
        for observation in observations:
            self.send_observation(observation, source=source)
            count += 1
        self.flush()
        return count

    def flush(self) -> Dict[str, Any]:
        with self._lock:
            if not self._buffer:
                return {"sent": 0, "mode": "none"}
            events, self._buffer = self._buffer, []
        if self._producer is not None:
            try:
                for event in events:
                    self._producer.send(self.topic, event)
                self._producer.flush(timeout=15)
                self.sent_to_broker += len(events)
                return {"sent": len(events), "mode": "kafka-broker"}
            except Exception as exc:  # noqa: BLE001 - fall back to spool, record the truth
                self._broker_error = str(exc)[:200]
                self._producer = None
        path = self._next_spool_file()
        with path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, default=str) + "\n")
        self.spooled += len(events)
        mode = "spool-directory (kafka disabled)" if not self.enabled \
            else f"spool-directory (broker send failed: {self._broker_error})"
        return {"sent": len(events), "mode": mode, "path": str(path)}

    def _next_spool_file(self) -> Path:
        # Microsecond stamp + uuid suffix: two flushes inside the same
        # microsecond must not overwrite each other's file (observed on this
        # box during the first verification run).
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        return self.spool_dir / f"events-{stamp}-{uuid.uuid4().hex[:8]}.json"


_PRODUCER: Optional[KafkaProducerStub] = None


def get_producer() -> KafkaProducerStub:
    global _PRODUCER
    if _PRODUCER is None:
        _PRODUCER = KafkaProducerStub()
    return _PRODUCER
