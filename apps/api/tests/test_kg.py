"""Entity/relation extraction and the graph channel built on top of it."""

from app.rag.kg import (
    EntityGraph,
    LLMExtractor,
    RuleExtractor,
    Triple,
    detect_entities,
    entity_type,
)


def test_rule_extractor_reads_typed_relations():
    triples = RuleExtractor().triples(
        "NexusAgent 是一个企业知识智能体。检索服务依赖于 Milvus。"
    )
    assert Triple("NexusAgent", "IS_A", "企业知识智能体") in triples
    assert Triple("检索服务", "DEPENDS_ON", "Milvus") in triples


def test_rule_extractor_drops_fragments_it_cannot_type():
    triples = RuleExtractor().triples("是一个。使用。")
    assert triples == []


def test_entity_type_uses_suffix_hints():
    assert entity_type("NexusAgent") == "term"
    assert entity_type("向量数据库") == "datastore"
    assert entity_type("检索服务") == "service"
    assert entity_type("企业知识智能体") == "concept"


def test_graph_recalls_direct_mentions_before_neighbours():
    chunks = [
        ("c1", "NexusAgent 是一个企业知识智能体。"),
        ("c2", "企业知识智能体 使用 Milvus。"),
        ("c3", "部署说明与升级步骤。"),
    ]
    graph = EntityGraph.build(chunks, RuleExtractor())
    ranked = graph.recall("NexusAgent 是什么")

    assert next(chunk_id for chunk_id, _ in ranked) == "c1"
    scores = dict(ranked)
    assert scores["c1"] > scores["c2"] > 0
    assert "c3" not in scores


def test_graph_stats_count_distinct_entities_and_edges():
    graph = EntityGraph.build(
        [("c1", "NexusAgent 是一个企业知识智能体。"), ("c2", "企业知识智能体 使用 Milvus。")],
        RuleExtractor(),
    )
    stats = graph.stats()
    assert stats["edges"] == 2
    assert stats["chunks"] == 2
    assert {"NexusAgent", "Milvus"} <= set(graph.types)


def test_detect_entities_finds_names_without_a_connector():
    names = detect_entities("RRF 融合三路结果，向量数据库负责召回")
    assert "RRF" in names
    assert "向量数据库" in names
    assert entity_type("向量数据库") == "datastore"


def test_llm_extractor_falls_back_when_the_model_is_unreachable():
    extractor = LLMExtractor("http://127.0.0.1:1", "test", "test", timeout=0.2)
    triples = extractor.triples("NexusAgent 使用 Milvus。")
    assert Triple("NexusAgent", "USES", "Milvus") in triples
