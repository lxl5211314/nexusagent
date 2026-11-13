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

1. `EventWriter.emit()` 先写库（拿 `seq`），再推给当前连接的订阅者。
2. `GET /runs/{id}/stream` 不依赖内存状态，直接按 `seq > Last-Event-ID` 轮询数据库，读完且 run 已终态就关闭。

好处是多实例下不需要 sticky session：客户端重连到任何一台机器，都能从库里补齐。代价是每个连接有一次轮询开销（默认 0.2s 间隔），对问答场景可以接受。

终态（`run.completed` / `run.finished` / 失败）一定会落库，客户端据此判断是否可以停止重连。

## 5. 外部存储

`VectorStore` / `GraphStore` 是 Protocol，不是基类。local 实现包着进程内索引，Milvus/Neo4j 实现直连客户端，签名一致。

几个实际踩过的坑，写在这里免得再犯：

- Milvus 集合被释放后状态是 `released`，直接 search 会报 `code=101 ... call load() before search`，重开集合后必须 `load_collection()`。
- Milvus 的 `filter` 是字符串拼接，租户值必须先过白名单再拼，否则 header 就是注入面。
- Neo4j 批量建边时 `combinations(...)[:n]` 会抛 `TypeError`（生成器不可下标），要先 `list()`。
- 删除 KB 或文档时，SQLite、向量库、图谱三处都要清；只清 SQLite 会留下孤儿块，检索还会命中已删除的内容。图谱的粒度是块，删文档后按剩余块整体 rebuild 最简单。

## 6. 多租户与网关

- 前端直连时自己带 `X-Tenant-Id`；经过网关时由 `ApiKeyFilter` 从 key 映射租户并覆写该头。
- 两侧使用同一套白名单：`[A-Za-z0-9_.-]{1,32}`，首位必须是字母或数字，非法值回落 `local`。
- 资源查询统一走 `_get_owned()`，跨租户返回 404 而不是 403。
- 网关的 `ProxyController` 对每个上游请求显式设置租户头。`WebClient` 不会自动透传 exchange 的 header，漏写就会静默变成默认租户，隔离看起来生效、实际没有。

## 7. 评估

- `recall@k`：gold 片段是否出现在前 k 个块里，按 gold 数量取比例。
- `mrr@k`：第一个命中块的倒数排名。
- `faithfulness`：答案句子被引用块覆盖的比例（词面），后续可换 LLM judge，函数签名不变。

样例集是 27 题 / 3 篇文档，用来防止改动把链路改坏，不代表真实效果。扩语料后应该按领域分组统计，而不是只看一个总数。
