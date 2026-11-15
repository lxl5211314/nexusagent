"""Entity/relation extraction and an in-memory entity graph.

The Neo4j adapter and the local fallback share this extractor, so switching
backends changes storage, not what the graph means: nodes are entities, edges
are typed relations, and a chunk links to the entities it mentions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings
from app.rag.text import split_sentences, tokenize

MAX_ENTITY_LEN = 16
MIN_ENTITY_LEN = 2

_ENTITY_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_.\-+]{2,16}$")
_TRAILING = "的 了 和 与 及 或 等 中 上 下 里 也 还 就 都 再 又"
_CLAUSE_MARK = re.compile(
    r"(?:并且|而且|但是|不过|同时|然后|因此|所以|因为|由于|其中|如果|之后|之前|以后|以前|可能|通常|往往|容易|仍然|即|则|就|也|都|还|并|而|但|或)"
)
_PARTICLE = re.compile(r"(?:的|了|和|与|及|或|等|中|上|下|里)")
_LEADING_PREP = re.compile(r"^(?:于|在|对|把|被)+")
_VERB_PHRASES = {
    "拒绝回答", "拒绝", "支持", "提供", "包括", "包含", "生成", "返回",
    "调用", "使用", "负责", "执行", "处理", "完成", "实现",
}
_STOPWORDS = {
    "这个", "那个", "我们", "你们", "他们", "它们", "目前", "同时", "此外", "因此",
    "所以", "但是", "如果", "可以", "需要", "应该", "已经", "不是", "没有", "一个",
}

# connector -> relation type. Order matters: longer connectors first so that
# "是一种" is not swallowed by "是".
CONNECTORS: tuple[tuple[str, str], ...] = (
    ("是一种", "IS_A"),
    ("是一个", "IS_A"),
    ("属于", "IS_A"),
    ("依赖于", "DEPENDS_ON"),
    ("依赖", "DEPENDS_ON"),
    ("基于", "BASED_ON"),
    ("包括", "INCLUDES"),
    ("包含", "INCLUDES"),
    ("分为", "INCLUDES"),
    ("负责", "OWNED_BY"),
    ("提供", "PROVIDED_BY"),
    ("使用", "USES"),
    ("调用", "CALLS"),
    ("写入", "WRITES_TO"),
    ("读取", "READS_FROM"),
    ("存储", "STORED_IN"),
    ("部署", "DEPLOYED_ON"),
    ("由", "MADE_OF"),
    ("采用", "USES"),
    ("通过", "USES"),
    ("支持", "SUPPORTS"),
    ("运行在", "DEPLOYED_ON"),
    ("连接到", "CONNECTS_TO"),
    ("组装成", "MADE_OF"),
    ("合并为", "INCLUDES"),
    (" yields ", "PRODUCES"),
    (" uses ", "USES"),
    (" is a ", "IS_A"),
    (" is an ", "IS_A"),
    (" depends on ", "DEPENDS_ON"),
    (" is part of ", "INCLUDES"),
)

_TYPE_HINTS = (
    ("公司", "organization"),
    ("团队", "organization"),
    ("部门", "organization"),
    ("平台", "system"),
    ("系统", "system"),
    ("服务", "service"),
    ("接口", "api"),
    ("模型", "model"),
    ("数据库", "datastore"),
    ("队列", "datastore"),
    ("索引", "datastore"),
)


@dataclass(frozen=True)
class Triple:
    head: str
    relation: str
    tail: str


@dataclass(frozen=True)
class GraphStats:
    nodes: int
    edges: int
    chunks: int


class Extractor(Protocol):
    name: str

    def triples(self, text: str) -> list[Triple]: ...


def entity_type(name: str) -> str:
    for suffix, kind in _TYPE_HINTS:
        if name.endswith(suffix):
            return kind
    if re.fullmatch(r"[A-Z][A-Za-z0-9_.\-]*", name):
        return "term"
    return "concept"


def _clean(raw: str, *, take: str) -> str:
    """Trim a fragment down to the phrase adjacent to the connector.

    Chinese sentences rarely carry the punctuation that separates a subject from
    its predicate, so a plain split leaves whole clauses behind. The two extra
    cuts below keep the boundary at a conjunction: the head keeps what follows
    the last clause marker, the tail stops at the first particle that opens a
    new clause.
    """
    text = raw.strip().strip("：:，,。.;；!！?？、\"'`（）()[]【】<>《》 ")
    if not text:
        return ""
    parts = re.split(r"[，,。.;；：:、\s]", text)
    parts = [p for p in parts if p]
    if not parts:
        return ""
    candidate = parts[-1] if take == "tail" else parts[0]
    candidate = candidate.strip(_TRAILING).strip()
    if take == "tail":
        marks = list(_CLAUSE_MARK.finditer(candidate))
        if marks:
            candidate = candidate[marks[-1].end():].strip(_TRAILING).strip()
    else:
        chunks = [p for p in candidate.split("的") if p]
        if len(chunks) > 1:
            candidate = max(chunks, key=len).strip(_TRAILING).strip()
        else:
            head = _PARTICLE.search(candidate)
            if head and head.start() >= MIN_ENTITY_LEN:
                candidate = candidate[: head.start()].strip(_TRAILING).strip()
        candidate = _LEADING_PREP.sub("", candidate).strip(_TRAILING).strip()
    for suffix in ("组成", "构成", "而来", "实现", "完成"):
        if candidate.endswith(suffix) and len(candidate) > len(suffix) + 1:
            candidate = candidate[: -len(suffix)].strip(_TRAILING).strip()
            break
    if len(candidate) < MIN_ENTITY_LEN or len(candidate) > MAX_ENTITY_LEN:
        return ""
    if candidate in _STOPWORDS or candidate in _VERB_PHRASES or not _ENTITY_RE.match(candidate):
        return ""
    return candidate


class RuleExtractor:
    """Connector-driven extraction.

    It is deliberately shallow: it reads "A 依赖 B" as a typed edge instead of
    guessing a full parse tree. That keeps the graph explainable — every edge
    can be traced back to the sentence that produced it.
    """

    name = "rule"

    def triples(self, text: str) -> list[Triple]:
        found: list[Triple] = []
        seen: set[tuple[str, str, str]] = set()
        for sentence in split_sentences(text):
            sentence = sentence.strip()
            if len(sentence) < 4:
                continue
            for connector, relation in CONNECTORS:
                if connector not in sentence:
                    continue
                left, _, right = sentence.partition(connector)
                head = _clean(left, take="tail")
                tail = _clean(right, take="head")
                if not head or not tail or head == tail:
                    continue
                key = (head, relation, tail)
                if key in seen:
                    continue
                seen.add(key)
                found.append(Triple(head=head, relation=relation, tail=tail))
        return found


class LLMExtractor:
    """Same contract, but asks a chat model for typed triples."""

    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._fallback = RuleExtractor()

    def triples(self, text: str) -> list[Triple]:
        prompt = (
            "Extract entity relations from the text. Return JSON only: "
            '{"triples":[{"head":"","relation":"","tail":""}]}. '
            "relation must be an UPPER_SNAKE_CASE verb such as IS_A, DEPENDS_ON, "
            "INCLUDES, USES, OWNS. head and tail must be noun phrases copied from "
            "the text, 2-24 characters, never a full sentence."
        )
        try:
            import httpx

            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": text[:4000]},
                    ],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            payload = json.loads(_strip_code_fence(content))
            rows = payload.get("triples", [])
            out: list[Triple] = []
            for row in rows:
                head = str(row.get("head", "")).strip()
                tail = str(row.get("tail", "")).strip()
                relation = str(row.get("relation", "")).strip().upper().replace(" ", "_")
                if (
                    MIN_ENTITY_LEN <= len(head) <= MAX_ENTITY_LEN
                    and MIN_ENTITY_LEN <= len(tail) <= MAX_ENTITY_LEN
                    and head != tail
                    and relation
                ):
                    out.append(Triple(head=head, relation=relation, tail=tail))
            return out or self._fallback.triples(text)
        except Exception:  # noqa: BLE001 - the graph must survive a model outage
            return self._fallback.triples(text)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


# Entities also come from names, not only from relations: a doc that says
# "RRF 融合三路结果" never uses a connector but RRF is still an entity.
_CJK_ENTITY_RE = re.compile(
    r"[\u4e00-\u9fff]{2,10}(?:数据库|服务|平台|系统|模型|索引|队列|网关|引擎|智能体|知识图谱|中间件|缓存)"
)
_ASCII_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9_.+-]{1,23}\b")
_EN_STOP = {
    "The", "This", "That", "These", "Those", "For", "And", "But", "If", "When",
    "While", "With", "Without", "From", "Into", "Each", "Every", "Note", "See",
}


def detect_entities(text: str) -> list[str]:
    """Proper names and typed phrases: the entity half of the graph."""
    found: list[str] = []
    for match in _CJK_ENTITY_RE.finditer(text):
        found.append(match.group(0))
    for match in _ASCII_ENTITY_RE.finditer(text):
        name = match.group(0).strip(".")
        if name in _EN_STOP or len(name) < 2:
            continue
        found.append(name)
    return list(dict.fromkeys(found))


class EntityGraph:
    """In-memory mirror of the Neo4j model, used by the local graph store."""

    def __init__(self) -> None:
        self.types: dict[str, str] = {}
        self.relations: list[tuple[str, str, str, float]] = []
        self.mentions: dict[str, set[str]] = {}

    @classmethod
    def build(cls, chunks: list[tuple[str, str]], extractor: Extractor) -> EntityGraph:
        graph = cls()
        seen_edges: dict[tuple[str, str, str], float] = {}
        for chunk_id, text in chunks:
            for triple in extractor.triples(text):
                graph.types.setdefault(triple.head, entity_type(triple.head))
                graph.types.setdefault(triple.tail, entity_type(triple.tail))
                graph.mentions.setdefault(triple.head, set()).add(chunk_id)
                graph.mentions.setdefault(triple.tail, set()).add(chunk_id)
                key = (triple.head, triple.relation, triple.tail)
                seen_edges[key] = round(seen_edges.get(key, 0.0) + 1.0, 3)
            for name in detect_entities(text):
                graph.types.setdefault(name, entity_type(name))
                graph.mentions.setdefault(name, set()).add(chunk_id)
        graph.relations = [(h, r, t, w) for (h, r, t), w in seen_edges.items()]
        return graph

    def matched_entities(self, query: str) -> list[str]:
        tokens = set(tokenize(query))
        hits = []
        for name in self.types:
            if name in query or name in tokens:
                hits.append(name)
        return hits

    def recall(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        hits = self.matched_entities(query)
        if not hits:
            return []
        scores: dict[str, float] = {}
        for name in hits:
            for chunk_id in self.mentions.get(name, ()):  # direct mention
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0
        neighbours: set[str] = set()
        for head, _relation, tail, _weight in self.relations:
            if head in hits:
                neighbours.add(tail)
            if tail in hits:
                neighbours.add(head)
        for name in neighbours - set(hits):
            for chunk_id in self.mentions.get(name, ()):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 0.5
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:limit]

    def stats(self) -> dict[str, int]:
        chunks = {chunk_id for ids in self.mentions.values() for chunk_id in ids}
        return {"nodes": len(self.types), "edges": len(self.relations), "chunks": len(chunks)}


def graph_snapshot(graph: EntityGraph, limit: int = 200) -> dict:
    """Flatten the graph for the UI: nodes carry their type, degree and source chunks."""
    degree: dict[str, int] = {}
    for head, _relation, tail, _weight in graph.relations:
        degree[head] = degree.get(head, 0) + 1
        degree[tail] = degree.get(tail, 0) + 1

    names = sorted(graph.types, key=lambda name: (-degree.get(name, 0), name))[:limit]
    kept = set(names)
    nodes = [
        {
            "name": name,
            "type": graph.types[name],
            "degree": degree.get(name, 0),
            "mentions": sorted(graph.mentions.get(name, ()))[:8],
        }
        for name in names
    ]
    edges = [
        {"source": head, "relation": relation, "target": tail, "weight": weight}
        for head, relation, tail, weight in graph.relations
        if head in kept and tail in kept
    ]
    return {"nodes": nodes, "edges": edges, "stats": graph.stats()}


_extractor: Extractor | None = None


def get_extractor() -> Extractor:
    global _extractor
    if _extractor is None:
        settings = get_settings()
        if settings.graph_extractor == "llm" and settings.llm_api_key:
            _extractor = LLMExtractor(
                settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s
            )
        else:
            _extractor = RuleExtractor()
    return _extractor


def reset_extractor() -> None:
    global _extractor
    _extractor = None
