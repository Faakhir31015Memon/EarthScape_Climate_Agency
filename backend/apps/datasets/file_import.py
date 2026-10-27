"""Streaming readers for uploaded climate files.

Each reader yields ``(row_index, record)`` pairs where ``row_index`` is the 1-based
data-record ordinal (the header row is not counted) and records are dicts keyed by
the raw source header. Callers must sniff the content first; the declared extension
is not trusted and no formula engine, external link or macro is ever run.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import zipfile
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

# 32 MiB: well above any real upload but keeps peak memory bounded for non-streamable JSON.
JSON_SAFE_LOAD_BYTES = 32 * 1024 * 1024
PARQUET_SAFE_LOAD_BYTES = 512 * 1024 * 1024
# xlrd and decryption hold the whole file in memory, hence the cap.
OLE_SAFE_LOAD_BYTES = 256 * 1024 * 1024

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
    """Return the content format of a file, ignoring its extension.

    Raises FileImportError for executables, active content, unknown formats, and when
    a supplied declared_extension conflicts with the sniffed content.
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
        detected = "encrypted" if _is_encrypted_ole(path) else "xls"
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
    if extension in {"exe", "dll", "sh", "bat", "cmd", "ps1", "py", "html", "htm",
                     "svg", "js", "com", "scr", "msi", "jar"}:
        return f"Refusing to accept a '{extension}' file, which is not a data format."
    expected = {
        "csv": {"csv", "txt"},
        "txt": {"csv", "txt"},
        "tsv": {"csv", "txt"},
        "json": {"json"},
        "jsonl": {"json"},
        "xlsx": {"xlsx", "xlsb", "encrypted"},
        "xlsm": {"xlsm", "xlsx", "xlsb", "encrypted"},
        "xltx": {"xltx", "xlsx", "encrypted"},
        "xltm": {"xltm", "xltx", "xlsm", "xlsx", "encrypted"},
        "xls": {"xls", "encrypted"},
        "xlsb": {"xlsb", "encrypted"},
        "ods": {"ods"},
        "parquet": {"parquet"},
        "nc": {"netcdf"},
    }.get(extension, {extension})
    if detected in expected:
        return None
    # A password-protected workbook reports its real subtype only once opened.
    if detected == "encrypted":
        return None
    return (f"The declared extension '.{extension}' does not match the file content "
            f"(detected '{detected}').")


def _sniff_zip_package(path: str) -> str:
    """Distinguish a real OOXML spreadsheet / ODS from a plain zip masquerade."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            content_types = ""
            if "xl/workbook.xml" not in names and not any(n.startswith("xl/") for n in names):
                if "mimetype" in names:
                    return _sniff_opendocument(archive.read("mimetype"))
                if any(n.endswith(".docx") or n == "word/document.xml" for n in names):
                    return "word-document"
                raise FileImportError("A zip archive was supplied that is not a supported spreadsheet.")
            if "xl/workbook.bin" in names:
                return "xlsb"
            if "[Content_Types].xml" in names:
                content_types = archive.read("[Content_Types].xml").decode("utf-8", "replace").lower()
    except zipfile.BadZipFile as exc:
        raise FileImportError("The archive could not be opened as a spreadsheet.") from exc
    return _classify_ooxml_spreadsheet(names, content_types)


def _classify_ooxml_spreadsheet(names: set, content_types: str) -> str:
    """Split the OOXML spreadsheet subtypes, which share one container layout."""
    if "xl/encryptedPayload.xml" in names or not content_types:
        # A truly encrypted workbook is an OLE container, so a zip with only an
        # encrypted payload counts as protected content.
        return "encrypted"
    macro = any(n.endswith("vbaProject.bin") for n in names)
    template = ("template.main+xml" in content_types
                or "template.macroenabled.main+xml" in content_types)
    if macro:
        # A macro stream makes it a macro flavour; only a template part makes it a .xltm.
        return "xltm" if template else "xlsm"
    if template:
        return "xltx"
    return "xlsx"


def _sniff_opendocument(mimetype_bytes: bytes) -> str:
    mimetype = mimetype_bytes.decode("utf-8", "replace").strip()
    if mimetype == "application/vnd.oasis.opendocument.spreadsheet":
        return "ods"
    if mimetype == "application/vnd.oasis.opendocument.text":
        return "word-document"
    return "opendocument"


def _is_encrypted_ole(path: str) -> bool:
    """True for an Office Rocket Book container (password-protected workbook)."""
    try:
        with open(path, "rb") as handle:
            blob = handle.read(65536)
    except OSError:
        return False
    # OLE stream names are stored UTF-16LE, so match both byte encodings.
    for marker in (b"EncryptedPackage", b"encryptedPayload"):
        if marker in blob or marker.decode().encode("utf-16-le") in blob:
            return True
    return False


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
    # utf-8-sig so the BOM is consumed, not left in the first field.
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
    """Scan a top-level JSON array of objects in fixed blocks; the buffer only ever
    holds the object being assembled, so huge arrays stream in bounded memory.
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
        # First list value, matching the common {"data": [...]} shape.
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


