# NexusAgent

企业私有知识智能体平台（MVP）。基于简历个人项目重建：多智能体编排 + Agentic RAG + 检索引用守卫 + 可离线运行。

> 本仓库从零重写，不复制旧仓库代码；仅沿用「三通道检索 + RRF 融合 + 引用强绑定」的设计思路。

## 特性（MVP v0.1）

- **多智能体编排**：LangGraph `StateGraph`，Supervisor → 拆解 → 并行子任务（`Send` fan-out）→ 汇总 → 引用守卫 → 终态。
- **Agentic RAG 三通道检索**：dense（hash 嵌入，离线）/ BM25（k1=1.5, b=0.75）/ 词共现图一跳 → **RRF 融合**（k=60）→ 启发式重排 → 上下文压缩。
- **引用守卫（防幻觉）**：答案中的 `[n]` 引用强制绑定检索块，越界/无引用自动重试重写；低置信度拒答。
- **可观测**：全程事件落库（DB 即事件源），SSE 流式推送 `run.started / run.step / run.answer / run.trace / run.completed / run.finished`；断线后按 run_id 从 DB 回放续传。
- **评估 CLI + API**：recall@K / mrr@K / faithfulness（gold_answer 词面判定），JSONL 数据集。
- **零外部依赖模式**：`llm_provider=mock` + `embedding_provider=hash`，无 Key、无 Docker、无网络即可全链路跑通。

## 快速开始

```bash
# 后端（Python 3.12+）
cd apps/api
pip install -e .
uvicorn app.main:app --port 8017
# 首次：灌入示例知识库与评估集
python -m app.seed

# 前端（Node 20+）
cd apps/web
npm install
npm run dev        # http://localhost:5173 ，/api 代理到 8017
```

生产构建后 FastAPI 会自动挂载 `apps/web/dist`，单进程访问 `http://localhost:8017/` 即为完整 UI。

## 测试

```bash
cd apps/api
pytest            # 31 个用例，全部离线可跑
```

## 架构

```
┌────────────────────────── Vue3 (apps/web) ──────────────────────────┐
│  KB 侧栏 · 聊天 SSE · 步骤/引用面板 · 检索 Trace · 上传 · 评估      │
└──────────────┬──────────────────────────────────────────────────────┘
               │ /api/*  (vite proxy → 8017)
┌──────────────▼──────────────────────────────────────────────────────┐
│  FastAPI (apps/api)                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────┐ │
│  │ knowledge    │   │ chat/stream  │   │ eval/runs               │ │
│  │ upload/CRUD  │   │ SSE 流式     │   │ recall/MRR/faithfulness │ │
│  └──────┬───────┘   └──────┬───────┘   └───────────┬─────────────┘ │
│         │                  │                       │               │
│         ▼                  ▼                       ▼               │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ingest: parse → chunk → embed → 落库                          │  │
│  │ retriever: dense + BM25 + graph → RRF(k=60) → rerank → 压缩   │  │
│  │ agent: supervisor → plan → Send[] → worker → synthesize       │  │
│  │   └─ citation guard（越界重写/拒答）                          │  │
│  │ streaming: run_events 落库 + SSE 扇出 + DB 回放续传            │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## 目录

```
apps/
  api/                 # FastAPI 后端（SQLite WAL，可换 PostgreSQL）
    app/
      agent/           # LangGraph 编排、LLM 双模、工具注册
      rag/             # chunk/embed/bm25/fusion/rerank/graph/retriever
      guard/           # 引用守卫（防幻觉）
      streaming/       # 事件源 + SSE 回放
      eval/            # 指标 + CLI
      api/routes/      # health/knowledge/chat/eval
    tests/             # 31 个用例
  web/                 # Vue3 + Vite 前端
docs/
  superpowers/specs/2026-11-13-nexusagent-mvp-design.md
```

## 路线图

- **v0.2**：Milvus（真实向量库）/ Neo4j（知识图谱）适配器、MinerU OCR 解析、Java Spring Boot 网关（对齐简历技术栈）。
- **v0.3**：多租户 + 审计日志 + docker-compose.lite（本地一键起）。
