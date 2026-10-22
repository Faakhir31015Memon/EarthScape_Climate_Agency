"""Import a spreadsheet or text data file straight into MongoDB.

Covers every reader the upload API accepts - .csv, .txt, .json/.ndjson, .xlsx, .xlsm,
.xltx, .xltm, .xls, .xlsb, .ods (including password-protected workbooks) and .parquet -
and writes the records as documents through the same PyMongo gateway the backend uses.

    python scripts/import_spreadsheet.py sample-data/format-fixtures/karachi_daily.ods
    python scripts/import_spreadsheet.py book.xlsb --collection raw_uploads --batch 2000
    python scripts/import_spreadsheet.py data.xlsx --dry-run --limit 50

``--dry-run`` reads and reports without touching the database. Records are stored
verbatim (plus ``imported_from`` / ``imported_at``); use the app's upload API for the
validated ``climate_observations`` pipeline with column mapping and unit checks.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.datasets import file_import  # noqa: E402
from common import db  # noqa: E402
from common.database import _safe_error  # noqa: E402


def _documents(rows, *, source: str, include_row_number: bool) -> list[dict]:
    out = []
    for index, record in rows:
        doc = {key: value for key, value in record.items() if key != "__trailing__"}
        if include_row_number:
            doc["source_row"] = index
        doc["imported_from"] = source
        doc["imported_at"] = datetime.now(timezone.utc)
        out.append(doc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", help="data file(s) to import")
    parser.add_argument("--collection", default="raw_imports",
                        help="target collection (default: raw_imports)")
    parser.add_argument("--database", default=None, help="override MONGODB_DATABASE")
    parser.add_argument("--batch", type=int, default=1000, help="insert batch size")
    parser.add_argument("--limit", type=int, default=0, help="stop after N records")
    parser.add_argument("--dry-run", action="store_true", help="read only, write nothing")
    parser.add_argument("--no-row-number", action="store_true",
                        help="do not add a source_row field")
    parser.add_argument("--clear", action="store_true",
                        help="drop the target collection before importing")
    args = parser.parse_args()

    if args.database:
        os.environ["MONGODB_DATABASE"] = args.database

    target = args.collection
    if not args.dry_run:
        probe = db.ping()
        if not probe.get("reachable"):
            print("MongoDB unreachable: " + _safe_error(Exception(probe.get("detail") or probe.get("error") or "")))
            print("Run `python scripts/verify_atlas.py --set-user <name>` first.")
            return 2
        print(f"Connected to {probe.get('database', target)} (server {probe.get('server_version')})")
        if args.clear:
            db.collection(target).drop()
            print(f"  dropped collection {target}")

    total = 0
    failures = 0
    for raw_path in args.files:
        path = str(ROOT / raw_path) if not Path(raw_path).is_absolute() else raw_path
        name = Path(path).name
        try:
            fmt = file_import.sniff_format(path, Path(path).suffix.lstrip("."))
            headers = file_import.read_headers(path, fmt)
            stream = file_import.open_file(path, fmt, max_rows=args.limit or None)
            rows = list(stream)
        except file_import.FileImportError as exc:
            failures += 1
            print(f"  {name:<34} FAILED  {exc}")
            continue

        documents = _documents(rows, source=name, include_row_number=not args.no_row_number)
        target = args.collection

        if args.dry_run:
            print(f"  {name:<34} {fmt:<10} rows={len(documents):<7} headers={headers[:4]}")
            total += len(documents)
            continue

        written = 0
        for start in range(0, len(documents), max(1, args.batch)):
            written += db.insert_many(target, documents[start:start + args.batch])
        total += written
        print(f"  {name:<34} {fmt:<10} inserted={written:<7} -> "
              f"{args.database or db.database_name()}.{target}")

    print("-" * 72)
    print(f"{total} record(s) processed, {failures} file(s) failed")
    if not args.dry_run and total:
        print(f"collection count now: {db.count(target)} documents in {target}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
