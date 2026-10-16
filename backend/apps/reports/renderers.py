"""Report renderers: CSV (stdlib), PDF (reportlab) and JSON.

Every renderer receives the same payload produced by :mod:`apps.reports.services`,
which is built exclusively from MongoDB aggregations. A section that has no rows is
rendered as an explicit "no data" block with the recorded reason - placeholder numbers
are never generated.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

MAX_PDF_ROWS = 150


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (list, tuple)):
        return "; ".join(_fmt(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}={_fmt(item)}" for key, item in value.items())
    return str(value)


def _metadata_lines(payload: dict[str, Any]) -> list[list[str]]:
    date_range = payload.get("date_range") or {}
    units = payload.get("units") or {}
    filters = payload.get("filters") or {}
    lines = [
        ["Report", payload.get("title") or payload.get("report_type", "")],
        ["Report type", payload.get("report_type", "")],
        ["Status", payload.get("status", "")],
        ["Generated at (UTC)", _fmt(payload.get("generated_at"))],
        ["Data source", payload.get("data_source", "")],
        ["Date range", _fmt(date_range) or "n/a"],
        ["Units", _fmt(units) or "n/a"],
        ["Requested by", payload.get("requested_by_label", "")],
        ["Filters applied", _fmt(filters) or "none"],
        ["Aggregation limits", _fmt(payload.get("limits")) or "n/a"],
    ]
    if payload.get("empty"):
        lines.append(["Empty", f"yes - {payload.get('reason') or 'no underlying data'}"])
    return lines


def render_csv(payload: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["# EarthScape Climate Agency report"])
    for row in _metadata_lines(payload):
        writer.writerow(["# " + row[0], row[1]])
    writer.writerow([])
    for section in payload.get("sections", []):
        writer.writerow([f"# SECTION: {section.get('title')}"])
        if section.get("notes"):
            for note in section["notes"]:
                writer.writerow([f"# note: {note}"])
        if not section.get("rows"):
            writer.writerow([f"# no data: {section.get('reason') or payload.get('reason') or 'empty result'}"])
            writer.writerow([])
            continue
        columns = section.get("columns") or []
        writer.writerow([column.get("label", column.get("key")) for column in columns])
        for row in section["rows"]:
            writer.writerow([_fmt(row.get(column["key"])) for column in columns])
        if section.get("truncated"):
            writer.writerow([f"# truncated at {section.get('row_limit')} rows"])
        writer.writerow([])
    return buffer.getvalue()


def render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=_fmt, indent=2, ensure_ascii=False)


def _pdf_payload(payload: dict[str, Any], buffer) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (LongTable, Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    styles = getSampleStyleSheet()
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=8, leading=10)
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8, leading=10,
                                textColor=colors.HexColor("#4a5568"))
    heading_style = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=11,
                                   leading=14, spaceBefore=10, spaceAfter=4)

    document = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), title=str(payload.get("title"))[:120],
        author="EarthScape Climate Agency", subject=str(payload.get("report_type")),
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )
    story: list[Any] = [
        Paragraph("EarthScape Climate Agency", styles["Title"]),
        Paragraph(_fmt(payload.get("title") or payload.get("report_type")), styles["Heading1"]),
        Spacer(1, 6),
    ]

    meta_rows = [[Paragraph(f"<b>{key}</b>", meta_style), Paragraph(_fmt(value), meta_style)]
                 for key, value in _metadata_lines(payload)]
    meta_table = Table(meta_rows, colWidths=[45 * mm, 205 * mm])
    meta_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f7fafc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_table)

    for section in payload.get("sections", []):
        story.append(Paragraph(_fmt(section.get("title")), heading_style))
        for note in section.get("notes") or []:
            story.append(Paragraph(_fmt(note), note_style))
        rows = section.get("rows") or []
        if not rows:
            story.append(Paragraph(
                f"<i>No data for this section: {_fmt(section.get('reason') or payload.get('reason'))}</i>",
                note_style))
            continue
        columns = section.get("columns") or []
        header = [Paragraph(f"<b>{_fmt(column.get('label', column['key']))}</b>", meta_style)
                  for column in columns]
        body = [[Paragraph(_fmt(row.get(column["key"])), meta_style) for column in columns]
                for row in rows[:MAX_PDF_ROWS]]
        table = LongTable([header, *body], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        story.append(table)
        if len(rows) > MAX_PDF_ROWS:
            story.append(Paragraph(
                f"<i>Table truncated in the PDF after {MAX_PDF_ROWS} rows; the CSV export "
                f"carries all {len(rows)} aggregated rows.</i>", note_style))

    metadata = payload.get("model_metadata") or []
    if metadata:
        story.append(Paragraph("Model metadata", heading_style))
        for entry in metadata:
            story.append(Paragraph(_fmt(entry), meta_style))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Generated {_fmt(payload.get('generated_at'))} by EarthScape Climate Agency. "
        "All figures are aggregated from stored MongoDB documents for the filters shown above.",
        note_style))
    document.build(story)


def render_pdf(payload: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    _pdf_payload(payload, buffer)
    return buffer.getvalue()


RENDERERS = {"csv": render_csv, "pdf": render_pdf, "json": render_json}
