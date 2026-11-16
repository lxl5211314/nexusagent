"""Small talk the knowledge base cannot answer.

"你好" is not a retrieval problem. Sending it through the RAG pipeline returns
whatever chunk happens to share a character with it, which is how a greeting
turned into a refused, half-nonsense answer. Greetings, identity questions and
thanks get a fixed reply instead, and the answer is not dressed up as something
the knowledge base said.
"""

from __future__ import annotations

import re

# Whole-question greetings. Anchored on purpose: "你好，RRF 是什么" is a real
# question with a polite prefix and must keep going through retrieval.
_GREETING = re.compile(
    r"^(?:你好|您好|hi|hello|hey|哈喽|嗨|在吗|在么|在不在|早上好|下午好|晚上好|晚安|早)"
    r"[\s!！。.~～,，?？、]*$",
    re.IGNORECASE,
)

# Identity / capability questions. These are about the assistant, not the corpus.
_IDENTITY = (
    "你是谁",
    "你是什么",
    "你是干什么的",
    "你能做什么",
    "你能干什么",
    "你会做什么",
    "你会什么",
    "你有什么功能",
    "你有哪些功能",
    "有什么功能",
    "怎么用你",
    "如何使用你",
    "怎么使用你",
    "使用说明",
    "功能介绍",
    "帮助",
    "help",
    "介绍一下你自己",
    "介绍你自己",
)

_THANKS = ("谢谢", "多谢", "感谢", "thanks", "thank you", "thx", "3q")

# A question may contain "你是谁" while still being about the corpus
# ("文档里说你是谁写的"). Identity/thanks only match short questions; greet
# patterns are anchored on the whole string.
_IDENTITY_MAX_LEN = 12
_THANKS_MAX_LEN = 24

#: Kinds that are not answers about the corpus — they carry no evidence and must
#: not be pushed through the citation guard's confidence gate.
SMALLTALK = ("greeting", "identity", "thanks")


def _clean(text: str) -> str:
    return re.sub(r"[\s!！。.~～,，?？、]+", "", text or "").lower()


def smalltalk_kind(question: str) -> str | None:
    """Return 'greeting' | 'identity' | 'thanks' | None for a whole question."""
    raw = (question or "").strip()
    if not raw:
        return None
    if _GREETING.match(raw):
        return "greeting"

    cleaned = _clean(raw)
    if len(cleaned) <= _THANKS_MAX_LEN and any(word in cleaned for word in _THANKS):
        return "thanks"
    if len(cleaned) <= _IDENTITY_MAX_LEN and any(word in cleaned for word in _IDENTITY):
        return "identity"
    return None


def refusal_text() -> str:
    """Shown in place of a draft that the citation guard could not support.

    The draft is not "almost right, with a warning": it is text the knowledge
    base does not back. Rendering it next to a 已拒答 badge still puts a
    plausible-looking guess in front of the reader.
    """
    return (
        "这个问题的答案在知识库里找不到能支撑的原文块，所以我不作答。\n\n"
        "可能的原因是库里没有相关文档，或者问题问的角度和文档对不上。"
        "可以换个问法，也可以先把相关材料传上来。"
    )


def compose_reply(kind: str, kb_name: str | None = None) -> str:
    """The fixed reply for a small-talk turn, written like a person would."""
    where = f"「{kb_name}」" if kb_name else "当前知识库"
    if kind == "thanks":
        return "不客气。还有别的想问的，直接说就行。"
    if kind == "identity":
        return (
            f"我是 NexusAgent，绑在{where}上的问答助手。\n\n"
            "我做的事只有一件：拿你的问题去检索这个库，把答案和出处一起给你。\n\n"
            "- 答案里的方括号数字是引用编号，点开能直接看到被引用的那段原文\n"
            "- 库里找不到依据时我会说找不到，不会编一个看起来合理的答案\n"
            "- 问题跟知识库无关时，我也一样拒答\n\n"
            f"你可以问{where}里写过的东西，比如「RRF 的 k 取多少」。"
        )
    return (
        f"你好。我是 NexusAgent，{where}的问答助手。"
        "直接问文档里的内容就行——答案会带引用，点开引用能看到原文。"
    )
