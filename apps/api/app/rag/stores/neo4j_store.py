"""Neo4j graph store adapter.

Model: ``(:Chunk {id, kb_id, text})`` -[:MENTIONS]-> ``(:Term {name, kb_id})``,
plus ``(:Term)-[:CO_OCCURS {weight}]-(:Term)`` for one-hop expansion. Terms come
from the same tokenizer the local index uses, so both backends rank alike.
"""

from __future__ import annotations

import logging
from itertools import combinations

from app.rag.stores.base import StoreStats
from app.rag.text import tokenize

log = logging.getLogger(__name__)

MAX_TERMS_PER_CHUNK = 16
MAX_PAIRS_PER_CHUNK = 40


class Neo4jGraphStore:
    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        from neo4j import GraphDatabase

        self.database = database
        self._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            # Fresh databases legitimately have no Chunk/Term labels yet; the
            # "label does not exist" notifications are pure noise in logs.
            notifications_min_severity="OFF",
        )

    # -- writes -------------------------------------------------------------
    def rebuild(self, kb_id: str, chunks: list[tuple[str, str]]) -> None:
        # Rebuild is a full replace: re-MERGEd co-occurrence weights would
        # otherwise drift upwards on every re-upload of the same corpus.
        self.delete_kb(kb_id)
        rows = []
        pairs: list[tuple[str, str]] = []
        for chunk_id, text in chunks:
            terms = list(dict.fromkeys(tokenize(text)))[:MAX_TERMS_PER_CHUNK]
            rows.append({"id": chunk_id, "text": text[:4000], "terms": terms})
            pairs.extend(list(combinations(sorted(terms), 2))[:MAX_PAIRS_PER_CHUNK])

        with self._driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (c:Chunk {id: row.id, kb_id: $kb})
                SET c.text = row.text
                WITH c, row
                UNWIND row.terms AS term
                MERGE (t:Term {name: term, kb_id: $kb})
                MERGE (c)-[:MENTIONS]->(t)
                """,
                rows=rows,
                kb=kb_id,
            )
            session.run(
                """
                UNWIND $pairs AS pair
                MATCH (a:Term {name: pair[0], kb_id: $kb})
                MATCH (b:Term {name: pair[1], kb_id: $kb})
                MERGE (a)-[r:CO_OCCURS]->(b)
                ON CREATE SET r.weight = 1
                ON MATCH SET r.weight = r.weight + 1
                """,
                pairs=[list(pair) for pair in pairs],
                kb=kb_id,
            )
        log.info("rebuilt neo4j graph kb=%s chunks=%s pairs=%s", kb_id, len(rows), len(pairs))

    def delete_kb(self, kb_id: str) -> None:
        with self._driver.session(database=self.database) as session:
            session.run("MATCH (n {kb_id: $kb}) DETACH DELETE n", kb=kb_id)

    # -- reads --------------------------------------------------------------
    def recall(self, kb_id: str, query: str, limit: int) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(tokenize(query)))[:MAX_TERMS_PER_CHUNK]
        if not terms:
            return []

        scores: dict[str, float] = {}
        with self._driver.session(database=self.database) as session:
            direct = session.run(
                """
                MATCH (c:Chunk {kb_id: $kb})-[:MENTIONS]->(t:Term {kb_id: $kb})
                WHERE t.name IN $terms
                RETURN c.id AS chunk_id, count(t) AS hits
                ORDER BY hits DESC
                LIMIT $limit
                """,
                kb=kb_id,
                terms=terms,
                limit=limit,
            )
            for record in direct:
                scores[record["chunk_id"]] = scores.get(record["chunk_id"], 0.0) + float(record["hits"])

            hop = session.run(
                """
                MATCH (t:Term {kb_id: $kb})-[r:CO_OCCURS]-(n:Term {kb_id: $kb})
                WHERE t.name IN $terms AND NOT n.name IN $terms
                MATCH (c:Chunk {kb_id: $kb})-[:MENTIONS]->(n)
                RETURN c.id AS chunk_id, sum(r.weight) AS weight
                ORDER BY weight DESC
                LIMIT $limit
                """,
                kb=kb_id,
                terms=terms,
                limit=limit,
            )
            for record in hop:
                scores[record["chunk_id"]] = scores.get(record["chunk_id"], 0.0) + 0.5 * float(
                    record["weight"]
                )

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    def stats(self, kb_id: str) -> StoreStats:
        with self._driver.session(database=self.database) as session:
            record = session.run(
                """
                MATCH (c:Chunk {kb_id: $kb})
                OPTIONAL MATCH (c)-[:MENTIONS]->(t:Term {kb_id: $kb})
                OPTIONAL MATCH (:Term {kb_id: $kb})-[e:CO_OCCURS]-(:Term {kb_id: $kb})
                RETURN count(DISTINCT c) AS chunks,
                       count(DISTINCT t) AS nodes,
                       count(DISTINCT e) AS edges
                """,
                kb=kb_id,
            ).single()
        if record is None:
            return StoreStats(backend="neo4j")
        return StoreStats(
            backend="neo4j",
            chunks=int(record["chunks"]),
            nodes=int(record["nodes"]),
            edges=int(record["edges"]),
        )

    def health(self) -> dict:
        try:
            self._driver.verify_connectivity()
            return {"backend": "neo4j", "ok": True}
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"backend": "neo4j", "ok": False, "error": str(exc)}

    def close(self) -> None:
        self._driver.close()
