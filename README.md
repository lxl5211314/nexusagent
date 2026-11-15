# NexusAgent

[![ci](https://github.com/lxl5211314/nexusagent/actions/workflows/ci.yml/badge.svg)](https://github.com/lxl5211314/nexusagent/actions/workflows/ci.yml)

面向企业私有知识库的多智能体问答平台。答案强制绑定原文块，检索不到就拒答，模型与向量库都可以换。

![NexusAgent 界面](docs/images/ui.png)

## 为什么做这个

企业里的制度、规范、产品文档和 FAQ 散在 OA、网盘、Confluence、邮件里，员工找不到，只能问人。把大模型直接接上去看着能用，但有两个问题绕不过去：

1. **答案没有出处。** 员工无法判断这句话是文档里的，还是模型编的。企业场景里，依据错误答案做决策的代价很高。
2. **检索不到时它照样回答。** 模型不会说「我不知道」，它会顺着问题编一段看起来合理的话。

所以这个项目的目标不是「让模型答得更像人」，而是**让每一句回答都能指回一个具体的文档块**；指不回去，就重写或者拒答。

四个硬约束决定了后面的设计：

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
- 入库 2 块 → 向量库 2 行、图谱 2 块 / 5 个实体 / 1 条关系
- 三路召回命中 2 条，分数里能看到 `dense / sparse / graph / rrf / rerank` 各自的值
- 删库后两个外部库都归零（`scripts/live_cleanup.py` 断言）

测试：后端 110 个用例（含 fakeredis 驱动的跨实例事件总线、运行状态缓存、分片上传和可选的真实存储联调），网关 11 个用例，全部离线可跑。

## 知识图谱里存的是什么

从文档块抽的是**实体和带类型的关系**，不是词频共现：

```
(:Chunk)-[:MENTIONS]->(:Entity {name, type})
(:Entity)-[:REL {type}]->(:Entity)
```

关系来自两类抽取器，同一套接口（`app/rag/kg.py`）：

- **规则**：按连接词切句（`是一种 / 依赖于 / 基于 / 包括 / 负责 / 调用 / 写入 / 存储 / 部署`…），左边取末段当 head、右边取首段当 tail，两侧都做长度、停用词和字符集过滤。优点是零成本、可解释；缺点是句子必须显式说出关系。
- **模型**：`NEXUS_GRAPH_EXTRACTOR=llm` 时走 OpenAI 兼容接口，要求只返回 `{"triples":[…]}`，解析失败或返回空就回落规则——图不能因为一次模型抖动变成空的。

另外补了一条 `detect_entities`：中文按「后缀词」识别（`…数据库 / …服务 / …平台 / …系统`），英文按首字母大写识别。没有它，「RRF 融合三路结果」这种没有连接词的句子一个实体都抽不出来，图谱就等于白建。

检索时实体命中直接加 1.0，一跳邻居加 0.5，和向量、BM25 两路一起送进 RRF。样例库上有 44 个实体、7 条关系；问「RRF 是怎么融合三路结果的」能命中 2 个块，纯词共现版本这里是 0。

## 角色与权限

网关解析出身份后，引擎按权限码校验，不比较角色名：

| 角色 | 能做什么 |
| --- | --- |
| `admin` | 读写知识库与文档、调 MCP 工具、改运行时设置、看审计 |
| `editor` | 读写知识库与文档、调 MCP 工具 |
| `viewer` | 只读知识库与文档 |

没有 `X-User-Role` 的请求按 `viewer` 处理（故障关闭）；未知角色同样降到 `viewer`。跨租户访问一律 404 而不是 403，避免通过状态码探测别的租户有没有这个知识库。审计行记录 `actor`，能回答「谁在什么时候传了哪份文件」。

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

### 换成 BGE 嵌入

默认的 `hash` 嵌入是为了离线可跑，不是效果方案。接真模型只要改环境变量——任何 OpenAI 兼容的 `/embeddings` 都行：

```bash
NEXUS_EMBEDDING_PROVIDER=openai
NEXUS_EMBEDDING_MODEL=BAAI/bge-m3
NEXUS_EMBEDDING_DIM=1024          # 必须和模型输出一致；换维度要重建集合并重新入库
NEXUS_LLM_BASE_URL=https://api.siliconflow.cn/v1
NEXUS_LLM_API_KEY=sk-...
```

维度写错的典型症状是入库时报 `vector dimension mismatch`：Milvus 集合在第一次创建时就固定了 `dim`，改维度需要 `delete_collection` 后重建。维度不一致的库混用没有意义，所以这里不做自动迁移。

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

网关做四件事，都是引擎侧不该重复的：

- **认证与身份**。`X-API-Key` 映射到租户；`key-roles` 再映射到 `admin/editor/viewer`，两个头一起下发给引擎，引擎只认权限码，不自己解析 key。没配 key 是开发模式，直接沿用调用方的租户与角色。
- **限流**。按租户的令牌桶，不是按 key——换 key 不重置额度。默认每实例一个桶；`NEXUS_GATEWAY_REDIS_URL` 指向 Redis 时改用 Lua 脚本统一扣减，多实例共享同一份额度，Redis 不可用会降级回本地桶而不是拒绝服务。
- **熔断**。上游连续失败到阈值（默认 20 次窗口里 50%）后开路 20 秒，期间直接 503，避免引擎被打穿后每个请求都等满超时。半开只放两个请求试探。
- **可观测**。每个请求一个 trace id（沿用 `X-Trace-Id` 或现生成），写进 MDC、回写响应头，并随转发头传给引擎；`/actuator/prometheus` 暴露 `gateway.request` 时延直方图与拒绝计数，按路由归一化，不让 id 把基数撑爆。

## 分片上传与断点续传

大文件不必一次 POST 完。客户端先开会话，再按 5 MB 分片 PUT，断线后 GET 一次就能知道哪几片已经落库：

```bash
UPLOAD=$(curl -s -X POST localhost:8017/api/kb/$KB/uploads \
  -H 'Content-Type: application/json' -d '{"filename":"manual.pdf"}' | jq -r .upload_id)

# 每片单独 PUT，顺序无所谓，失败只重传这一片
curl -X PUT localhost:8017/api/kb/$KB/uploads/$UPLOAD/parts/1 \
  --data-binary @part-1.bin

# 断线重连后先问：哪些片已经在对象存储里
curl localhost:8017/api/kb/$KB/uploads/$UPLOAD | jq '.uploaded, .bytes_received'

# 片齐了再合并并入库；未 complete 之前不会产生文档
curl -X POST localhost:8017/api/kb/$KB/uploads/$UPLOAD/complete \
  -H 'Content-Type: application/json' -d '{"parts":[1,2,3]}'
```

分片状态在对象存储（S3/MinIO 的 multipart，本地模式落在 `.uploads/`），进程重启也能续传。`complete` 才解析、切块、入索引，失败会清掉暂存前缀，不会留半个文档。

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

Skill 是提示词侧的能力：一个目录一份 `SKILL.md`，front matter 写 `name` / `description`，正文是步骤。启动时只读卡片（`name` + `description` 建目录），子任务来了才按描述词面匹配挑最多 2 个，挑中之后才读正文进上下文。

工具同理：外部 server 可能有几百个工具，全部塞给模型既贵又乱。worker 先按子任务问句打分，只把最相关的 6 个（可配 `NEXUS_MAX_TOOLS_PER_TASK`）放进这一轮的调色板，`ToolRegistry.scope()` 返回一个只暴露这几个工具的视图——注册表还是全局的，模型看到的不是。加载了哪些、为什么选它，事件流里有一条 `step.started` 带着工具名，前端能看见。

## 目录

```
apps/api      FastAPI 引擎
  app/agent       LangGraph 编排、LLM 双模、工具注册
  app/mcp         MCP 客户端（stdio / streamable HTTP）与运行时发现
  app/rag         解析/分块/嵌入/BM25/RRF/重排/图谱/检索
    stores/       VectorStore / GraphStore 协议与 local/milvus/neo4j 实现
  app/storage     原件存储（本地 / S3+MinIO，含分片上传）
  app/cache       运行状态与最近对话缓存（进程内 / Redis）
  app/guard       引用守卫
  app/streaming   事件落库 + SSE 回放
  app/eval        指标与 CLI
apps/gateway  Spring Boot 边缘网关（Java 21）
apps/web      Vue3 + Vite
tools         neo4j-embedded（本地 Bolt，联调用）
```

## 已知限制

写在这里是因为它们确实存在，不是待办清单：

- 实体关系抽取的规则版只认「句子把关系说出口」的写法，模型版依赖外部接口；两者都不做实体消歧（「订单服务」和「订单系统」会是两个节点）。
- 重排是启发式的（词面覆盖 + 位置），没有接 cross-encoder。
- 默认 SQLite 单机；多实例部署需要换 PostgreSQL，并保证 `run_events` 共享。
- 扫描件依赖外部 OCR 服务，仓库里不带模型权重。
- 评估集规模小，只能证明链路没坏，证明不了效果上限。
- 前端没有单测，也没做过并发压测；性能数字目前只有 SQL 调优那条从生产带回来的。

## 关于这个仓库

这个仓库是整理后的版本：需求、取舍、测试和文档都在同一处，能跑的东西都跑过一遍才提交。早期探索时的原型、废弃分支和内部环境的配置没有带进来，所以提交历史不完整——这解释的是历史，不是设计：每个决定为什么这么做，都在上表和 `docs/DESIGN.md` 里，可以逐条对着代码看。

如果你只想验证它是不是能跑，三步：`pip install -e ".[stores,events]" pytest pytest-asyncio fakeredis` → `python -m app.seed` → `pytest`；再开 `uvicorn app.main:app` 和 `npm run dev` 就能看到界面，不需要 Docker、不需要任何 Key。

## License

MIT
