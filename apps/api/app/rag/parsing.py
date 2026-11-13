"""Document parsing.

The ``Parser`` protocol is the seam MinerU / PaddleOCR plug into; the router
below picks a parser by extension+size, so adding an OCR path does not touch
the chunking or embedding stages.
"""

from __future__ import annotations

import io
from pathlib import Path

from app.rag import ocr


class UnsupportedFormat(ValueError):
    pass


class ParseError(RuntimeError):
    pass


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def parse(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml", ".log"}:
        return _decode(data)
    if suffix == ".pdf":
        return _parse_pdf(data, filename)
    if suffix == ".docx":
        return _parse_docx(data)
    if suffix in IMAGE_SUFFIXES:
        return _parse_scanned(filename, data)
    raise UnsupportedFormat(
        f"unsupported format: {suffix or '(none)'} (supported: md, txt, pdf, docx, png, jpg)"
    )


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_pdf(data: bytes, filename: str = "document.pdf") -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ParseError("pypdf is not installed") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise ParseError(f"pdf parse failed: {exc}") from exc

    text = "\n\n".join(page for page in pages if page)
    if not text:
        # Scanned PDF: hand it to OCR when configured, otherwise say why.
        return _parse_scanned(filename, data, hint="pdf has no extractable text")
    return text


def _parse_scanned(filename: str, data: bytes, hint: str = "scanned document") -> str:
    if not ocr.ocr_enabled():
        raise ParseError(
            f"{hint} - enable OCR with NEXUS_OCR_BACKEND=mineru or paddle"
        )
    try:
        return ocr.ocr_parse(filename, data)
    except ocr.OCRUnavailable as exc:
        raise ParseError(f"{hint} - OCR failed: {exc}") from exc


def _parse_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise ParseError("python-docx is not installed") from exc

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"docx parse failed: {exc}") from exc

    blocks = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)
