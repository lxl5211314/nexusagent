"""LLM abstraction with two implementations.

* ``MockLLM``  — deterministic extractive generation. No network, no key, and
  stable output, which is what the test suite and the default demo run on.
* ``OpenAILLM`` — any OpenAI-compatible chat endpoint (DeepSeek / Qwen /
  SiliconFlow / vLLM).

Both expose the same three capabilities the graph needs: ``plan``,
``generate_stream`` and ``generate``.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from app.agent.state import Finding, SubTask
from app.core.config import get_settings
from app.rag.retriever import RetrievedChunk
from app.rag.text import split_sentences, tokenize

_CONNECTORS = re.compile(r"(?:并且|以及|同时|还有|另外|顺便|然后|再|；|;|。|\n)+")
_ENUM = re.compile(r"^\s*(?:\d+[.、)]|[-*•])\s*", re.MULTILINE)

_SYSTEM_ANSWER = """你是企业知识库问答助手。规则：
1. 只能使用给定片段回答，不得引入片段外的事实。
2. 每个结论句末尾必须标注来源编号，格式 [1]、[2]，编号对应片段序号。
3. 片段不足以回答时，直接说明「知识库中没有足够依据」，不要猜测。
4. 用中文回答，先给结论，再给依据，控制在 5 句以内。"""


class LLM:
    name = "base"

    async def plan(self, question: str, max_subtasks: int) -> list[SubTask]:
        raise NotImplementedError

    async def generate_stream(
        self,
        question: str,
        contexts: list[RetrievedChunk],
        findings: list[Finding],
        strict: bool = False,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    async def generate(
        self,
        question: str,
        contexts: list[RetrievedChunk],
        findings: list[Finding],
        strict: bool = False,
    ) -> str:
        chunks: list[str] = []
        async for delta in self.generate_stream(question, contexts, findings, strict):
            chunks.append(delta)
        return "".join(chunks)


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------
class MockLLM(LLM):
    name = "mock"

    async def plan(self, question: str, max_subtasks: int) -> list[SubTask]:
        return split_subtasks(question, max_subtasks)

    async def generate_stream(
        self,
        question: str,
        contexts: list[RetrievedChunk],
        findings: list[Finding],
        strict: bool = False,
    ) -> AsyncIterator[str]:
        text = self._compose(question, contexts, findings, strict)
        # Emit in small slices so the SSE path behaves like a real token stream.
        step = 6
        for i in range(0, len(text), step):
            yield text[i : i + step]

    def _compose(
        self, question: str, contexts: list[RetrievedChunk], findings: list[Finding], strict: bool
    ) -> str:
        if not contexts:
            return "知识库中没有检索到与问题相关的依据，无法作答。"

        query_terms = set(tokenize(question))
        picked: list[tuple[int, str]] = []
        for idx, ctx in enumerate(contexts[:3], start=1):
            sentence = _best_sentence(ctx.content, query_terms)
            if not sentence:
                continue
            picked.append((idx, sentence))

        if not picked:
            return "知识库中命中的片段与问题相关性不足，无法给出有依据的回答。"

        lead = "仅依据检索片段作答：" if strict else "根据知识库中的内容："
        body = "；".join(f"{sentence} [{idx}]" for idx, sentence in picked)
        tail = ""
        if findings:
            tail = "。补充检索结论：" + findings[0].summary.split("。")[0]
        return f"{lead}{body}{tail}。"


def _best_sentence(content: str, query_terms: set[str], limit: int = 110) -> str:
    sentences = [s for s in split_sentences(content) if len(s) >= 6]
    if not sentences:
        return content.strip()[:limit]
    scored = [(len(query_terms & set(tokenize(s))), idx, s) for idx, s in enumerate(sentences)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    best = scored[0][2] if scored and scored[0][0] > 0 else sentences[0]
    best = best.strip().rstrip("。")
    return best[:limit]


def split_subtasks(question: str, max_subtasks: int) -> list[SubTask]:
    cleaned = _ENUM.sub("\n", question).strip()
    parts = [part.strip(" ，,、。") for part in _CONNECTORS.split(cleaned)]
    parts = [part for part in parts if len(part) >= 3]
    if not parts:
        parts = [question.strip()]
    # Deduplicate while keeping order, then cap.
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    return [SubTask(id=i, question=part) for i, part in enumerate(unique[:max_subtasks], start=1)]


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------
class OpenAILLM(LLM):
    name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.timeout = settings.llm_timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def plan(self, question: str, max_subtasks: int) -> list[SubTask]:
        prompt = (
            f"把用户问题拆成 1-{max_subtasks} 个可独立检索的子问题。"
            "只输出 JSON：{\"subtasks\":[{\"question\":\"...\"}]}。"
            "如果问题本身不可拆分，就返回一个子问题。\n\n用户问题：" + question
        )
        try:
            text = await self._chat([{"role": "user", "content": prompt}], json_mode=True)
            payload = json.loads(_strip_code_fence(text))
            items = payload.get("subtasks", [])
            subtasks = [
                SubTask(id=i, question=str(item["question"]).strip())
                for i, item in enumerate(items[:max_subtasks], start=1)
                if str(item.get("question", "")).strip()
            ]
            return subtasks or [SubTask(id=1, question=question)]
        except Exception:  # noqa: BLE001 - planning must never break the run
            return [SubTask(id=1, question=question)]

    async def generate_stream(
        self,
        question: str,
        contexts: list[RetrievedChunk],
        findings: list[Finding],
        strict: bool = False,
    ) -> AsyncIterator[str]:
        if not contexts:
            yield "知识库中没有检索到与问题相关的依据，无法作答。"
            return

        blocks = [
            f"【片段{i}】来源：{ctx.doc_name} / {ctx.heading or '正文'}\n{ctx.compressed}"
            for i, ctx in enumerate(contexts, start=1)
        ]
        user = "\n\n".join(blocks) + f"\n\n用户问题：{question}"
        if strict:
            user += "\n\n上一次回答存在引用编号越界，请严格只引用上面给出的片段编号。"

        async for delta in self._chat_stream(
            [{"role": "system", "content": _SYSTEM_ANSWER}, {"role": "user", "content": user}]
        ):
            yield delta

    async def _chat(self, messages: list[dict], json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _chat_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        settings = get_settings()
        if settings.llm_provider == "openai" and settings.llm_api_key:
            _llm = OpenAILLM()
        else:
            _llm = MockLLM()
    return _llm


def reset_llm() -> None:
    global _llm
    _llm = None
