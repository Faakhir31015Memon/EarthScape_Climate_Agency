"""Kafka ingestion (guarded) — producer and consumer for canonical observations.

Kafka is optional infrastructure: ``kafka-python`` must be importable and a
broker reachable via ``KAFKA_BROKERS``; the topic comes from
``KAFKA_CLIMATE_TOPIC`` (default ``earthscape.climate.observations``).  Without
those, this module reports ``unavailable`` honestly and the platform uses the
polling path instead — it never pretends a broker exists.

Guarantees implemented here:
* idempotent consumption keyed by ``dedupe_key`` (checkpointed state file),
* dead-letter file for messages that fail validation
  (``artifacts/checkpoints/kafka_dead_letter.jsonl``),
* checkpoint of processed offsets/counts (``artifacts/checkpoints/kafka_consumer.json``),
* every written record passes ``streaming.schema_compat.assert_canonical``, so
  stream output is schema-identical to batch output.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline_config import ENV, checkpoints_root, layer_path
from validation.dedupe import DeduplicationIndex
from validation.quality import validate_record

DEFAULT_TOPIC = "earthscape.climate.observations"


class KafkaUnavailable(Exception):
    pass


def kafka_importable() -> bool:
    try:
        import kafka  # noqa: F401

        return True
    except Exception:
        return False


def require_kafka(role: str):
    if not kafka_importable():
        raise KafkaUnavailable(
            "kafka-python is not installed; Kafka " + role +
            " is unavailable on this machine (use streaming/polling_ingest.py instead)")
    brokers = ENV.raw("KAFKA_BROKERS")
    if not brokers:
        raise KafkaUnavailable("KAFKA_BROKERS is not set; cannot contact a cluster")
    import kafka

    return kafka, brokers


def topic() -> str:
    return ENV.raw("KAFKA_CLIMATE_TOPIC", DEFAULT_TOPIC)


# ---------------------------------------------------------------- producer
def produce_records(records, key_fn=lambda r: r.get("dedupe_key", "").encode()):
    kafka, brokers = require_kafka("producer")
    from kafka import KafkaProducer

    producer = KafkaProducer(bootstrap_servers=brokers.split(","),
                             value_serializer=None)
    sent = 0
    try:
        for rec in records:
            fut = producer.send(topic(), key=key_fn(rec),
                                value=json.dumps(rec, ensure_ascii=False).encode("utf-8"))
            fut.get(timeout=10)
            sent += 1
    finally:
        producer.flush()
        producer.close()
    return {"status": "ok", "topic": topic(), "sent": sent}


def produce_file(path: Path):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return produce_records(records)


# ---------------------------------------------------------------- consumer
def _consumer_checkpoint_path() -> Path:
    return checkpoints_root() / "kafka_consumer.json"


def _dead_letter_path() -> Path:
    return checkpoints_root() / "kafka_dead_letter.jsonl"


def _seen_path() -> Path:
    return checkpoints_root() / "kafka_seen_keys.txt"


def _load_seen() -> DeduplicationIndex:
    seen = set()
    p = _seen_path()
    if p.is_file():
        seen = set(p.read_text(encoding="utf-8").split())
    return DeduplicationIndex(seen)


def consume_to_lake(max_records: int = 10_000, timeout_seconds: int = 30) -> dict:
    """Consume a bounded batch from the topic into raw/stream-kafka JSONL."""
    kafka, brokers = require_kafka("consumer")
    from kafka import KafkaConsumer

    consumer = KafkaConsumer(topic(), bootstrap_servers=brokers.split(","),
                             group_id=ENV.raw("KAFKA_CONSUMER_GROUP", "earthscape-pipeline"),
                             enable_auto_commit=False,
                             auto_offset_reset="earliest")
    index = _load_seen()
    checkpoint = json.loads(_consumer_checkpoint_path().read_text(encoding="utf-8")) \
        if _consumer_checkpoint_path().is_file() else {"processed": 0, "rejected": 0, "duplicates": 0}
    stream_dir = layer_path("raw", "stream-kafka")
    batch_file = stream_dir / f"kafka-batch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    written = 0
    messages = 0
    new_keys: list = []
    try:
        with open(batch_file, "w", encoding="utf-8") as out, \
                open(_dead_letter_path(), "a", encoding="utf-8") as dlq:
            for message in consumer:
                messages += 1
                try:
                    raw = json.loads(message.value.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    checkpoint["rejected"] += 1
                    dlq.write(json.dumps({"reason": f"unparsable: {exc}", "offset": message.offset,
                                          "raw_preview": str(message.value)[:200]}) + "\n")
                    continue
                outcome = validate_record(raw)
                if outcome.record is None:
                    checkpoint["rejected"] += 1
                    dlq.write(json.dumps({"reason": "validation rejected", "offset": message.offset,
                                          "issues": outcome.issues[:5],
                                          "dedupe_key": raw.get("dedupe_key")}) + "\n")
                    continue
                if not index.accept(outcome.record):
                    checkpoint["duplicates"] += 1
                    continue
                from streaming.schema_compat import assert_canonical

                assert_canonical(outcome.record, context="kafka")
                out.write(json.dumps(outcome.record, ensure_ascii=False) + "\n")
                new_keys.append(outcome.record["dedupe_key"])
                written += 1
                if written >= max_records or messages >= max_records * 4:
                    break
            consumer.commit()
        if written == 0:
            batch_file.unlink(missing_ok=True)
        else:
            with open(_seen_path(), "a", encoding="utf-8") as fh:
                fh.write("\n".join(new_keys) + "\n")
    finally:
        consumer.close()
    checkpoint.update({"processed": checkpoint.get("processed", 0) + written,
                       "last_run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "persisted_seen_keys_file": str(_seen_path())})
    _consumer_checkpoint_path().write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    return {"status": "ok", "topic": topic(), "messages_read": messages, "written": written,
            "checkpoint": checkpoint, "batch_file": str(batch_file) if written else None}


def status() -> dict:
    if not kafka_importable():
        return {"status": "unavailable", "reason": "kafka-python not installed"}
    if not ENV.raw("KAFKA_BROKERS"):
        return {"status": "unavailable", "reason": "KAFKA_BROKERS not set"}
    try:
        from kafka import KafkaAdminClient

        KafkaAdminClient(bootstrap_servers=ENV.raw("KAFKA_BROKERS").split(","),
                         request_timeout_ms=3000)
        return {"status": "ok", "brokers": ENV.raw("KAFKA_BROKERS"), "topic": topic()}
    except Exception as exc:
        return {"status": "unavailable", "reason": f"broker unreachable: {type(exc).__name__}"}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kafka ingest (guarded)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_prod = sub.add_parser("produce")
    p_prod.add_argument("file", type=Path)
    sub.add_parser("consume").add_argument("--max", type=int, default=1000)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.cmd == "produce":
        print(json.dumps(produce_file(args.file), indent=2))
    elif args.cmd == "consume":
        print(json.dumps(consume_to_lake(max_records=args.max), indent=2))
    else:
        print(json.dumps(status(), indent=2))
