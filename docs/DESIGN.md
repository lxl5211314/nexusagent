# 设计说明

这份文档记录实现时的取舍，偏「为什么这么做」，不重复 README 的用法。

## 1. 数据模型

```
knowledge_bases ──< documents ──< chunks
runs ──< run_events
runs ──< tool_invocations
audit_logs
eval_runs
```

几个刻意的选择：

- **块是引用的最小单位。** `chunks.id` 同时出现在向量库主键、图谱节点属性、`citations[].chunk_id` 里。三处用同一个 id，引用校验才有意义。
- **`run_events` 带自增 `seq`。** SSE 断线重连时按 `seq` 补发，客户端不需要自己去重。
- **`tool_invocations` 单独建表**，不塞进事件里。工具调用的入参出参可能很大，事件流只保留摘要和耗时。
- **`audit_logs` 与业务同事务提交**，读接口只提供倒序查询，不提供改和删。

`tenant_id` 是后加的，`init_db()` 里做了前向迁移：检查列是否存在，缺了就 `ALTER TABLE`，旧数据默认归 `local`。测试里专门模拟了「旧库没有该列」的情况。

## 2. 检索

### 三路召回

| 通道 | 实现 | 擅长 |
|---|---|---|
| dense | 向量近邻（本地暴力 / Milvus HNSW-COSINE） | 语义相近但用词不同 |
| sparse | BM25（k1=1.5, b=0.75） | 型号、缩写、专有名词 |
| graph | 词共现图一跳 | 问题里只出现部分术语时补齐上下文 |

三路各自返回 `(chunk_id, score)`，分数不跨通道比较。

### 融合

```
score(chunk) = Σ_i  w_i / (k + rank_i)      k = 60, w = {dense:1.0, sparse:1.0, graph:0.5}
```

选 RRF 而不是加权求和，是因为三路分数量纲完全不同：余弦在 `[-1,1]`，BM25 可以到几十，图权重是计数值。归一化能解决量纲，但归一化参数会随语料漂移；RRF 只用排名，语料换了也不用调参。`k=60` 是原论文的经验值，作用是压低头部排名的差距。

图通道权重给 0.5，是因为词共现的召回偏宽，容易把「提到同一个词」但无关的块带进来。

### 重排与压缩

融合后的候选先按 `rerank_top_k=5` 截断，再用启发式重排：查询词覆盖率、标题命中、块长度惩罚。最后把相邻块拼接压缩，避免同一段话被切碎后重复占用上下文。

没有接 cross-encoder，原因是推理成本和多实例部署复杂度；接口留了位置，换实现不影响上层。

## 3. 编排

```
supervisor ──▶ route() ──▶ plan ──▶ dispatch ──┬─▶ worker(subtask 1) ─┐
                                                ├─▶ worker(subtask 2) ─┤
                                                └─▶ worker(subtask n) ─┤
                                                                       ▼
                                                              synthesize ──▶ finalize
```

- `route()` 是**纯函数**，只读 state，不写 state。LangGraph 只保留 state 里声明过的字段，想靠「在节点里塞一个 next」做路由会被静默丢弃。
- 扇出用 conditional edge 返回 `list[Send]`。节点返回 `Send` 会抛 `InvalidUpdateError`。
- `findings` 用 `add` reducer 累加，`contexts` 用去重合并；并行分支写同一个 key 也不会互相覆盖。
- `synthesize` 里做引用校验：解析 `[n]` → 对齐本轮 contexts → 越界或缺失则带约束重试一次 → 仍失败就拒答。

子任务拆解在 mock 模式下按连接词切分，在 openai 模式下走 function calling 返回 JSON schema；两条路径共用同一个 state 结构。

## 4. 流式

事件的产生和传输是分开的：

1. `EventWriter.emit()` 先写库（拿 `seq`），再 `bus.publish()` 给订阅者。
2. `GET /runs/{id}/stream` 先读进程内队列，超时再按 `seq > Last-Event-ID` 从库里补，读完且 run 已终态就关闭。

`Bus` 有两个实现：`LocalBus` 是进程内队列，`RedisBus` 把事件 `PUBLISH` 到 `nexus:run:{id}`。多实例时不要求 sticky session：连接落在哪台机器都能收到；即使某条 publish 丢了，回库补齐也会把缺口填上，因为真值在 `run_events` 表。

代价是每个空闲连接有一次轮询开销（默认 0.2s 间隔），问答场景可以接受。终态（`run.completed` / `run.finished` / 失败）一定落库，客户端据此停止重连。

## 5. 外部存储

`VectorStore` / `GraphStore` / `ObjectStore` 都是 Protocol，不是基类。local 实现包着进程内索引或文件目录，Milvus/Neo4j/S3 实现直连客户端，签名一致。

原始文件单独存一份：分块是派生数据，引用里点「看原文」要的是上传的那份字节。`Document.object_key` 记录位置，删文档时按前缀清对象；老库通过 `ALTER TABLE` 前向补列，不用重建。

