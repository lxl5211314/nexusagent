# NexusAgent — MVP v0.1 设计说明

日期：2026-11-13
状态：✅ v0.1 已实现并验证（31 测试全绿、HTTP 实测通过、提交 757b03f）

## 1. 目标与验收

**目标**：从零实现一个可 `uv run` 直接启动、无需任何外部 API Key 即可演示的企业知识智能体平台 MVP，作为简历项目 NexusAgent 的可验证代码底座。

**验收判据（全部可执行）**

1. `uv sync && uv run uvicorn app.main:app` 3 秒内启动，`/api/health` 返回 ok。
2. 上传 md/txt 文档 → 自动解析、分块、建索引；`/api/retrieval/search` 能返回 dense / sparse / rrf / rerank 四段 trace。
3. `POST /api/chat/stream` 返回 SSE：`step.started` → `tool.call` → `token` → `citation` → `run.completed`。
4. 答案中的 `[n]` 必须指向本次检索到的 chunk；不满足时重写一次，仍失败则拒答（`refused=true`）。
5. 刷新页面 / 断线后 `GET /api/runs/{id}/stream?after_seq=N` 能续传，终态从 DB 恢复。
6. `uv run python -m app.eval.cli run --dataset data/eval/sample.jsonl` 输出 recall@5、MRR、citation_valid_rate、faithfulness。
7. `pytest` 全绿（分块、RRF、citation guard、API 冒烟）。

## 2. 非目标（MVP 明确不做）

- 不做多租户 / SSO / 计费。
- 不接 Milvus / Neo4j 真实集群（提供同名接口 + 内存实现，`MILVUS_URI`/`NEO4J_URI` 存在时按适配器加载，v0.2 实现）。
- 不接 MinerU / PaddleOCR（v0.2），MVP 用 pypdf / python-docx / 纯文本。
- 不做前端组件级细粒度权限（v0.3，Java 网关负责）。

## 3. 架构

```
apps/api (Python 3.13 / FastAPI)
  api/routes    knowledge / chat / runs / retrieval / eval / health
  agent         LangGraph StateGraph: supervisor -> plan -> dispatch(Send) -> worker -> synthesize -> verify
  rag           parse -> chunk -> embed(hash) -> index(vector+bm25) -> hybrid retrieve -> RRF(k=60) -> rerank -> compress
  guard         citation_guard: [n] 越界检测 / 重写 / 拒答
  streaming     event store (DB) + SSE（Last-Event-ID 续传）
  eval          recall@k / MRR / citation_valid_rate / faithfulness

apps/web (Vue3 + TS + Vite)
  对话（SSE 思考链路 / 工具调用 / 引用）| 知识库 | 检索调试 | 评估
```

**关键取舍**

- **DB 作为事件源**：SSE 事件逐条落 `run_events`，断线按 `seq` 回放。多实例下不需要 sticky session，代价是多一次写。
- **LLM 双模**：`mock` 用抽取式生成（零依赖、可离线跑、结果确定，便于测试）；`openai` 走 OpenAI 兼容协议（DeepSeek / Qwen / SiliconFlow）。
- **检索可插拔**：`VectorStore` / `GraphStore` / `Embedder` 三个接口，内存实现 + 真实实现同签名，方便面试时讲「为什么这样分层」。
- **工具注册三件套**：每个工具声明 `input/output schema + permission + side_effect`，注册表强制只读优先、写操作需审批令牌。与用友「智能交付 Agent 工具注册规范」一致。

## 4. 数据模型

`knowledge_bases` / `documents` / `chunks` / `runs` / `run_events` / `tool_invocations` / `eval_runs` / `eval_cases`。

chunk 存 `embedding` JSON（MVP），生产切 Milvus 时该字段废弃。

## 5. 错误处理

| 场景 | 行为 |
| --- | --- |
| 文档解析失败 | document.status=failed + error，接口返回 202 后异步可见 |
| 检索 0 命中 | 直接拒答，confidence=0，附「未检索到依据」 |
| 引用越界 | 重写 1 次；仍越界 → 拒答并记录 violation |
| LLM 超时/异常 | run.status=failed，事件流发 `run.failed`，不吞异常 |
| 客户端断开 | 生成任务不取消，继续写完并落终态（可重连取回） |

## 6. 测试策略

- 单元：chunking 边界、BM25、RRF 排序、citation guard 三分支、tool registry 权限拒绝。
- 集成：上传 → 检索 → SSE 全链路（TestClient + mock LLM）。
- 评估：跑 sample 数据集，断言 recall@5 ≥ 0.8（回归门禁）。

## 7. 里程碑

- v0.1（本次）：上述 7 条验收。
- v0.2：Milvus / Neo4j 适配器、MinerU 解析、Java Spring Boot 网关（JWT + RBAC + SSE 透传）。
- v0.3：多租户、审计、Docker lite 编排、前端拆包。
