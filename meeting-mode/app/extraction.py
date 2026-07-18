"""Extracts text/rows from client files, tagging every piece with a source
reference (filename + sheet/page) so ForgeSpec generation can trace a
number back to where it came from — required for conflict detection later
(PRD §5 step 5: "preserve source references... so conflicting values can
be identified")."""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from pypdf import PdfReader

log = logging.getLogger("meeting-mode")


class ExtractionError(Exception):
    def __init__(self, filename: str, message: str):
        self.filename = filename
        self.message = message
        super().__init__(f"{filename}: {message}")


@dataclass
class ExtractedExcel:
    filename: str
    sheets: dict[str, list[dict]] = field(default_factory=dict)  # sheet -> rows (header-keyed)


@dataclass
class ExtractedPdf:
    filename: str
    pages: list[str] = field(default_factory=list)
    full_text: str = ""


def extract_excel(path: Path, original_filename: str) -> ExtractedExcel:
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise ExtractionError(original_filename, f"could not open as Excel: {exc}") from exc

    result = ExtractedExcel(filename=original_filename)
    try:
        for ws in wb.worksheets:
            rows_iter = ws.iter_rows(values_only=True)
            try:
                header = next(rows_iter)
            except StopIteration:
                result.sheets[ws.title] = []
                continue
            header = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header)]
            rows = []
            for raw_row in rows_iter:
                if raw_row is None or all(v is None for v in raw_row):
                    continue
                rows.append({header[i]: raw_row[i] for i in range(min(len(header), len(raw_row)))})
            result.sheets[ws.title] = rows
    finally:
        wb.close()

    if not any(result.sheets.values()):
        raise ExtractionError(original_filename, "Excel file has no data rows in any sheet")

    return result


def extract_pdf(path: Path, original_filename: str) -> ExtractedPdf:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ExtractionError(original_filename, f"could not open as PDF: {exc}") from exc

    if reader.is_encrypted:
        raise ExtractionError(original_filename, "PDF is password-protected")

    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:
            log.warning("pdf=%s page=%d text extraction failed: %s", original_filename, i, exc)
            pages.append("")

    full_text = "\n".join(pages).strip()
    if not full_text:
        raise ExtractionError(original_filename, "no extractable text (likely a scanned/image-only PDF)")

    return ExtractedPdf(filename=original_filename, pages=pages, full_text=full_text)


def summarize_excel(extracted: ExtractedExcel, max_rows_per_sheet: int = 200, max_chars: int = 12000) -> str:
    """Bounded, LLM-prompt-friendly rendering. Keeps the source filename +
    sheet name attached to every row block so a value can be traced back."""
    lines = [f"file: {extracted.filename}"]
    for sheet, rows in extracted.sheets.items():
        lines.append(f"  sheet: {sheet} ({len(rows)} row(s))")
        for row in rows[:max_rows_per_sheet]:
            lines.append(f"    {row}")
        if len(rows) > max_rows_per_sheet:
            lines.append(f"    ... {len(rows) - max_rows_per_sheet} more row(s) truncated")
    text = "\n".join(lines)
    return text[:max_chars] + ("\n... truncated" if len(text) > max_chars else "")


def summarize_pdf(extracted: ExtractedPdf, max_chars: int = 8000) -> str:
    text = f"file: {extracted.filename}\n{extracted.full_text}"
    return text[:max_chars] + ("\n... truncated" if len(text) > max_chars else "")
