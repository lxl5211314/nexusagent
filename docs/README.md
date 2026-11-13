# NexusAgent 企业知识智能体平台

> 面向企业私有知识库的多智能体问答平台。简历个人项目，从零重建（MVP）。

## 版本路线

| 版本 | 内容 | 状态 |
|------|------|------|
| v0.1 | MVP：LangGraph 编排 + 三通道 RAG + RRF + 引用守卫 + SSE 事件源 + 评估 | ✅ 完成 |
| v0.2 | Milvus 适配器、Neo4j 适配器、MinerU OCR、Java Spring Boot 网关 | ⏳ 计划 |
| v0.3 | 多租户、审计日志、docker-compose.lite | ⏳ 计划 |

## v0.1 验收判据（全部通过）

1. `GET /health` → `{"status":"ok"}`
2. 上传文档 → 解析 → 分块 → 嵌入 → 建索引（36 块/示例）
3. `POST /api/chat/stream` SSE 四段事件：`run.started → run.step → run.answer → run.completed → run.finished`
4. 引用越界/无引用 → 自动重写；低置信度 → 拒答
5. 断线续传：`GET /api/runs/{id}/stream` 从 DB 回放全序事件
6. 评估：recall@5=1.0 / mrr@5=0.917 / faithfulness=0.667
7. `pytest` 31 用例全绿

## 关键设计取舍

- **DB 即事件源**：`run_events` 落库 → SSE 回放。免 sticky session，多实例一致。
- **LLM 双模**：`mock`（确定性抽取式，离线可测）/ `openai`（OpenAI 兼容）。嵌入同理：`hash` / `openai`。
- **三通道检索**：dense + BM25 + 词共现图一跳 → RRF(k=60) → 启发式重排 → 上下文压缩。
- **工具注册三件套**：input/output schema + permission + side_effect。只读默认，写操作需审批令牌（对齐用友工具注册规范）。
- **引用守卫**：`[n]` 强制绑定检索块；越界或无引用 → 重试重写（`citation_max_retry=1`）；置信度 < 0.35 → 拒答。

## 运行

```bash
cd apps/api && pip install -e . && python -m app.seed && uvicorn app.main:app --port 8017
cd apps/web && npm install && npm run dev
```

## 测试

```bash
cd apps/api && pytest
```

## 参考

- 设计文档：`docs/superpowers/specs/2026-11-13-nexusagent-mvp-design.md`
