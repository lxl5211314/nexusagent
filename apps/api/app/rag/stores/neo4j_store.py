"""Neo4j graph store adapter.

Model: ``(:Chunk {id, kb_id, text})`` -[:MENTIONS]-> ``(:Entity {name, kb_id, type})``,
plus ``(:Entity)-[:REL {type, weight}]->(:Entity)`` for typed relations extracted
from the chunk text. The local index builds the same graph in memory, so both
backends answer one-hop questions alike.
"""

from __future__ import annotations

import logging

from app.rag.kg import EntityGraph, get_extractor
from app.rag.stores.base import StoreStats
from app.rag.text import tokenize

log = logging.getLogger(__name__)

MAX_TOKENS = 32
MAX_TEXT = 4000


class Neo4jGraphStore:
    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        from neo4j import GraphDatabase

        self.database = database
        self._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            # Fresh databases legitimately have no labels yet; the
            # "label does not exist" notifications are pure noise in logs.
            notifications_min_severity="OFF",
        )

    # -- writes -------------------------------------------------------------
    def rebuild(self, kb_id: str, chunks: list[tuple[str, str]]) -> None:
        # Rebuild is a full replace: re-MERGEd relation weights would otherwise
        # drift upwards on every re-upload of the same corpus.
        self.delete_kb(kb_id)
        graph = EntityGraph.build(chunks, get_extractor())
        rows = [{"id": chunk_id, "text": text[:MAX_TEXT]} for chunk_id, text in chunks]
        entities = [{"name": name, "type": kind} for name, kind in graph.types.items()]
        mentions = [
            {"chunk": chunk_id, "entity": name}
            for name, chunk_ids in graph.mentions.items()
            for chunk_id in chunk_ids
        ]
        relations = [
            {"head": head, "rel": rel, "tail": tail, "weight": weight}
            for head, rel, tail, weight in graph.relations
        ]

        with self._driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (c:Chunk {id: row.id, kb_id: $kb})
                SET c.text = row.text
                """,
                rows=rows,
                kb=kb_id,
            )
            session.run(
                """
                UNWIND $entities AS row
                MERGE (e:Entity {name: row.name, kb_id: $kb})
                SET e.type = row.type
                """,
                entities=entities,
                kb=kb_id,
            )
            session.run(
                """
                UNWIND $mentions AS m
                MATCH (c:Chunk {id: m.chunk, kb_id: $kb})
                MATCH (e:Entity {name: m.entity, kb_id: $kb})
                MERGE (c)-[:MENTIONS]->(e)
                """,
                mentions=mentions,
                kb=kb_id,
            )
            session.run(
                """
                UNWIND $relations AS rel
                MATCH (a:Entity {name: rel.head, kb_id: $kb})
                MATCH (b:Entity {name: rel.tail, kb_id: $kb})
                MERGE (a)-[r:REL {type: rel.rel}]->(b)
                ON CREATE SET r.weight = rel.weight
                ON MATCH SET r.weight = r.weight + rel.weight
                """,
                relations=relations,
                kb=kb_id,
            )
        log.info(
            "rebuilt neo4j graph kb=%s chunks=%s entities=%s relations=%s",
            kb_id,
            len(rows),
            len(entities),
            len(relations),
        )

    def delete_kb(self, kb_id: str) -> None:
        with self._driver.session(database=self.database) as session:
            session.run("MATCH (n {kb_id: $kb}) DETACH DELETE n", kb=kb_id)

    # -- reads --------------------------------------------------------------
    def recall(self, kb_id: str, query: str, limit: int) -> list[tuple[str, float]]:
        tokens = list(dict.fromkeys(tokenize(query)))[:MAX_TOKENS]
        if not tokens:
            return []

        scores: dict[str, float] = {}
        with self._driver.session(database=self.database) as session:
            direct = session.run(
                """
                MATCH (e:Entity {kb_id: $kb})
                WHERE $text CONTAINS e.name OR e.name IN $tokens
                MATCH (c:Chunk {kb_id: $kb})-[:MENTIONS]->(e)
                RETURN c.id AS chunk_id, count(e) AS hits
                ORDER BY hits DESC
                LIMIT $limit
                """,
                kb=kb_id,
                text=query,
                tokens=tokens,
                limit=limit,
            )
            for record in direct:
                scores[record["chunk_id"]] = scores.get(record["chunk_id"], 0.0) + float(record["hits"])

            hop = session.run(
                """
                MATCH (e:Entity {kb_id: $kb})-[r:REL]-(n:Entity {kb_id: $kb})
                WHERE ($text CONTAINS e.name OR e.name IN $tokens)
                  AND NOT ($text CONTAINS n.name)
                MATCH (c:Chunk {kb_id: $kb})-[:MENTIONS]->(n)
                RETURN c.id AS chunk_id, sum(coalesce(r.weight, 1)) AS weight
                ORDER BY weight DESC
                LIMIT $limit
                """,
                kb=kb_id,
                text=query,
                tokens=tokens,
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
                OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity {kb_id: $kb})
                OPTIONAL MATCH (:Entity {kb_id: $kb})-[r:REL]->(:Entity {kb_id: $kb})
                RETURN count(DISTINCT c) AS chunks,
                       count(DISTINCT e) AS nodes,
                       count(DISTINCT r) AS edges
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

    def snapshot(self, kb_id: str, limit: int = 200) -> dict:
        with self._driver.session(database=self.database) as session:
            node_rows = session.run(
                """
                MATCH (e:Entity {kb_id: $kb})
                OPTIONAL MATCH (e)-[r:REL]-(:Entity {kb_id: $kb})
                WITH e, count(r) AS degree
                ORDER BY degree DESC, e.name
                LIMIT $limit
                RETURN e.name AS name, e.type AS type, degree
                """,
                kb=kb_id,
                limit=limit,
            ).data()
            edge_rows = session.run(
                """
                MATCH (a:Entity {kb_id: $kb})-[r:REL]->(b:Entity {kb_id: $kb})
                RETURN a.name AS source, r.type AS relation, b.name AS target,
                       coalesce(r.weight, 1.0) AS weight
                LIMIT $limit
                """,
                kb=kb_id,
                limit=limit * 4,
            ).data()
            mention_rows = session.run(
                """
                MATCH (c:Chunk {kb_id: $kb})-[:MENTIONS]->(e:Entity {kb_id: $kb})
                RETURN e.name AS name, collect(c.id)[..8] AS chunks
                """,
                kb=kb_id,
            ).data()

        mentions = {row["name"]: row["chunks"] for row in mention_rows}
        nodes = [
            {
                "name": row["name"],
                "type": row["type"] or "concept",
                "degree": int(row["degree"]),
                "mentions": mentions.get(row["name"], []),
            }
            for row in node_rows
        ]
        kept = {node["name"] for node in nodes}
        edges = [
            {
                "source": row["source"],
                "relation": row["relation"],
                "target": row["target"],
                "weight": float(row["weight"]),
            }
            for row in edge_rows
            if row["source"] in kept and row["target"] in kept
        ]
        stats = self.stats(kb_id)
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {"nodes": stats.nodes, "edges": stats.edges, "chunks": stats.chunks},
        }

    def health(self) -> dict:
        try:
            self._driver.verify_connectivity()
            return {"backend": "neo4j", "ok": True}
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"backend": "neo4j", "ok": False, "error": str(exc)}

    def close(self) -> None:
        self._driver.close()
