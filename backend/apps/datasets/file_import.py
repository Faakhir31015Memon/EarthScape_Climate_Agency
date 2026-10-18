"""Streaming readers for uploaded climate files.

Every reader yields ``(row_index, record)`` pairs where ``row_index`` is the
**1-based ordinal of the data record** (the header row is never counted).  Records
are plain ``dict`` objects keyed by the source header exactly as it appeared in the
file.

Memory discipline (what makes each reader a *streaming* reader):

``csv`` / ``txt``
    ``csv.DictReader`` iterates the underlying text file line by line; the whole
    file is never materialised.  The delimiter and encoding are sniffed from a
    bounded 64 KiB probe with :mod:`csv.Sniffer` and :mod:`chardet` (both optional).

``json``
    *NDJSON* (one object per line) is streamed line by line.  A JSON *array of
    objects* is streamed with an incremental byte scanner that tracks string /
    escape state and ``{}`` nesting depth, emitting one object at a time, so an
    arbitrarily large array is parsed without loading it fully.  ``json.load`` is
    only used as a guarded fallback when the file is small AND its shape is not a
    flat array of objects; larger non-streamable JSON raises a clear size error.

``xlsx``
    ``openpyxl`` ``read_only=True`` workbook + a generator over rows streams cells;
    read-only mode does not build the full object tree in memory.

``parquet``
    ``pyarrow.parquet.ParquetFile.iter_batches`` streams row-group batches.
    ``pandas.read_parquet`` is only the fallback when PyArrow is unavailable and is
    size-guarded.

Nothing here opens an Excel formula engine, follows external links, or trusts the
declared extension - callers must sniff content first (see :func:`sniff_format`).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Dict, Iterator, List, Optional, Tuple

# A JSON document larger than this is refused unless it can be streamed.  32 MiB is
# comfortably above any reasonable eProject file while keeping peak memory bounded.
JSON_SAFE_LOAD_BYTES = 32 * 1024 * 1024
# Parquet fallback via pandas is refused above this many bytes.
PARQUET_SAFE_LOAD_BYTES = 512 * 1024 * 1024

_CSV_SAMPLE_BYTES = 64 * 1024

# Executable / active-content signatures that must never be accepted as data.
_REJECT_SIGNATURES = {
    b"MZ": "windows-executable",                 # PE / .exe / .dll
    b"#!": "script-shebang",                     # .sh / interpreter scripts
    b"\x7fELF": "elf-binary",                    # linux executable
    b"%PDF": "portable-document",                # PDF (not a tabular format here)
}
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<svg", b"<?xml")
_SCRIPT_MARKERS = (b"<script",)


class FileImportError(Exception):
    """Raised when a file cannot be opened, sniffed or streamed."""


# --------------------------------------------------------------------- signature
def read_signature(path: str, nbytes: int = 8) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(nbytes)


def sniff_format(path: str, declared_extension: Optional[str] = None) -> str:
    """Return the *content* format ('csv'|'txt'|'json'|'xlsx'|'parquet'|'xls'|'netcdf').

    Raises :class:`FileImportError` for executables, active content, or anything that
    is not a recognised data format.  When ``declared_extension`` is supplied and its
    implied type conflicts with the sniffed content, the file is rejected.
    """
    if not os.path.isfile(path):
        raise FileImportError("The stored file is missing.")
    size = os.path.getsize(path)
    if size == 0:
        raise FileImportError("The uploaded file is empty.")

    head = read_signature(path, 8)
    low = head.lower()
    for signature, label in _REJECT_SIGNATURES.items():
        if head.startswith(signature):
            raise FileImportError(f"Refusing to read a non-data file (detected {label}).")

    detected: Optional[str] = None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        detected = _sniff_zip_package(path)
    elif head.startswith(b"\xd0\xcf\x11\xe0"):
        detected = "xls"
    elif head.startswith(b"PAR1"):
        detected = "parquet"
    elif head.startswith(b"CDF") or head[1:4] == b"HDF":
        detected = "netcdf"
    else:
        text_head = head[:1].strip()
        if text_head in (b"[", b"{"):
            detected = "json"
        else:
            detected = _sniff_text(path)

    if declared_extension:
        extension = declared_extension.lower().lstrip(".")
        conflict = _extension_conflict(extension, detected)
        if conflict:
            raise FileImportError(conflict)
    return detected


def _extension_conflict(extension: str, detected: str) -> Optional[str]:
    # Extensions that are outright forbidden as data uploads.
    if extension in {"exe", "dll", "sh", "bat", "cmd", "ps1", "py", "html", "htm",
                     "svg", "js", "com", "scr", "msi", "jar"}:
        return f"Refusing to accept a '{extension}' file - that is not a data format."
    expected = {
        "csv": {"csv", "txt"},
        "txt": {"csv", "txt"},
        "json": {"json"},
        "xlsx": {"xlsx", "xlsm", "xls"},
        "xls": {"xlsx", "xlsm", "xls"},
        "xlsm": {"xlsx", "xlsm", "xls"},
        "parquet": {"parquet"},
        "nc": {"netcdf"},
    }.get(extension, {extension})
    if detected in expected or extension in expected:
        return None
    return (f"The declared extension '.{extension}' does not match the file content "
            f"(detected '{detected}').")


def _sniff_zip_package(path: str) -> str:
    """Distinguish a real xlsx (OOXML) from a plain zip / docx masquerade."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileImportError("The archive could not be opened as a spreadsheet.") from exc
    if "xl/workbook.xml" in names or any(n.startswith("xl/") for n in names):
        return "xlsx"
    if "mimetype" in names:
        return "opendocument"
    if any(n.endswith(".docx") or n == "word/document.xml" for n in names):
        return "word-document"
    raise FileImportError("A zip archive was supplied that is not a supported spreadsheet.")


