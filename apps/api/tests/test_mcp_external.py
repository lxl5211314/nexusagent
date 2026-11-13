
async def test_worker_appends_an_external_answer_as_a_citable_source(monkeypatch):
    from app.agent import graph as graph_module
    from app.agent.state import SubTask
    from app.agent.tools import ToolResult
    from app.guard.citation import check_answer
    from app.mcp.router import ToolChoice

    class EmptyRetrieval:
        def __init__(self):
            self.items = []
            self.trace = None

    monkeypatch.setattr(graph_module, "retrieve", lambda *args, **kwargs: EmptyRetrieval())
    monkeypatch.setattr(
        "app.mcp.router.enrich_with_mcp",
        lambda *args, **kwargs: (
            ToolChoice("chrome", "list_pages", 1.2, {}),
            ToolResult(ok=True, data={"text": "打开着三个标签页"}, error="", latency_ms=3.0, meta={}),
        ),
    )

    state = {
        "task": SubTask(id=1, question="list open browser pages"),
        "kb_id": "no-such-kb",
        "run_id": "run-x",
    }
    out = await graph_module.worker(state, {"configurable": {"emit": lambda *_: None}})
    contexts = out["contexts"]

    assert contexts[-1].chunk_id == "mcp.chrome.list_pages"
    assert contexts[-1].scores["external"] == 1.0
    # The guard must accept the external source, otherwise the toggle would only produce refusals.
    check = check_answer("有三个标签页 [1]", contexts)
    assert check.valid and check.citations[0].doc_name == "mcp.chrome.list_pages"


async def test_worker_skips_the_external_source_when_the_toggle_is_off(monkeypatch):
    from app.agent import graph as graph_module
    from app.agent.state import SubTask
    from app.agent.tools import ToolResult
    from app.core.config import get_settings
    from app.mcp.router import ToolChoice

    class EmptyRetrieval:
        def __init__(self):
            self.items = []
            self.trace = None

    monkeypatch.setattr(graph_module, "retrieve", lambda *args, **kwargs: EmptyRetrieval())
    monkeypatch.setattr(
        "app.mcp.router.enrich_with_mcp",
        lambda *args, **kwargs: (
            ToolChoice("chrome", "list_pages", 1.2, {}),
            ToolResult(ok=True, data={"text": "打开着三个标签页"}, error="", latency_ms=3.0, meta={}),
        ),
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_cite_external", False)

    state = {
        "task": SubTask(id=1, question="list open browser pages"),
        "kb_id": "no-such-kb",
        "run_id": "run-y",
    }
    out = await graph_module.worker(state, {"configurable": {"emit": lambda *_: None}})
    assert out["contexts"] == []
