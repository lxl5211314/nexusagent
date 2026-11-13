"""Live integration check against real Milvus (Lite) and real Neo4j (Bolt).

Run with both backends enabled:

    NEXUS_VECTOR_BACKEND=milvus NEXUS_MILVUS_URI=<abs path>.db \
    NEXUS_GRAPH_BACKEND=neo4j NEXUS_NEO4J_URI=bolt://127.0.0.1:7687 \
    python -m scripts.live_check

It exercises the same code the API uses: ingest -> Milvus upsert + Neo4j rebuild
-> hybrid retrieve -> agent chat, and prints what each backend actually holds.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.agent.graph import build_graph
from app.core.config import get_settings
from app.db.base import init_db, session_scope
from app.db.models import Chunk, KnowledgeBase
from app.rag.ingest import ingest_document
from app.rag.retriever import retrieve
from app.rag.stores import get_graph_store, get_vector_store

KB = "live-kb"
DOC = """# NexusAgent 检索说明

混合检索由三路召回组成：向量检索、BM25 关键词检索、知识图谱一跳扩展。
三路结果用 RRF 融合，k 取 60，权重分别是 1.0、1.0、0.5。
RRF 的公式是 sum(weight / (k + rank))，只依赖排名不依赖分数，避免量纲不一致。

## 幻觉抑制

答案必须绑定引用块，引用编号越界会被判为无效并触发重写。
低置信度时直接拒答，而不是编造一个看似合理的答案。
"""

QUERY = "RRF 融合的 k 值和权重是多少？"


def main() -> int:
    settings = get_settings()
    print(f"vector={settings.vector_backend} graph={settings.graph_backend}")
    init_db()

    with session_scope() as session:
        if session.get(KnowledgeBase, KB) is None:
            session.add(KnowledgeBase(id=KB, name="live kb", description="", tenant_id="local"))
            session.flush()
        try:
            doc = ingest_document(session, KB, "retrieval.md", DOC.encode("utf-8"))
            print(f"ingested doc={doc.id} chunks=ok")
        except Exception as exc:  # noqa: BLE001 - duplicate on re-run is fine
            print(f"ingest skipped: {exc}")

        total = session.query(Chunk).filter(Chunk.kb_id == KB).count()
        print(f"chunks in sqlite: {total}")

    vs, gs = get_vector_store(), get_graph_store()
    print("vector health:", vs.health())
    print("graph health:", gs.health())
    print("vector stats:", vs.stats(KB) if hasattr(vs, "stats") else "n/a")
    print("graph stats:", gs.stats(KB))

    result = retrieve(KB, QUERY, top_k=5, with_trace=True)
    hits = result.items
    print("retrieval hits:", len(hits))
    for h in hits:
        print(f"  rank={h.rank} id={h.chunk_id[:8]} scores={h.scores}")

    graph_only = gs.recall(KB, QUERY, 5)
    print("graph recall:", graph_only)

    with session_scope() as session:
        state = asyncio.run(
            build_graph().ainvoke({"question": QUERY, "kb_id": KB, "run_id": "live", "attempts": 0})
        )
    print("answer:", state["answer"][:200])
    print("citations:", state["citations"])
    print("confidence:", state["confidence"], "refused:", state["refused"])
    print(
        "RESULT",
        json.dumps(
            {
                "vector_backend": vs.backend,
                "graph_backend": gs.backend,
                "hits": len(hits),
                "graph_edges": gs.stats(KB).edges,
                "answer_valid": state["answer_valid"],
            },
            ensure_ascii=False,
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