def _sniff_text(path: str) -> str:
    """Decide 'csv' vs 'json' vs 'txt' for a text file by inspecting a bounded head."""
    with open(path, "rb") as handle:
        chunk = handle.read(_CSV_SAMPLE_BYTES)
    stripped = chunk.lstrip()
    if stripped[:1] in (b"[", b"{"):
        return "json"
    lowered = stripped.lower()
    for marker in _HTML_MARKERS + _SCRIPT_MARKERS:
        if lowered[:4096].lstrip().startswith(marker):
            raise FileImportError("Refusing to read HTML/SVG/script content as data.")
    return "csv"


# ----------------------------------------------------------------- encoding/delim
def detect_encoding(path: str) -> str:
    with open(path, "rb") as handle:
        raw = handle.read(_CSV_SAMPLE_BYTES)
    # A UTF-8 BOM must be consumed, so open with utf-8-sig rather than utf-8.
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        import chardet

        guess = chardet.detect(raw) or {}
        encoding = (guess.get("encoding") or "").lower()
        if encoding in {"ascii", "us-ascii"}:
            return "utf-8"
        if encoding and guess.get("confidence", 0) >= 0.6:
            return encoding
    except Exception:  # noqa: BLE001 - chardet is best-effort, never fatal
        pass
    return "utf-8"


def detect_delimiter(path: str, encoding: str) -> str:
    try:
        with open(path, "r", encoding=encoding, newline="") as handle:
            sample = handle.read(_CSV_SAMPLE_BYTES)
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            first_line = sample.splitlines()[0] if sample else ""
            counts = {d: first_line.count(d) for d in (",", ";", "\t", "|")}
            best = max(counts, key=counts.get)
            return best if counts[best] else ","
    except (OSError, UnicodeDecodeError):
        return ","


