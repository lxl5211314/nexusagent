# NexusAgent

面向企业私有知识库的多智能体问答平台。答案强制绑定原文块，检索不到就拒答，模型与向量库都可以换。

![NexusAgent 界面](docs/images/ui.png)

## 为什么做这个

企业里的制度、规范、产品文档和 FAQ 散在 OA、网盘、Confluence、邮件里，员工找不到，只能问人。把大模型直接接上去看着能用，但有两个问题绕不过去：

1. **答案没有出处。** 员工无法判断这句话是文档里的，还是模型编的。企业场景里，依据错误答案做决策的代价很高。
2. **检索不到时它照样回答。** 模型不会说「我不知道」，它会顺着问题编一段看起来合理的话。

所以这个项目的目标不是「让模型答得更像人」，而是**让每一句回答都能指回一个具体的文档块**；指不回去，就重写或者拒答。

三个硬约束决定了后面的设计：

- 引用必须落到块级，而不是文档级 —— 否则「出处」没有校验价值。
- 引用要能点开看到原文 —— 只给文件名，读者还是得自己去翻；点开直接看到被引用的那一块，才谈得上核对。
- 检索链路要可替换 —— 客户环境里 Milvus、Neo4j 有没有、什么版本都不确定。
- 私有化部署 —— 不能假设一定连得上公有云 API，所以模型层做了 mock/OpenAI 兼容双模。

## 整体结构

```
Vue3 (apps/web)
  └─ /api/* ──▶ Spring Boot 网关（可选）
                 API Key → 租户 · 固定窗口限流 · SSE 透传
                        │
                        ▼
                 FastAPI 引擎 (apps/api)
                 ├─ ingest   parse(±MinerU/PaddleOCR) → chunk → embed → 落库 → 同步外部库
                 │            └─ 原件留档：本地目录 ──or──▶ MinIO/S3
                 ├─ retrieve dense + BM25 + 图谱 → RRF(k=60) → 重排 → 压缩
                 ├─ agent    supervisor → plan → Send[] 并行 → worker → synthesize
                 │            └─ 引用守卫：越界重写 / 低置信度拒答
                 ├─ stream   run_events 落库 + SSE 扇出 + 断线回放
                 └─ stores   local(SQLite) ──or──▶ Milvus + Neo4j
```

## 几个关键决定

| 问题 | 做法 | 为什么 |
|---|---|---|
| 子任务怎么并行 | LangGraph `StateGraph`，conditional edge 返回 `Send` 列表扇出 | 子任务数量是运行时才知道的，普通 edge 写死分支做不到；`Send` 是官方给的动态扇出方式 |
| 三路召回怎么合 | 各自排名后用 RRF（`Σ w/(k+rank)`，k=60）融合 | 三路分数量纲不同（余弦、BM25、图权重），直接相加会被某一路支配；RRF 只看排名，天然可比 |
| 引用怎么校验 | 解析答案里的 `[n]`，逐个对齐本轮检索到的块；越界或缺失就带着约束重写一次 | 只靠 prompt 要求「请引用」不可靠，必须在校验层兜底 |
| 检索不到怎么办 | 置信度低于 0.35 直接拒答 | 拒答是正确答案的一种；硬答一次，用户就会开始怀疑所有回答 |
| 流式怎么保证不丢 | 事件先写 `run_events` 再推 SSE；断线按 `Last-Event-ID` 从库里回放 | 只靠内存队列，进程重启或换实例就丢事件；落库后 SSE 变成「读库的投影」 |
| 换向量库要改多少 | `VectorStore` / `GraphStore` 两个 Protocol，local 与 Milvus/Neo4j 同签名 | 检索层只依赖协议，切库只改环境变量，不动业务代码 |
| 租户怎么隔离 | 网关注入 `X-Tenant-Id`，服务端白名单净化后落到四张表，跨租户一律 404 | 403 等于告诉对方「这个 id 存在，只是不属于你」，本身就是信息泄露 |
| 审计会不会丢 | 审计行和业务行同一次 commit | 先写业务再写日志，中间崩了就会出现「操作成功但没记录」 |
| 原件要不要留 | 分块是派生数据，原始字节单独存（本地目录或 MinIO） | 用户在引用上点「看原文」时要的是原文件；只留分块，等于把证据丢了 |

## 实测

样例集 27 个问题（覆盖 3 篇示例文档），`python -m app.eval.cli`：

