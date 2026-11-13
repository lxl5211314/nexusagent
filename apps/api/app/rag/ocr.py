"""OCR fallback for scanned documents.

Two backends are supported because they fail differently:

* MinerU (``mineru-api``, ``POST /file_parse``) returns markdown with tables and
  headings intact — the better default for layout-heavy PDFs.
* PaddleX / PP-StructureV3 serving (``POST /layout-parsing``) returns per-page
  layout blocks; we stitch their markdown back together.

Both clients are tolerant about the envelope: self-hosted builds differ, so we
walk the JSON for the longest markdown-ish string instead of pinning one shape.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)

MARKDOWN_KEYS = ("md_content", "markdown", "md", "content")


class OCRUnavailable(RuntimeError):
    pass


def ocr_enabled() -> bool:
    return get_settings().ocr_backend in {"mineru", "paddle"}


def ocr_parse(filename: str, data: bytes) -> str:
    backend = get_settings().ocr_backend
    if backend == "paddle":
        return _paddle_parse(filename, data)
    return _mineru_parse(filename, data)


def _mineru_parse(filename: str, data: bytes) -> str:
    """POST the document to MinerU and return its markdown."""
    import httpx

    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.mineru_api_key}"} if settings.mineru_api_key else {}
    try:
        response = httpx.post(
            f"{settings.mineru_base_url.rstrip('/')}/file_parse",
            headers=headers,
            files={"files": (filename, data)},
            data={
                "return_md": "true",
                "return_content_list": "false",
                "return_images": "false",
                "parse_method": "auto",
            },
            timeout=settings.mineru_timeout_s,
        )
        response.raise_for_status()
    except Exception as exc:
        raise OCRUnavailable(f"mineru request failed: {exc}") from exc

    markdown = _find_markdown(response.json())
    if not markdown:
        raise OCRUnavailable("mineru returned no markdown field")
    return markdown


def _paddle_parse(filename: str, data: bytes) -> str:
    """POST to a PaddleX serving pipeline and reassemble page markdown."""
    import httpx

    settings = get_settings()
    encoded = base64.b64encode(data).decode("ascii")
    try:
        response = httpx.post(
            f"{settings.paddle_base_url.rstrip('/')}/layout-parsing",
            json={"file": encoded, "fileType": 1, "useDocOrientationClassify": False},
            timeout=settings.paddle_timeout_s,
        )
        response.raise_for_status()
    except Exception as exc:
        raise OCRUnavailable(f"paddle request failed: {exc}") from exc

    payload = response.json()
    pages = _page_markdowns(payload)
    if pages:
        return "\n\n".join(pages)
    fallback = _find_markdown(payload)
    if fallback:
        return fallback
    raise OCRUnavailable("paddle returned no text")


def _page_markdowns(payload: Any) -> list[str]:
    """Pull ``result.layoutParsingResults[*].markdown.text`` when present."""
    pages: list[str] = []
    result = payload.get("result") if isinstance(payload, dict) else None
    blocks = result.get("layoutParsingResults") if isinstance(result, dict) else None
    for block in blocks or []:
        markdown = block.get("markdown") if isinstance(block, dict) else None
        text = markdown.get("text") if isinstance(markdown, dict) else None
        if isinstance(text, str) and text.strip():
            pages.append(text.strip())
    return pages


def _find_markdown(payload: Any) -> str:
    """Depth-first search for the longest markdown-ish string in the payload."""
    found: list[str] = []

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, str(child_key))
        elif isinstance(node, list):
            for child in node:
                walk(child, key)
        elif isinstance(node, str) and key in MARKDOWN_KEYS and len(node.strip()) > 20:
            found.append(node)

    walk(payload)
    return max(found, key=len) if found else ""