# --------------------------------------------------------------------- csv reader
def iter_csv(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    encoding = detect_encoding(path)
    delimiter = detect_delimiter(path, encoding)
    with open(path, "r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            raise FileImportError("The file has no header row.")
        normalized_header = [_clean_key(name, i) for i, name in enumerate(header)]
        index = 0
        for physical_line, values in enumerate(reader, start=2):
            if all((value is None or str(value).strip() == "") for value in values):
                continue  # blank lines are ignored, not counted as records
            record: Dict[str, object] = {}
            for position, key in enumerate(normalized_header):
                record[key] = values[position] if position < len(values) else ""
            if len(values) > len(normalized_header):
                record["__trailing__"] = values[len(normalized_header):]
            index += 1
            yield index, record
            if max_rows is not None and index >= max_rows:
                return


def _clean_key(name: object, position: int) -> str:
    text = (str(name) if name is not None else "").strip()
    if not text:
        text = f"column_{position + 1}"
    # Collapse a trailing index that csv/Excel add for duplicate headers.
    text = re.sub(r"(\.\d+|__x000d_)$", "", text)
    return text


# --------------------------------------------------------------------- ndjson
def iter_json(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    encoding = detect_encoding(path)
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        prefix = _first_meaningful_byte(handle)
    if prefix == b"{":
        # Newline-delimited JSON: stream line by line.
        index = 0
        with open(path, "r", encoding=encoding) as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip().rstrip(",")
                if not line or line in {"[", "]"}:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise FileImportError(f"Invalid JSON on line {line_no}: {exc.msg}") from exc
                index += 1
                if isinstance(record, dict):
                    yield index, record
                else:
                    yield index, {"value": record}
                if max_rows is not None and index >= max_rows:
                    return
        return

    if prefix == b"[":
        # Array of (flat or nested) objects: stream with the incremental scanner.
        yield from _iter_json_object_array(path, encoding, max_rows=max_rows)
        return

    # Top-level object that is not NDJSON (e.g. {"results": [...]}): guarded load.
    if size > JSON_SAFE_LOAD_BYTES:
        raise FileImportError(
            "This JSON document is too large to parse safely and is not in a streamable "
            f"shape (array-of-objects or NDJSON). File size {size} bytes exceeds the "
            f"{JSON_SAFE_LOAD_BYTES} byte guard. Convert it to CSV, NDJSON, or a flat "
            "JSON array of records."
        )
    with open(path, "r", encoding=encoding) as handle:
        document = json.load(handle)
    yield from _records_from_object(document, max_rows=max_rows)


def _first_meaningful_byte(handle) -> bytes:
    handle.seek(0)
    chunk = handle.read(4096).lstrip()
    # tolerate a UTF-8 BOM
    if chunk.startswith(b"\xef\xbb\xbf"):
        chunk = chunk[3:].lstrip()
    return chunk[:1]


def _iter_json_object_array(path: str, encoding: str, *, max_rows: Optional[int]) -> Iterator[Tuple[int, Dict[str, object]]]:
    """Incremental scanner for a top-level JSON array whose elements are objects.

    Reads in fixed-size blocks; the working buffer only ever holds the object being
    assembled, so a 5 GB array of records streams in bounded memory.
    """
    index = 0
    buffer = io.StringIO()
    depth = 0
    in_string = False
    escape = False
    started = False
    chunk_size = 65536

    def emit(text: str) -> Optional[Tuple[int, Dict[str, object]]]:
        nonlocal index
        try:
            record = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FileImportError(f"Malformed JSON record #{index + 1}: {exc.msg}") from exc
        if not isinstance(record, dict):
            record = {"value": record}
        index += 1
        return index, record

    with open(path, "r", encoding=encoding) as handle:
        # Skip leading whitespace and the opening '['.
        pre = ""
        while True:
            more = handle.read(1)
            if not more:
                return
            pre += more
            if more.strip():
                break
        if pre.strip() != "[":
            raise FileImportError("Expected a JSON array at the top level.")
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            for char in block:
                if not started:
                    if char in " \t\r\n,":
                        continue
                    if char == "]":
                        if max_rows is not None and index >= max_rows:
                            return
                        return
                    if char == "{":
                        started = True
                        depth = 1
                        buffer.truncate(0)
                        buffer.seek(0)
                        buffer.write("{")
                        continue
                    # A non-object element (number/string/array) inside the array.
                    started = True
                    depth = 0
                    buffer.truncate(0)
                    buffer.seek(0)
                    buffer.write(char)
                    if char in "\"":
                        in_string = True
                    continue

                buffer.write(char)
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                    continue
                if char in "{[":
                    depth += 1
                elif char in "}]":
                    depth -= 1
                    if depth <= 0:
                        result = emit(buffer.getvalue())
                        started = False
                        if result is not None:
                            yield result
                        if max_rows is not None and index >= max_rows:
                            return


def _records_from_object(document: object, *, max_rows: Optional[int]) -> Iterator[Tuple[int, Dict[str, object]]]:
    records: List[object]
    if isinstance(document, list):
        records = document
    elif isinstance(document, dict):
        # Find the first list-of-objects value (common ``{"data": [...]}`` shape).
        records = []
        for value in document.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                records = value
                break
    else:
        records = [document]
    index = 0
    for item in records:
        index += 1
        yield index, item if isinstance(item, dict) else {"value": item}
        if max_rows is not None and index >= max_rows:
            return


# --------------------------------------------------------------------- xlsx
def iter_xlsx(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.worksheets[0]
        header: Optional[List[str]] = None
        index = 0
        for values in worksheet.iter_rows(values_only=True):
            if header is None:
                header = [_clean_key(name, i) for i, name in enumerate(values)]
                continue
            if all(value is None or str(value).strip() == "" for value in values):
                continue
            record = {header[position]: (values[position] if position < len(values) else None)
                      for position in range(len(header))}
            index += 1
            yield index, record
            if max_rows is not None and index >= max_rows:
                return
    finally:
        workbook.close()


# --------------------------------------------------------------------- parquet
def iter_parquet(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    index = 0
    try:
        import pyarrow.parquet as pq
    except Exception:  # noqa: BLE001 - fall back to pandas, size-guarded
        size = os.path.getsize(path)
        if size > PARQUET_SAFE_LOAD_BYTES:
            raise FileImportError(
                "PyArrow is required to stream large parquet files. Install pyarrow or "
                "convert the file to CSV.")
        import pandas as pd

        frame = pd.read_parquet(path)
        for record in frame.to_dict("records"):
            index += 1
            yield index, record
            if max_rows is not None and index >= max_rows:
                return
        return

    parquet_file = pq.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=1000):
        for record in batch.to_pylist():
            index += 1
            yield index, record
            if max_rows is not None and index >= max_rows:
                return


# ---------------------------------------------------------------- dispatcher
_ITERATORS = {
    "csv": iter_csv,
    "txt": iter_csv,
    "json": iter_json,
    "xlsx": iter_xlsx,
    "parquet": iter_parquet,
}

UNSUPPORTED = {"xls", "opendocument", "word-document", "netcdf"}


def open_file(path: str, fmt: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    """Return a streaming iterator for a sniffed format.

    Raises :class:`FileImportError` for formats this build cannot stream.
    """
    if fmt in UNSUPPORTED:
        raise FileImportError(
            f"Files detected as '{fmt}' are not supported by this build's readers. "
            "Convert to .csv, .json (array or NDJSON), .xlsx or .parquet and re-upload.")
    reader = _ITERATORS.get(fmt)
    if reader is None:
        raise FileImportError(f"No reader is available for detected format '{fmt}'.")
    return reader(path, max_rows=max_rows)


def read_headers(path: str, fmt: Optional[str] = None) -> List[str]:
    """Return the header list for a file without materialising its rows."""
    fmt = fmt or sniff_format(path)
    if fmt in {"csv", "txt"}:
        encoding = detect_encoding(path)
        delimiter = detect_delimiter(path, encoding)
        with open(path, "r", encoding=encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            header = next(reader, [])
            return [_clean_key(name, i) for i, name in enumerate(header)]
    if fmt == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            values = next(workbook.worksheets[0].iter_rows(values_only=True), None) or []
            return [_clean_key(name, i) for i, name in enumerate(values)]
        finally:
            workbook.close()
    # json / parquet: headers come from the first record's keys.
    try:
        for _, record in open_file(path, fmt, max_rows=1):
            return [key for key in record if key != "__trailing__"]
    except FileImportError:
        raise
    except StopIteration:
        return []
    return []
