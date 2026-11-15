"""Delete the demo KB from every backend so live_check starts clean."""

from app.db.base import session_scope
from app.db.models import Chunk, Document, KnowledgeBase
from app.rag.stores import get_graph_store, get_vector_store

KB = "live-kb"

with session_scope() as session:
    session.query(Chunk).filter(Chunk.kb_id == KB).delete()
    session.query(Document).filter(Document.kb_id == KB).delete()
    session.query(KnowledgeBase).filter(KnowledgeBase.id == KB).delete()

get_vector_store().delete_kb(KB)
get_graph_store().delete_kb(KB)
print("cleared", KB)