```
recall@5 = 1.00    mrr@5 = 0.92    faithfulness = 0.81    zero_hit = 0
```

这是回归用的冒烟集，不是效果基准：语料只有 3 篇文档，难度远低于真实企业库。

真实后端（不是内存桩）也跑过一遍：

- Milvus Lite（同一个 gRPC 协议）+ Neo4j 进程内 Bolt
- 入库 2 块 → 向量库 2 行、图谱 2 块 / 25 节点 / 80 条共现边
- 三路召回命中 2 条，分数里能看到 `dense / sparse / graph / rrf / rerank` 各自的值
- 删库后两个外部库都归零（`scripts/live_cleanup.py` 断言）

测试：后端 87 个用例（含 fakeredis 驱动的跨实例事件总线、运行状态缓存和可选的真实存储联调），网关 7 个用例，全部离线可跑。

## 快速开始

不需要 Docker，不需要任何 API Key：

```bash
cd apps/api
pip install -e .
python -m app.seed            # 灌入示例知识库
uvicorn app.main:app --port 8017

cd ../web
npm install && npm run build  # 构建后由 FastAPI 直接托管
```

打开 `http://127.0.0.1:8017/`。想在命令行里看链路：

```bash
curl -N -X POST localhost:8017/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"kb_id":"<kb_id>","question":"RRF 的 k 取多少？"}'
```

## 换成 Milvus / Neo4j

```bash
docker compose -f docker-compose.infra.yml up -d     # etcd + minio + milvus + neo4j

cd apps/api
pip install -e ".[stores]"
NEXUS_VECTOR_BACKEND=milvus NEXUS_GRAPH_BACKEND=neo4j uvicorn app.main:app --port 8017
curl -s localhost:8017/api/system/backends           # 确认真的连上了
```

没有容器环境也能验证：`pip install -e ".[stores-lite]"` 用 Milvus Lite，`tools/neo4j-embedded` 用 `neo4j-harness` 起进程内 Bolt，跑 `python -m scripts.live_check`。

## 原件与扫描件

上传的原始文件默认落在 `data/uploads`，也可以直接丢进 MinIO：`pip install -e ".[objects]"`，然后设 `NEXUS_OBJECT_BACKEND=s3` 与 `NEXUS_S3_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET`（compose 里的 minio 就是现成的）。`GET /api/kb/{id}/documents/{doc_id}/content` 取回原件，删文档时连同对象一起清。

扫描件（PDF 无文字层、图片）交给 OCR：

```bash
NEXUS_OCR_BACKEND=mineru NEXUS_MINERU_BASE_URL=http://127.0.0.1:8888
# 或 PP-StructureV3 serving：
NEXUS_OCR_BACKEND=paddle NEXUS_PADDLE_BASE_URL=http://127.0.0.1:8080
```

两个后端都返回 markdown 再进分块器，所以表格和标题不会退化成一行行散字。

## 多实例与 PostgreSQL

单进程默认用 SQLite + 内存队列，开箱即用。要多实例部署时：

```bash
docker compose -f docker-compose.infra.yml up -d redis postgres
pip install -e ".[events,postgres]"

NEXUS_DATABASE_URL=postgresql+psycopg://nexus:nexus@127.0.0.1:5432/nexus \
NEXUS_REDIS_URL=redis://127.0.0.1:6379/0 \
uvicorn app.main:app --workers 4
```

开 Redis 后，写事件的一侧 `PUBLISH nexus:run:{id}`，任意实例上的 SSE 连接都能收到；队列只是降低延迟，事件真值仍在 `run_events` 表，漏掉的部分按 `seq` 从库里补。`tests/test_bus.py` 用 fakeredis 的进程内 server 验证跨实例收发，不需要装 Redis。

同一份 Redis 还存两类热数据：

- `nexus:run:{id}:state` —— 运行状态快照（状态、序号、答案、置信度、拒答标记，TTL 1 小时）。`GET /api/runs/{id}` 先读它，未命中再回表并把结果写回，前端轮询不必每次查库。
- `nexus:chat:{tenant}:recent` —— 租户最近 50 轮问答，`GET /api/chat/history` 直接读它，界面上「最近对话」就是这块。

两者的真值都在 SQLite/Postgres：缓存写失败只记一条 warning，读不到就回表，所以 Redis 挂掉是降级而不是故障（`tests/test_cache.py` 覆盖了不可达时读穿透到 DB 的路径）。

