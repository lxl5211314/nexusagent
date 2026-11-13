# NexusAgent

企业私有知识智能体平台。多智能体编排 + Agentic RAG + 引用守卫 + 可离线运行。

> 本仓库从零重写，不复制旧仓库代码；沿用「三通道检索 + RRF 融合 + 引用强绑定」的设计思路。

## 特性

### v0.1 — 单机可跑的全链路

- **多智能体编排**：LangGraph `StateGraph`，Supervisor → 拆解 → 并行子任务（`Send` fan-out）→ 汇总 → 引用守卫 → 终态。
- **Agentic RAG 三通道检索**：dense / BM25（k1=1.5, b=0.75）/ 词共现图一跳 → **RRF 融合**（k=60）→ 启发式重排 → 上下文压缩。
- **引用守卫（防幻觉）**：答案中的 `[n]` 强制绑定检索块，越界或无引用自动重试重写；低置信度拒答。
- **可观测**：事件全程落库（DB 即事件源），SSE 推送 `run.started / run.step / run.answer / run.trace / run.completed / run.finished`；断线按 run_id 从 DB 回放续传。
- **评估 CLI + API**：recall@K / mrr@K / faithfulness，JSONL 数据集。
- **零外部依赖模式**：`llm_provider=mock` + `embedding_provider=hash` + `vector_backend=local`，无 Key、无 Docker、无网络即可跑通。

### v0.2 — 换成生产组件，接口不变

- **Milvus 向量库适配器**：单 collection + `kb_id` 标量过滤；HNSW/COSINE（M=16, efConstruction=200）；`VectorStore` 协议与本地实现同签名，检索链路零改动。
- **Neo4j 知识图谱适配器**：`(:Chunk)-[:MENTIONS]->(:Term)` + `CO_OCCURS` 共现边；召回 = 直接命中 + 一跳扩展（权重 0.5），与本地词共现图同一套 tokenizer。
- **MinerU OCR**：扫描版 PDF 与图片走 `POST /file_parse` 取 markdown（保留标题/表格），未配置 OCR 时返回可操作的错误提示。
- **Java Spring Boot 网关**：API Key → 租户映射、固定窗口限流（429）、SSE 透传（不解析事件帧）、上传与知识库 CRUD 代理。
- **`/api/system/backends`**：一次看清当前进程实际连的是哪套 vector/graph/ocr/llm。

### v0.3 — 多租户、审计与两容器部署

- **租户隔离**：`X-Tenant-Id` 由网关注入（直连时前端可手填），落到 `knowledge_bases / runs / tool_invocations / eval_runs`；所有读写按租户过滤，跨租户访问一律 404（不泄露资源是否存在）。
- **租户头净化**：只允许 `[A-Za-z0-9_.-]`，非法值回落到 `local`；Python 与 Java 两侧同一套规则，防止把 header 拼进 Milvus filter / SQL。
- **审计日志**：`audit_logs` 追加写，覆盖 `kb.create / kb.delete / document.upload / document.delete / chat.ask / eval.run`；`GET /api/audit?limit=` 按租户倒序返回。
- **前向迁移**：`init_db()` 会对 v0.1/v0.2 的旧库自动 `ALTER TABLE ... ADD COLUMN tenant_id`，无需删库重建。
- **两容器部署**：`docker-compose.lite.yml` 只起 api + web（nginx 反代 `/api`，`proxy_buffering off` 保 SSE），零外部依赖，`http://localhost:8088` 即用。

## 快速开始

### 1. 后端（Python 3.11+）

```bash
cd apps/api
pip install -e .            # 或 uv sync
uvicorn app.main:app --port 8017
python -m app.seed          # 首次：灌入示例知识库与评估集
```

### 2. 前端（Node 20+）

```bash
cd apps/web
npm install
npm run dev                 # http://localhost:5173 ，/api 代理到 8017
```

生产构建后 FastAPI 自动挂载 `apps/web/dist`，访问 `http://localhost:8017/` 即为完整 UI。

### 3. 切到 Milvus / Neo4j（可选）

```bash
docker compose -f docker-compose.infra.yml up -d      # etcd + minio + milvus + neo4j
docker compose -f docker-compose.infra.yml --profile ocr up -d  # MinerU（多 GB 镜像）

cd apps/api
pip install -e ".[stores]"
NEXUS_VECTOR_BACKEND=milvus NEXUS_GRAPH_BACKEND=neo4j uvicorn app.main:app --port 8017
# 已有数据：重新解析入一次库即可（ingest 会把块推送到外部库）
curl -s localhost:8017/api/system/backends
```

### 4. Java 网关（可选，JDK 21 + Maven 3.9+）

```bash
cd apps/gateway
mvn spring-boot:run          # :8080，转发到 127.0.0.1:8017
# 开启鉴权：NEXUS_GATEWAY_API_KEYS 或 application.yml 的 nexus.gateway.api-keys
curl -N -X POST localhost:8080/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"RRF 怎么融合？","kb_id":"<kb_id>"}'
```

## 测试

```bash
cd apps/api && pytest        # 48 个用例，全部离线
cd apps/gateway && mvn test  # 7 个用例（限流器 + 鉴权/限流集成 + 租户头规范化）
```

## 架构

```
┌────────────────────────── Vue3 (apps/web) ──────────────────────────┐
│  KB 侧栏 · 聊天 SSE · 步骤/引用面板 · 检索 Trace · 上传 · 评估      │
└──────────────┬──────────────────────────────────────────────────────┘
               │ /api/*
┌──────────────▼──────────────┐        ┌──────────────────────────────┐
│ Spring Boot Gateway (可选)  │        │ 直连（开发默认）              │
│ API Key→租户 · 限流 · SSE   │───────▶│                              │
└──────────────┬──────────────┘        └──────────────┬───────────────┘
               └──────────────────┬───────────────────┘
┌─────────────────────────────────▼───────────────────────────────────┐
│  FastAPI (apps/api)                                                 │
│  knowledge CRUD/upload · chat/stream (SSE) · eval/runs · system     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ ingest: parse(±MinerU OCR) → chunk → embed → 落库 → 同步外部库 │  │
│  │ retriever: dense + BM25 + graph → RRF(k=60) → rerank → 压缩    │  │
│  │ agent: supervisor → plan → Send[] → worker → synthesize        │  │
│  │   └─ citation guard（越界重写/拒答）                           │  │
│  │ streaming: run_events 落库 + SSE 扇出 + DB 回放续传             │  │
│  └───────────────────────────────────────────────────────────────┘  │
│  stores: local (SQLite) ──or──▶ Milvus + Neo4j                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 目录

```
apps/
  api/                 # FastAPI 引擎（SQLite WAL，可换 PostgreSQL）
    app/
      agent/           # LangGraph 编排、LLM 双模、工具注册
      rag/             # chunk/embed/bm25/fusion/rerank/graph/retriever
        stores/        # VectorStore/GraphStore 协议 + local/milvus/neo4j
      guard/           # 引用守卫（防幻觉）
      streaming/       # 事件源 + SSE 回放
      eval/            # 指标 + CLI
      api/routes/      # health/knowledge/chat/eval/system
    tests/             # 42 个用例
  gateway/             # Spring Boot 3.4 边缘网关（Java 21）
  web/                 # Vue3 + Vite 前端
docker-compose.infra.yml   # etcd/minio/milvus/neo4j（+ MinerU profile）
docs/
  superpowers/specs/2026-11-13-nexusagent-mvp-design.md
```

## 路线图

- **v0.3**：多租户隔离 + 审计日志 + `docker-compose.lite`（引擎与依赖一键起）+ 检索评测回归基线。