@contextmanager
def _ooxml_source(path: str) -> Iterator[str]:
    """Yield a readable OOXML path, transparently decrypting protected workbooks."""
    if _looks_like_encrypted_zip(path):
        temp = _decrypt_to_temp(path)
        try:
            yield temp
        finally:
            _unlink_quietly(temp)
        return
    yield path


def _looks_like_encrypted_zip(path: str) -> bool:
    """Agile-encrypted OOXML bodies stay a zip but hide every part in ``encData``."""
    found_xl_part = False
    encrypted = False
    try:
        with zipfile.ZipFile(path) as archive:
            if "xl/workbook.xml" in archive.namelist():
                return False
            for entry in archive.infolist():
                if not entry.filename.startswith("xl/") or entry.is_dir():
                    continue
                found_xl_part = True
                with archive.open(entry) as part:
                    body = part.read(8192)
                if b"encData" in body:
                    encrypted = True
                    break
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return False
    return found_xl_part and encrypted


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------- xlsx
def iter_xlsx(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    # Eager (read_only=False) but keep_vba off: cell values only, so openpyxl never
    # holds a second copy of the zip in memory.
    from openpyxl import load_workbook

    with _ooxml_source(path) as readable:
        workbook = load_workbook(readable, data_only=True)
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


# ------------------------------------------------------------- password-protected
def _spreadsheet_password() -> Optional[str]:
    """Password used to open workbook-encrypted uploads (``UPLOAD_FILE_PASSWORD``)."""
    try:
        from config import env

        return env.get_raw("UPLOAD_FILE_PASSWORD") or None
    except Exception:  # noqa: BLE001 - configuration lookup must never crash an import
        return os.environ.get("UPLOAD_FILE_PASSWORD") or None


def _decrypt_to_temp(path: str) -> str:
    """Write a password-protected workbook out to a temp file and return its path."""
    try:
        import msoffcrypto
    except ImportError as exc:
        raise FileImportError(
            "This workbook is encrypted. Install msoffcrypto-tool and set "
            "UPLOAD_FILE_PASSWORD to import it.") from exc

    password = _spreadsheet_password()
    if not password:
        raise FileImportError(
            "This workbook is password-protected. Set UPLOAD_FILE_PASSWORD in .env to "
            "allow encrypted files to be imported.")
    if os.path.getsize(path) > OLE_SAFE_LOAD_BYTES:
        raise FileImportError(
            f"An encrypted workbook must be fully decrypted before reading; the file is "
            f"larger than the {OLE_SAFE_LOAD_BYTES} byte guard.")

    handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    handle.close()
    decrypted = handle.name
    try:
        with open(path, "rb") as source, open(decrypted, "wb") as sink:
            office_file = msoffcrypto.OfficeFile(source)
            office_file.load_key(password=password)
            office_file.decrypt(sink)
    except FileImportError:
        os.unlink(decrypted)
        raise
    except Exception as exc:  # noqa: BLE001 - every crypto failure is a user-facing error
        os.unlink(decrypted)
        message = str(exc).strip() or type(exc).__name__
        raise FileImportError(
            f"The workbook could not be decrypted with UPLOAD_FILE_PASSWORD ({message}).") from exc
    return decrypted


def _classify_decrypted(decrypted_path: str) -> str:
    """Reader chosen after decryption, since the container hides the real subtype."""
    try:
        return sniff_format(decrypted_path)
    except FileImportError:
        return "xlsx"


# --------------------------------------------------------------------- xlsm / templates
def iter_xlsm(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    """Macro/template workbook through the OOXML reader; the macro stream is never run."""
    return iter_xlsx(path, max_rows=max_rows)


# --------------------------------------------------------------------- ods
def _ods_repeat_cap() -> int:
    """odfpy expands ``number-columns-repeated``; LibreOffice pads rows to 16 384."""
    try:
        from config import env

        return max(1, env.get_int("ODS_MAX_COLUMN_REPEAT", 4096))
    except Exception:  # noqa: BLE001 - config is unavailable in bare-pipeline contexts
        return 4096


def _ods_cell_text(cell) -> str:
    """Concatenate the leaf text nodes of an ODF cell (``text:p`` wraps ``text``)."""
    parts: List[str] = []

    def walk(node) -> None:
        data = getattr(node, "data", None)
        if isinstance(data, str):
            parts.append(data)
        for child in getattr(node, "childNodes", None) or []:
            walk(child)

    walk(cell)
    return "".join(parts)


def _ods_row_values(row, TableCell, CoveredTableCell, repeat_cap: int) -> List[object]:
    """Flatten one ODF table row into a positional value list, honouring repeats."""
    from odf.namespaces import TABLENS

    values: List[object] = []
    for cell in row.getElementsByType(TableCell) + row.getElementsByType(CoveredTableCell):
        text = _ods_cell_text(cell)
        try:
            span = int(cell.attributes.get((TABLENS, "number-columns-repeated")) or 1)
        except (TypeError, ValueError):
            span = 1
        span = max(1, min(span, repeat_cap))
        values.extend([text] * span)
    while values and (values[-1] is None or str(values[-1]).strip() == ""):
        values.pop()
    return values


def iter_ods(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    index = 0
    header: Optional[List[str]] = None
    try:
        from odf.opendocument import load
        from odf.table import Table, TableCell, CoveredTableCell, TableRow
    except ImportError as exc:
        raise FileImportError(
            "OpenDocument (.ods) support needs the odfpy package. Install odfpy or "
            "convert the file to .csv or .xlsx and re-upload.") from exc

    document = load(path)
    repeat_cap = _ods_repeat_cap()
    for sheet in document.getElementsByType(Table):
        for row in sheet.getElementsByType(TableRow):
            values = _ods_row_values(row, TableCell, CoveredTableCell, repeat_cap)
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


# --------------------------------------------------------------------- xls (BIFF)
def iter_xls(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    if os.path.getsize(path) > OLE_SAFE_LOAD_BYTES:
        raise FileImportError(
            f"Legacy .xls workbooks are loaded fully into memory and are capped at "
            f"{OLE_SAFE_LOAD_BYTES} bytes. Convert the file to .csv or .xlsx and re-upload.")
    try:
        import xlrd
    except ImportError as exc:
        raise FileImportError(
            "Legacy .xls support needs the xlrd package. Install xlrd or convert the "
            "file to .csv or .xlsx and re-upload.") from exc

    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    header: Optional[List[str]] = None
    index = 0
    for rowno in range(sheet.nrows):
        values = [sheet.cell_value(rowno, colno) for colno in range(sheet.ncols)]
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


# --------------------------------------------------------------------- xlsb
def iter_xlsb(path: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    try:
        from pyxlsb import open_workbook
    except ImportError as exc:
        raise FileImportError(
            "Excel binary (.xlsb) support needs the pyxlsb package. Install pyxlsb or "
            "convert the file to .csv or .xlsx and re-upload.") from exc

    index = 0
    with open_workbook(path) as workbook:
        with workbook.get_sheet(1) as sheet:
            header: Optional[List[str]] = None
            for values in sheet.rows():
                row = [cell.v if getattr(cell, "v", None) is not None else "" for cell in values]
                if header is None:
                    header = [_clean_key(name, i) for i, name in enumerate(row)]
                    continue
                if all(value is None or str(value).strip() == "" for value in row):
                    continue
                record = {header[position]: (row[position] if position < len(row) else None)
                          for position in range(len(header))}
                index += 1
                yield index, record
                if max_rows is not None and index >= max_rows:
                    return


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
    "xlsm": iter_xlsx,
    "xltx": iter_xlsx,
    "xltm": iter_xlsx,
    "xls": iter_xls,
    "xlsb": iter_xlsb,
    "ods": iter_ods,
    "parquet": iter_parquet,
}

UNSUPPORTED = {"word-document", "opendocument", "netcdf"}


def _iter_encrypted(path: str, *, max_rows: Optional[int] = None
                    ) -> Iterator[Tuple[int, Dict[str, object]]]:
    """Decrypt a protected workbook, then stream whichever format it turns out to be."""
    temp = _decrypt_to_temp(path)
    try:
        inner = _classify_decrypted(temp)
        reader = _ITERATORS.get(inner, iter_xlsx)
        yield from reader(temp, max_rows=max_rows)
    finally:
        _unlink_quietly(temp)


_ITERATORS["encrypted"] = _iter_encrypted


def open_file(path: str, fmt: str, *, max_rows: Optional[int] = None) -> Iterator[Tuple[int, Dict[str, object]]]:
    """Return a streaming iterator for a sniffed format, or raise FileImportError."""
    if fmt in UNSUPPORTED:
        raise FileImportError(
            f"Files detected as '{fmt}' are not supported by this build's readers. "
            "Convert to .csv, .json (array or NDJSON), an Excel/LibreOffice spreadsheet "
            "or .parquet and re-upload.")
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
    if fmt in {"xlsx", "xlsm", "xltx", "xltm"}:
        from openpyxl import load_workbook

        with _ooxml_source(path) as readable:
            workbook = load_workbook(readable, data_only=True)
        try:
            values = next(workbook.worksheets[0].iter_rows(values_only=True), None) or []
            return [_clean_key(name, i) for i, name in enumerate(values)]
        finally:
            workbook.close()
    # json / parquet / xls / xlsb / ods / encrypted: headers come from the first record.
    try:
        for _, record in open_file(path, fmt, max_rows=1):
            return [key for key in record if key != "__trailing__"]
    except FileImportError:
        raise
    except StopIteration:
        return []
    return []
