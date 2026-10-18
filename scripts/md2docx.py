"""Convert the project's Markdown documentation into a single .docx deliverable.

Usage:
    .venv/Scripts/python.exe scripts/md2docx.py OUT.docx IN.md [IN2.md ...]

Files are concatenated in the order given, each starting on a new page. Supported
Markdown: ATX headings (# .. ######), fenced code blocks, unordered and ordered
lists (one nesting level), GFM tables, blockquotes, horizontal rules, and inline
**bold**, *italic*, `code`, [text](url). Images are referenced by alt text because
the submission report is a text document.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

HEADING_SIZES = {1: 20, 2: 16, 3: 13, 4: 12, 5: 11, 6: 10}
CODE_FONT = "Consolas"
INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`|\[[^\]]+\]\([^)]+\))")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
DIVIDER = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def add_inline(paragraph, text: str, *, bold: bool = False, italic: bool = False,
               code: bool = False) -> None:
    """Render inline Markdown as runs, recursing into a span's content so that
    ``**bold `code`**`` or ``[`file.md`](url)`` keep their formatting instead of
    publishing the markers."""
    for part in INLINE.split(text.replace("\\|", "|")):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            add_inline(paragraph, part[2:-2], bold=True, italic=italic, code=code)
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            add_inline(paragraph, part[1:-1], bold=bold, italic=True, code=code)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            add_inline(paragraph, part[1:-1], bold=bold, italic=italic, code=True)
        elif part.startswith("["):
            match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            if not match:
                paragraph.add_run(part)
                continue
            start = len(paragraph.runs)
            add_inline(paragraph, match.group(1), bold=bold, italic=italic, code=code)
            for run in paragraph.runs[start:]:
                run.font.color.rgb = RGBColor(0x1D, 0x63, 0xD8)
                run.underline = True
            if match.group(2).startswith("http"):
                paragraph.add_run(f" ({match.group(2)})").font.size = Pt(8.5)
        else:
            run = paragraph.add_run(part)
            if bold:
                run.bold = True
            if italic:
                run.italic = True
            if code:
                run.font.name = CODE_FONT
                run.font.size = Pt(9.5)


def flush_table(document, rows: list[str]) -> None:
    # Split on real column separators only: `\|` inside a cell is an escaped
    # pipe, and splitting there would break the code spans around it.
    cells = [[c.strip().replace("\x00", "|")
              for c in r.strip().strip("|").replace("\\|", "\x00").split("|")]
             for r in rows]
    cells = [row for row in cells if not DIVIDER.match("|".join(row))]
    if not cells:
        return
    width = max(len(row) for row in cells)
    table = document.add_table(rows=len(cells), cols=width)
    table.style = "Light Grid Accent 1"
    for y, row in enumerate(cells):
        for x in range(width):
            text = row[x] if x < len(row) else ""
            cell = table.cell(y, x)
            cell.text = ""
            add_inline(cell.paragraphs[0], text)
            if y == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True
    document.add_paragraph()


def convert(document, text: str) -> None:
    lines = text.splitlines()
    table_rows: list[str] = []
    prose: list[str] = []
    pending: tuple[object, list[str]] | None = None
    code: list[str] | None = None
    index = 0

    def flush_prose():
        if prose:
            paragraph = document.add_paragraph()
            add_inline(paragraph, " ".join(prose))
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            for run in paragraph.runs:
                run.font.size = Pt(10.5)
            prose.clear()

    def flush_pending():
        # A list item and its hard-wrapped continuation lines render as one
        # inline pass, otherwise `**bold**` split across a source line break
        # would survive into the document as asterisks.
        nonlocal pending
        if pending:
            paragraph, chunks = pending
            add_inline(paragraph, " ".join(chunks))
            for run in paragraph.runs:
                run.font.size = Pt(10.5)
            pending = None

    def flush_all():
        flush_pending()
        flush_prose()

    def flush_table_rows():
        if table_rows:
            flush_all()
            flush_table(document, table_rows)
            table_rows.clear()

    while index < len(lines):
        line = lines[index]
        index += 1

        if code is not None:
            if line.strip().startswith("```"):
                flush_all()
                paragraph = document.add_paragraph()
                run = paragraph.add_run("\n".join(code))
                run.font.name = CODE_FONT
                run.font.size = Pt(8.5)
                paragraph.paragraph_format.left_indent = Pt(12)
                paragraph.paragraph_format.space_after = Pt(8)
                code = None
            else:
                code.append(line)
            continue

        if line.strip().startswith("```"):
            flush_all()
            flush_table_rows()
            code = []
            continue

        if TABLE_ROW.match(line):
            flush_all()
            table_rows.append(line)
            continue
        flush_table_rows()

        stripped = line.strip()
        if not stripped:
            flush_all()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_all()
            level = len(heading.group(1))
            paragraph = document.add_heading("", level=min(level, 6))
            paragraph.text = ""
            add_inline(paragraph, heading.group(2))
            for run in paragraph.runs:
                run.font.size = Pt(HEADING_SIZES[level])
                run.font.color.rgb = RGBColor(0x16, 0x28, 0x1C)
            continue

        if re.match(r"^(---+|\*\*\*+|===+)\s*$", stripped):
            flush_all()
            rule = document.add_paragraph()
            rule.paragraph_format.space_before = Pt(6)
            rule.paragraph_format.space_after = Pt(6)
            rule.add_run("— " * 1).font.color.rgb = RGBColor(0xBB, 0xBB, 0xBB)
            continue

        if stripped.startswith(">"):
            flush_all()
            paragraph = document.add_paragraph()
            add_inline(paragraph, stripped.lstrip("> ").strip())
            for run in paragraph.runs:
                run.italic = True
                run.font.size = Pt(10)
            paragraph.paragraph_format.left_indent = Pt(18)
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        ordered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        nested = re.match(r"^\s+[-*+]\s+(.*)$", line)
        if bullet or ordered or nested:
            flush_all()
            if nested:
                paragraph = document.add_paragraph(style="List Bullet 2")
                content = nested.group(1)
            elif bullet:
                paragraph = document.add_paragraph(style="List Bullet")
                content = bullet.group(1)
            else:
                paragraph = document.add_paragraph(style="List Number")
                content = ordered.group(2)
            pending = (paragraph, [content])
            continue

        if pending is not None:
            # A hard-wrapped continuation of the list item above.
            pending[1].append(stripped)
            continue

        # Hard-wrapped prose: buffer until the paragraph ends.
        prose.append(stripped)

    flush_all()
    flush_table_rows()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2

    output = Path(argv[1])
    document = Document()
    document.styles["Normal"].font.name = "Calibri"
    document.styles["Normal"].font.size = Pt(10.5)

    for position, source in enumerate(argv[2:]):
        path = Path(source)
        if not path.is_file():
            print(f"missing input: {path}", file=sys.stderr)
            return 1
        if position:
            document.add_page_break()
        convert(document, path.read_text(encoding="utf-8"))

    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    print(f"wrote {output} ({output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