## 网关（可选）

```bash
cd apps/gateway
mvn spring-boot:run          # :8080，转发到 127.0.0.1:8017
```

API Key 存在时按 key 映射租户，不存在时是开发模式（沿用调用方的 `X-Tenant-Id`）。限流按租户做 60 秒固定窗口。

## 扩展：MCP 与 Skill

工具分两类。内置的 `kb.search` / `kg.expand` / `calc.eval` 在进程内；外部能力走 MCP，运行时发现，不写死在代码里。

![工具目录](docs/images/tools.png)

`mcp.servers.json` 的格式和常见 MCP 宿主一致，从别的客户端拷一份就能用：

```json
{
  "servers": {
    "filesystem": {"type": "local", "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data/shared"]},
    "github": {"type": "remote", "url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer ${GITHUB_MCP_TOKEN}"}}
  }
}
```

```bash
export NEXUS_MCP_CONFIG=$PWD/apps/api/mcp.servers.json
python -m uvicorn app.main:app --port 8017
curl localhost:8017/api/mcp/tools | jq '.items[] | {server, name, side_effect}'
```

几个实现上的决定：

- **懒连接**。进程启动不拉起任何子进程；第一次 `tools/list` 才 spawn，某个 server 挂了只丢它自己的条目，`errors` 里能看到原因，其它 server 照常工作。
- **只读判断三档**。优先信 server 的 `annotations.readOnlyHint`；没有就 `destructiveHint`；再没有按动词猜（`get/list/read/search` 算读）。猜不准时默认读，但写工具一律要 `approval_token` 才放行。
- **两种传输**。stdio 用换行分隔的 JSON-RPC（子进程 stdin/stdout），remote 用 streamable HTTP，响应是 SSE 时从 `data:` 帧里取。
- **同一套门禁**。MCP 工具注册进 `ToolRegistry` 后，权限码是 `mcp:<server>`（写是 `mcp:<server>:write`），和内置工具走同一个 `invoke`。
- **外部工具只在检索不够时插一脚**：子任务命中的片段少于 2 个，才按词面覆盖挑一个只读工具补一次外部信息；参数只填有默认值的，必填项没默认就跳过，宁可不用也不瞎调。调用一样走 `ToolRegistry`，事件里能看到 `tool.call` 和耗时。
- **手点也一样留痕**：`POST /api/mcp/call` 无论成功、被门禁拦下还是上游报错，都会写一条 `mcp.call` 审计，带工具名、参数键和结果摘要。
- **外部证据也能被引用**：工具返回的内容作为第 n+1 条来源进入上下文，和文档块一起编号，引用校验照样逐条回查——不是「模型说调过工具」就算数。开关在侧栏「设置」里，`GET/PUT /api/settings` 同步生效，重启后仍保留。

Skill 是提示词侧的能力：一个目录一份 `SKILL.md`，front matter 写 `name` / `description`，正文是步骤。`GET /api/skills` 只列卡片，`GET /api/skills/{name}` 才返回正文——按需加载，不把全部提示词塞进上下文。

## 目录

```
apps/api      FastAPI 引擎
  app/agent       LangGraph 编排、LLM 双模、工具注册
  app/mcp         MCP 客户端（stdio / streamable HTTP）与运行时发现
  app/rag         解析/分块/嵌入/BM25/RRF/重排/图谱/检索
    stores/       VectorStore / GraphStore 协议与 local/milvus/neo4j 实现
  app/guard       引用守卫
  app/streaming   事件落库 + SSE 回放
  app/eval        指标与 CLI
apps/gateway  Spring Boot 边缘网关（Java 21）
apps/web      Vue3 + Vite
tools         neo4j-embedded（本地 Bolt，联调用）
```

## 已知限制

写在这里是因为它们确实存在，不是待办清单：

- 图谱召回用的是**词共现**，不是实体关系抽取；一跳之外不扩展。
- 重排是启发式的（词面覆盖 + 位置），没有接 cross-encoder。
- 默认 SQLite 单机；多实例部署需要换 PostgreSQL，并保证 `run_events` 共享。
- 扫描件依赖外部 OCR 服务，仓库里不带模型权重。
- 评估集规模小，只能证明链路没坏，证明不了效果上限。

## License

MIT
