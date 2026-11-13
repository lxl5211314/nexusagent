from __future__ import annotations

import json

from app.db.base import init_db, session_scope
from app.db.models import KnowledgeBase
from app.rag.ingest import DuplicateDocument, ingest_document


def seed_sample(kb_name: str = "NexusAgent 示例库", sample_dir: str | None = None) -> str:
    """Create (or reuse) a sample KB and ingest bundled sample docs. Returns kb_id."""
    from pathlib import Path

    # app/seed.py -> parents[0]=app, parents[1]=apps/api (where data/ lives)
    root = Path(__file__).resolve().parents[1]
    sample_dir = sample_dir or str(root / "data" / "samples")
    # Running `python -m app.seed` on a fresh checkout must work by itself,
    # without starting the server first just to create the tables.
    init_db()
    with session_scope() as session:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.name == kb_name).first()
        if kb is None:
            kb = KnowledgeBase(name=kb_name, description="内置示例文档（离线演示用）")
            session.add(kb)
            session.flush()
        kb_id = kb.id

    for path in sorted(Path(sample_dir).glob("*.md")):
        with session_scope() as session:
            kb = session.get(KnowledgeBase, kb_id)
            if kb is None:
                break
            try:
                ingest_document(session, kb_id, path.name, path.read_bytes(), "text/markdown")
            except DuplicateDocument:
                continue  # already seeded by a previous test — idempotent
    return kb_id


if __name__ == "__main__":
    kb_id = seed_sample()
    print(json.dumps({"kb_id": kb_id, "seeded": True}))
