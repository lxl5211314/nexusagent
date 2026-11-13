"""OCR fallback for scanned documents.

MinerU (``mineru-api``) exposes ``POST /file_parse`` and returns markdown, which
keeps tables and headings in a shape the chunker can use — far better than raw
text lines. The client is deliberately tolerant about the response envelope:
self-hosted MinerU versions differ, so we walk the JSON for a markdown field.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)

MARKDOWN_KEYS = ("md_content", "markdown", "md", "content")


class OCRUnavailable(RuntimeError):
    pass


def ocr_enabled() -> bool:
    return get_settings().ocr_backend == "mineru"


def ocr_parse(filename: str, data: bytes) -> str:
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
