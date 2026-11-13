# NexusAgent 企业知识智能体平台

> 面向企业私有知识库的多智能体问答平台。简历个人项目，从零重建（MVP）。

## 版本路线

| 版本 | 内容 | 状态 |
|------|------|------|
| v0.1 | MVP：LangGraph 编排 + 三通道 RAG + RRF + 引用守卫 + SSE 事件源 + 评估 | ✅ 完成 |
| v0.2 | Milvus 适配器、Neo4j 适配器、MinerU OCR、Java Spring Boot 网关 | ✅ 完成 |
| v0.3 | 多租户隔离、审计日志、两容器部署（docker-compose.lite） | ✅ 完成 |

## v0.1 验收判据（全部通过）

1. `GET /health` → `{"status":"ok"}`
2. 上传文档 → 解析 → 分块 → 嵌入 → 建索引（36 块/示例）
3. `POST /api/chat/stream` SSE 四段事件：`run.started → run.step → run.answer → run.completed → run.finished`
4. 引用越界/无引用 → 自动重写；低置信度 → 拒答
5. 断线续传：`GET /api/runs/{id}/stream` 从 DB 回放全序事件
6. 评估：recall@5=1.0 / mrr@5=0.917 / faithfulness=0.667
7. `pytest` 31 用例全绿

## v0.2 验收判据（全部通过）

1. 后端 **42** 用例全绿（新增 stores/OCR/工厂/健康路由 11 例），`mvn test` **6** 例全绿
2. `GET /api/system/backends` → `vector/graph/ocr/llm/embedding` 实时状态
3. `NEXUS_VECTOR_BACKEND=milvus` → 工厂构造 `MilvusVectorStore`；检索走 HNSW/COSINE + `kb_id` 标量过滤
4. `NEXUS_GRAPH_BACKEND=neo4j` → 召回 = 直接命中 + 一跳共现扩展（权重 0.5）
5. 扫描 PDF/图片：`NEXUS_OCR_BACKEND=mineru` 调 `/file_parse` 取 markdown；未开启时错误文案直接给出开关名
6. 网关：缺 Key → 401；超过每分钟配额 → 429；`/health`、`/actuator`、`/api/v1/health` 免鉴权
7. 网关 SSE 实测透传：`POST :8080/api/v1/chat/stream` → `step.started / tool.call / tool.result / token / run.completed`

## v0.3 验收判据（全部通过）

1. 后端 **48** 用例全绿（新增租户/审计/迁移 6 例），`mvn test` **7** 例全绿
2. `POST /api/kb` 带 `X-Tenant-Id: acme` → 仅 `acme` 的 `GET /api/kb` 可见；`globex` 列表为空、按 id 直取 404
3. 上传、提问、评估均写入 `audit_logs`，`GET /api/audit` 按租户倒序返回，跨租户为空
4. `X-Tenant-Id` 非法值（空、含空格/引号/超长）回落 `local`，Python 与 Java 两侧规则一致
5. 旧库（无 `tenant_id` 列）启动时自动补列，`init_db()` 可重复执行
6. `docker-compose.lite.yml` 两容器起服务，nginx 反代 `/api` 且 `proxy_buffering off`，SSE 不粘包
7. 网关在开发模式（无 API Key）下透传调用方 `X-Tenant-Id`，不再硬编码 `local`

## 关键设计取舍

- **DB 即事件源**：`run_events` 落库 → SSE 回放。免 sticky session，多实例一致。
- **LLM 双模**：`mock`（确定性抽取式，离线可测）/ `openai`（OpenAI 兼容）。嵌入同理：`hash` / `openai`。
- **三通道检索**：dense + BM25 + 词共现图一跳 → RRF(k=60) → 启发式重排 → 上下文压缩。
- **工具注册三件套**：input/output schema + permission + side_effect。只读默认，写操作需审批令牌（对齐用友工具注册规范）。
- **引用守卫**：`[n]` 强制绑定检索块；越界或无引用 → 重试重写（`citation_max_retry=1`）；置信度 < 0.35 → 拒答。
- **存储按协议而非实现分支**：`VectorStore` / `GraphStore` 两个 Protocol，`local` 与 `milvus`/`neo4j` 同签名；检索层只依赖协议，切库只改环境变量。
- **图谱召回双实现同构**：本地词共现图与 Neo4j 用同一个 tokenizer、同样的「直接命中 + 一跳 × 0.5」，避免换库后排序漂移。
- **OCR 是解析器而不是链路**：MinerU 只在 `parse()` 内部兜底（扫描 PDF/图片），产出仍是 markdown，下游分块/嵌入不变。
- **跨租户一律 404**：不用 403。403 等于确认「这个 id 存在，只是不属于你」，本身就是越权信息。
- **审计与业务同事务**：审计行和业务行共用一次 commit，避免「操作成功但日志丢了」；读接口只读，不提供改/删。
- **租户头只给字符白名单**：Milvus 的 `filter` 是字符串拼接，租户值必须先净化再入参，否则 header 就是注入面。
- **迁移只向前**：`init_db()` 只补 `tenant_id` 列并建索引，不做数据搬迁；旧数据默认归 `local`。

## 运行

```bash
cd apps/api && pip install -e . && python -m app.seed && uvicorn app.main:app --port 8017
cd apps/web && npm install && npm run dev
# 网关（可选）
cd apps/gateway && mvn spring-boot:run
```

## 测试

```bash
cd apps/api && pytest          # 42 passed
cd apps/gateway && mvn test    # 6 passed
```

## 外部依赖（v0.2）

```bash
docker compose -f docker-compose.infra.yml up -d           # milvus + neo4j
docker compose -f docker-compose.infra.yml --profile ocr up -d  # MinerU
```

## 参考

- 设计文档：`docs/superpowers/specs/2026-11-13-nexusagent-mvp-design.md`
