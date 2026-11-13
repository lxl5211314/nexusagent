"""Raw document storage.

Chunks are derived data; the uploaded bytes are the thing a user asks for when
they click a citation. Keeping them also means a re-index never needs the
browser to hold the file.
"""

from __future__ import annotations

from typing import Protocol


class ObjectStore(Protocol):
    backend: str

    def put(self, key: str, data: bytes, content_type: str = "") -> str: ...

    def get(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def health(self) -> bool: ...
