"""Upload handling: safe temp storage, format sniffing, bounded streaming readers.

Large files are never loaded whole into memory: CSV and JSONL are read row by row, and
Excel uses read-only streaming. Every row iteration is capped by MAX_UPLOAD_ROWS.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from django.conf import settings

from common.env import REPO_ROOT
from common.exceptions import ValidationError
from common.logging_config import get_logger

logger = get_logger("earthscape.uploads")

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
EXTENSIONS = {"csv", "tsv", "txt", "json", "jsonl", "xls", "xlsx"}
MAX_PREVIEW_COLUMNS = 40
UPLOAD_ROOT = Path(settings.APP.get("UPLOAD_TEMP_DIR", "artifacts/uploads"))


def _upload_root() -> Path:
    root = UPLOAD_ROOT if UPLOAD_ROOT.is_absolute() else REPO_ROOT / UPLOAD_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class StoredFile:
    ref: str
    path: Path
    original_name: str
    size_bytes: int
    sha256: str
    fmt: str
    encoding: str = "utf-8"
    delimiter: str = ","
    sheet_name: str | None = None

    def as_dict(self) -> dict:
        return {
            "ref": self.ref,
            "file_name": self.original_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "format": self.fmt,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "sheet_name": self.sheet_name,
            "storage_uri": str(self.path),
        }


def detect_format(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] or "").lower()
    if ext not in EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '.{ext or 'unknown'}'. Allowed: "
            f"{', '.join(sorted(EXTENSIONS))}.",
            code="unsupported_file_type",
        )
    if ext in {"csv"}:
        return "csv"
    if ext == "tsv":
        return "tsv"
    if ext in {"xls", "xlsx"}:
        return "excel"
    if ext == "jsonl":
        return "jsonl"
    if ext == "json":
        return "json"
    return "csv"


def store_upload(django_file) -> StoredFile:
    original_name = SAFE_NAME.sub("_", (django_file.name or "upload.bin"))[:120]
    fmt = detect_format(original_name)
    size = getattr(django_file, "size", None) or 0
    if size > int(settings.APP["MAX_UPLOAD_BYTES"]):
        raise ValidationError(
            f"File is {size/1e6:.1f} MB; the limit is "
            f"{int(settings.APP['MAX_UPLOAD_BYTES'])/1e6:.0f} MB.",
            code="file_too_large",
        )
    if size == 0:
        raise ValidationError("The uploaded file is empty.", code="empty_file")

    # Refuse obviously unsafe payloads masquerading as data files.
    head = django_file.read(512)
    try:
        django_file.seek(0)
    except Exception:
        pass
    if head.startswith(b"%PDF"):
        raise ValidationError("That file is a PDF, not a supported data file.", code="content_mismatch")
    if head.startswith(b"MZ") or (head.startswith(b"PK\x03\x04") and fmt != "xlsx"):
        raise ValidationError("File content does not match an allowed data format.", code="content_mismatch")

    ref = f"upl_{uuid.uuid4().hex[:16]}"
    path = _upload_root() / f"{ref}.{fmt if fmt != 'excel' else original_name.rsplit('.', 1)[-1]}"
    digest = hashlib.sha256()
    written = 0
    with path.open("wb") as handle:
        for chunk in django_file.chunks():
            digest.update(chunk)
            handle.write(chunk)
            written += len(chunk)
            if written > int(settings.APP["MAX_UPLOAD_BYTES"]):
                handle.close()
                path.unlink(missing_ok=True)
                raise ValidationError("File exceeded the size limit while streaming.",
                                      code="file_too_large")

    encoding, delimiter = "utf-8", ("," if fmt != "tsv" else "\t")
    if fmt in {"csv", "tsv", "txt", "jsonl", "json"}:
        encoding, delimiter = _sniff_text_shape(path, delimiter)

    stored = StoredFile(ref=ref, path=path, original_name=original_name, size_bytes=written,
                        sha256=digest.hexdigest(), fmt=fmt, encoding=encoding, delimiter=delimiter)
    logger.info("stored upload %s (%s bytes)", ref, written, extra={"event": "upload_stored"})
    return stored


def _sniff_text_shape(path: Path, default_delimiter: str) -> tuple[str, str]:
    raw = path.read_bytes()
    encoding = "utf-8"
    if raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        try:
            raw[:200_000].decode("utf-8")
        except UnicodeDecodeError:
            encoding = "latin-1"
    delimiter = default_delimiter
    if default_delimiter == ",":
        sample = "\n".join(raw[:20_000].decode(encoding, errors="replace").splitlines()[:20])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
    return encoding, delimiter


def resolve(ref: str) -> StoredFile:
    """Look up a previously stored upload by its reference."""
    safe = SAFE_NAME.sub("_", ref)[:64]
    matches = sorted(_upload_root().glob(f"{safe}.*"))
    if not matches:
        raise ValidationError("Validation session not found. Upload the file again.",
                              code="upload_not_found")
    path = matches[0]
    fmt = path.suffix.lstrip(".")
    fmt = {"xls": "excel", "xlsx": "excel"}.get(fmt, fmt)
    return StoredFile(
        ref=safe,
        path=path,
        original_name=safe,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        fmt=fmt,
        encoding="utf-8",
        delimiter=",",
    )


@dataclass
class HeaderInfo:
    columns: list[str]
    sample_rows: list[dict]
    detected_types: dict[str, str]
    row_estimate: int
    notes: list[str] = field(default_factory=list)


def read_header(stored: StoredFile, sample_limit: int = 25) -> HeaderInfo:
    columns: list[str] = []
    samples: list[dict] = []
    notes: list[str] = []
    estimate = 0
    for index, row in enumerate(iter_rows(stored, max_rows=sample_limit)):
        if index == 0:
            columns = list(row.keys())[:MAX_PREVIEW_COLUMNS]
            if len(row) > MAX_PREVIEW_COLUMNS:
                notes.append(f"only the first {MAX_PREVIEW_COLUMNS} columns are shown for mapping")
        if len(samples) < sample_limit - 1:
            samples.append({k: row.get(k) for k in columns})
        estimate += 1
    if not columns:
        raise ValidationError("No header row could be read from the file.", code="no_header")

    detected = {col: _column_type([s.get(col) for s in samples]) for col in columns}
    return HeaderInfo(columns=columns, sample_rows=samples, detected_types=detected,
                      row_estimate=estimate, notes=notes)


def iter_rows(stored: StoredFile, max_rows: int | None = None) -> Iterator[dict]:
    """Stream rows as dicts. Yields header-derived keys only."""
    limit = max_rows or int(settings.APP["MAX_UPLOAD_ROWS"])
    if stored.fmt in {"csv", "tsv", "txt"}:
        yield from _iter_delimited(stored, limit)
    elif stored.fmt == "jsonl":
        yield from _iter_jsonl(stored.path, limit)
    elif stored.fmt == "json":
        yield from _iter_json_doc(stored.path, limit)
    elif stored.fmt == "excel":
        yield from _iter_excel(stored.path, limit)
    else:
        raise ValidationError(f"Cannot read format '{stored.fmt}'.", code="unsupported_format")


def _iter_delimited(stored: StoredFile, limit: int) -> Iterator[dict]:
    with stored.path.open("r", encoding=stored.encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=stored.delimiter)
        header: list[str] | None = None
        emitted = 0
        for raw in reader:
            if header is None:
                header = [(col or f"column_{i}").strip() for i, col in enumerate(raw)]
                continue
            if emitted >= limit:
                return
            yield {header[i]: (raw[i] if i < len(raw) else None) for i in range(len(header))}
            emitted += 1


def _iter_jsonl(path: Path, limit: int) -> Iterator[dict]:
    emitted = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                yield {"_malformed_line": line[:500]}
                continue
            if emitted >= limit:
                return
            if isinstance(row, dict):
                yield row
            elif isinstance(row, list):
                for item in row:
                    if isinstance(item, dict):
                        yield item
            emitted += 1


def _iter_json_doc(path: Path, limit: int) -> Iterator[dict]:
    """Accept {records:[...]}, [...] or a dict-of-arrays column store."""
    text = path.read_text(encoding="utf-8", errors="replace")
    payload = json.loads(text)
    if isinstance(payload, dict):
        for key in ("records", "data", "observations", "rows", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            keys = [k for k, v in payload.items() if isinstance(v, list)]
            if keys and all(len(payload[k]) == len(payload[keys[0]]) for k in keys):
                length = len(payload[keys[0]])
                for index in range(min(length, limit)):
                    yield {k: payload[k][index] for k in keys}
                return
    if isinstance(payload, list):
        for row in payload[:limit]:
            if isinstance(row, dict):
                yield row


def _iter_excel(path: Path, limit: int) -> Iterator[dict]:
    from openpyxl import load_workbook

    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    header: list[str] | None = None
    emitted = 0
    try:
        for raw in sheet.iter_rows(values_only=True):
            if header is None:
                header = [str(col).strip() if col is not None else f"column_{i}"
                          for i, col in enumerate(raw)]
                continue
            if emitted >= limit:
                break
            yield {header[i]: (raw[i] if i < len(raw) else None) for i in range(len(header))}
            emitted += 1
    finally:
        workbook.close()


def _column_type(values: list) -> str:
    from common.canonical import is_missing, to_float

    numeric = 0
    textual = 0
    for value in values:
        if is_missing(value):
            continue
        if to_float(value) is not None:
            numeric += 1
        else:
            text = str(value)
            if re.search(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}", text):
                return "date"
            textual += 1
    if numeric and not textual:
        return "number"
    if numeric and textual:
        return "mixed"
    return "text"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows_chunk(stored: StoredFile, count: int) -> list[dict]:
    out: list[dict] = []
    for index, row in enumerate(iter_rows(stored, max_rows=count)):
        out.append(row)
    return out