几个实际踩过的坑，写在这里免得再犯：

- Milvus 集合被释放后状态是 `released`，直接 search 会报 `code=101 ... call load() before search`，重开集合后必须 `load_collection()`。
- Milvus 的 `filter` 是字符串拼接，租户值必须先过白名单再拼，否则 header 就是注入面。
- Neo4j 批量建边时 `combinations(...)[:n]` 会抛 `TypeError`（生成器不可下标），要先 `list()`。
- 删除 KB 或文档时，SQLite、向量库、图谱、对象存储四处都要清；只清 SQLite 会留下孤儿块，检索还会命中已删除的内容。图谱的粒度是块，删文档后按剩余块整体 rebuild 最简单。

## 6. 多租户与网关

- 前端直连时自己带 `X-Tenant-Id`；经过网关时由 `ApiKeyFilter` 从 key 映射租户并覆写该头。
- 两侧使用同一套白名单：`[A-Za-z0-9_.-]{1,32}`，首位必须是字母或数字，非法值回落 `local`。
- 资源查询统一走 `_get_owned()`，跨租户返回 404 而不是 403。
- 网关的 `ProxyController` 对每个上游请求显式设置租户头。`WebClient` 不会自动透传 exchange 的 header，漏写就会静默变成默认租户，隔离看起来生效、实际没有。

## 7. MCP 与 Skill

- 传输只有两种：stdio（子进程，换行分隔的 JSON-RPC）和 streamable HTTP（POST，响应可能是 `text/event-stream`，从 `data:` 帧里取 JSON-RPC）。`initialize` 之后发 `notifications/initialized`，再走 `tools/list` / `tools/call`。
- 连接是懒的，进程启动不 spawn；某个 server 抛错只从目录里移除它自己，`errors` 保留原因，其它 server 继续可用。
- 只读判定：`readOnlyHint` → `destructiveHint` → 动词启发式。注解优先，因为 server 最清楚自己的副作用；启发式只兜住没写注解的 server。
- 工具名统一为 `mcp.<server>.<tool>`，注册进同一个 `ToolRegistry`，权限码 `mcp:<server>` / `mcp:<server>:write`，写工具必须带审批令牌。
- Skill 是磁盘上的 `<name>/SKILL.md`。列表接口只返回 front matter，正文按名字单独取，避免把全部提示词一次性塞进上下文。
- 当前只在 `plan` 节点做一次发现（进程级幂等）。要做工具级懒加载，需要把目录按子任务相关度过滤后再注入，那是下一步。

## 8. 评估

- `recall@k`：gold 片段是否出现在前 k 个块里，按 gold 数量取比例。
- `mrr@k`：第一个命中块的倒数排名。
- `faithfulness`：答案句子被引用块覆盖的比例（词面），后续可换 LLM judge，函数签名不变。

样例集是 27 题 / 3 篇文档，用来防止改动把链路改坏，不代表真实效果。扩语料后应该按领域分组统计，而不是只看一个总数。

## 8. 外部工具的接入点

知识库答不了的问题（命中片段少于 2 个）才轮到外部工具。选择器按问题与工具名/描述的词面覆盖度打分，只考虑只读且必填参数都有默认值的工具——参数拼不出来就放弃，不拿空参数去撞上游。

调用同样经过 `ToolRegistry`，因此权限、审计、`tool.call` 事件和内置工具完全一致。手工调用走 `POST /api/mcp/call`，会写 `mcp.call` 审计；写工具仍然要 `approval_token`。

`ensure_loaded()` 只在确实发现了 server 之后才置位，否则一次「配置还没加载」的空跑会让整个进程永远不注册 MCP 工具。

外部证据的编号：工具返回正文后，worker 追加一个 `RetrievedChunk`（`chunk_id=mcp.<server>.<tool>`、`heading=外部工具`、`scores={"rerank":0.25,"external":1.0}`）到 contexts 尾部。这样引用校验不需要为外部源开特例——它天然落在 `1..len(contexts)` 内，UI 也能按 doc_name 显示来源。0.25 的 rerank 分让它对置信度的贡献有上限，不会因为「调了个工具」就把拒答阈值顶穿。

两个开关落在 `app/core/runtime.py`：`mcp_tool_router` 控制要不要调，`mcp_cite_external` 控制结果要不要进来源。只有白名单里的键可改，写入 `data/runtime.json`，启动时 `apply_overrides()` 回灌到 settings 单例，避免为一个布尔值重启服务。

引用抽屉：`check_answer` 里只带 `snippet`，而 snippet 是压缩后的句子集合——读者点开时想核对的是原文，不是压缩结果。所以前端点引用后单独打 `GET /api/kb/{kb}/chunks/{chunk_id}` 取整块内容。接口按 `kb_id` 校验租户属主，跨租户一律 404；外部工具来源（`chunk_id` 形如 `mcp.server.tool`）没有对应行，直接用 citation 自带的正文。
