"""Live cleanup check: ingest -> external stores populated -> delete -> empty."""

from app.db.base import init_db, session_scope
from app.db.models import KnowledgeBase
from app.rag.ingest import ingest_document
from app.rag.retriever import retrieve
from app.rag.stores import get_graph_store, get_vector_store

KB = "delete-kb"
DOC = (
    "# 检索\nRRF 融合 k=60 weights 1.0 1.0 0.5\n"
    "## 幻觉\n答案必须绑定引用块，越界判无效。\n"
)

init_db()
with session_scope() as s:
    s.add(KnowledgeBase(id=KB, name="k", description="", tenant_id="local"))
    s.flush()
    ingest_document(s, KB, "a.md", DOC.encode("utf-8"))

vs, gs = get_vector_store(), get_graph_store()
print("after ingest  vector=", vs.stats(KB).chunks, " graph=", gs.stats(KB))
r = retrieve(KB, "RRF k 值", top_k=3)
print("hits", len(r.items), [h.chunk_id[:6] for h in r.items])
vs.delete_kb(KB)
gs.delete_kb(KB)
print("after delete  vector=", vs.stats(KB).chunks, " graph=", gs.stats(KB))
assert vs.stats(KB).chunks == 0 and gs.stats(KB).chunks == 0
print("DELETE_CLEANUP_OK")
