# DD-05 Hybrid Retrieval 详细设计

## 1. 目标与边界

在已有向量/关键词/混合 DocumentChunk 检索基础上，补齐 **Graph-like Retrieval**、**Structured SQL Retrieval**、**Time-series Retrieval** 与 **Query Planner**，使研究 Agent 能根据用户问题自动选择检索后端，并返回带血缘和审计轨迹的证据。

覆盖：FR-004、FR-005、RAG-001～RAG-005、NFR-002。

本设计不引入 Neo4j/Milvus/ClickHouse 等新基础设施，先在 PostgreSQL 关系模型上用 SQL 实现 Graph-like 与 Structured 召回，为后续存储选型留下可替换接口。

## 2. 设计基线

- `RetrievalRequest` 是跨后端统一请求契约；新增 `retrieval_mode` 取值 `planned`。
- `QueryPlanner` 只负责**计划生成**，不直接访问外部存储；计划受 Schema 约束，可被审计和重放。
- 各后端返回统一 `RetrievedItem`，`backend` 字段标识来源；Graph/SQL/Time-series 后端在 `text` 中放置结构化摘要，在 `citation.locator` 中保存下钻所需 id。
- `RetrievalService` 是编排入口：按模式选择 vector/lexical/hybrid/graph/sql/timeseries/planned 路径，负责后处理与 `RetrievalTrace` 生成。
- 真实 LLM 仅用于可选的意图/实体增强；核心召回必须可解释、可复现、受 `as_of` 约束。

## 3. 组件划分

| 组件 | 职责 | 现有文件 |
| --- | --- | --- |
| `RetrievalService` | 入口编排、过滤、轨迹组装 | `app/retrieval/service.py` |
| `FusionService` | 多路结果 RRF/weighted 融合 | `app/retrieval/fusion.py` |
| `QueryPlanner` | 解析查询意图、实体、时间、事件类型，生成 `RetrievalPlan` | `app/retrieval/planner.py`（新增） |
| `GraphRetrieval` | 基于 Event/Entity/Claim/Document 关系做关联召回 | `app/retrieval/service.py` 内方法（新增） |
| `StructuredRetrieval` | 按实体、事件类型、时间范围查询事件/事实卡片/Claim | `app/retrieval/service.py` 内方法（新增） |
| `TimeSeriesRetrieval` | 按时间窗召回事件/公告，支持事件发生频率排序 | `app/retrieval/service.py` 内方法（新增） |
| `Retrieval API` | 暴露 `POST /api/v1/retrieval/retrieve` | `app/api/routes.py`（新增） |

## 4. 检索后端

### 4.1 Vector / Lexical / Hybrid

已落地：

- `Repository.find_similar_document_chunks()` 使用 pgvector 余弦距离。
- `Repository.find_document_chunks_by_keywords()` 使用 PostgreSQL `to_tsvector`/`ts_rank_cd`。
- `FusionService` 做 RRF 与 weighted-score 融合。

### 4.2 Graph-like Retrieval

不引入图数据库，使用 PostgreSQL 关系表实现三跳关联召回：

```text
Entity -> event_entities -> Event -> documents -> DocumentChunk
Entity -> claim_evidence -> Claim -> event -> Event
Event  -> entity_aliases -> Entity
```

输入：`entity_ids`、`event_ids`、`time_range`、`top_k`。

输出：`RetrievedItem`，其中：

- `backend = "graph"`
- `text` 为关联事件标题/实体名/Claim 谓词摘要。
- `citation.locator` 保存 `{event_id, entity_id, claim_id, document_id, chunk_id}`。
- `backend_scores = {"graph": score}`，score 按跳数衰减（1 跳 1.0、2 跳 0.8、3 跳 0.64）。

### 4.3 Structured SQL Retrieval

按明确字段过滤召回：

- 事件：`event_type`、`occurred_at` 范围、`importance >= threshold`、`status`。
- Claim：`status = verified`、`predicate`、`confidence`。
- FactCard：`status = published`、`as_of`。

输出 `backend = "sql"` 的 `RetrievedItem`。

### 4.4 Time-series Retrieval

输入时间窗，按 `occurred_at` 倒序列出事件，并返回相关 DocumentChunk。用于"最近 N 天发生了什么"类问题。

## 5. Query Planner

### 5.1 输入输出

```python
@dataclass(frozen=True)
class RetrievalIntent:
    intent: str          # "event_lookup" | "document_search" | "impact_analysis" | "timeline"
    entity_ids: list[str]
    event_ids: list[str]
    event_types: list[str]
    time_range: tuple[datetime | None, datetime | None]
    keywords: list[str]

@dataclass(frozen=True)
class RetrievalPlan:
    query: str
    intents: list[RetrievalIntent]
    backends: list[str]  # ["vector", "graph", "sql"]
    primary_backend: str
    top_k: int
    as_of: datetime | None
```

### 5.2 规则层实现（MVP）

- 用 `LIKE` 查询 `events.entities`/`entity_aliases` 做实体对齐。
- 正则提取日期/时间窗（`2026-08-05`、`最近7天`、`本月`）。
- 关键词映射事件类型：`加息/降息/FOMC/LPR/准备金` -> `macro_policy`；`收购/合并` -> `ma`；`财报/业绩` -> `earnings`；`分红/派息` -> `dividend`。
- 意图路由：
  - 含"影响/利好/利空/板块" -> `impact_analysis`，主后端 `graph + vector`。
  - 含"最新/最近/时间线" -> `timeline`，主后端 `timeseries + sql`。
  - 含"文档/公告/原文" -> `document_search`，主后端 `vector/lexical/hybrid`。
  - 否则默认 `event_lookup`，主后端 `sql + vector`。

后续可接入 LLM 做意图增强，但规则层必须始终可解释、可审计。

### 5.3 计划执行

`RetrievalService` 对 `planned` 模式：

1. 调用 `QueryPlanner.plan(request)`。
2. 并发执行 `backends` 中各路召回。
3. 用 `FusionService` 融合多路结果。
4. 返回统一 `RetrievalTrace`。

## 6. API 设计

### 请求

```http
POST /api/v1/retrieval/retrieve
Authorization: Bearer <token>
Content-Type: application/json

{
  "query": "美联储加息对银行股的影响",
  "retrieval_mode": "planned",
  "top_k": 10,
  "as_of": "2026-08-14T00:00:00Z",
  "chunk_types": ["event_description", "financial_impact"],
  "source_tiers": ["S", "A"],
  "min_score": 0.0
}
```

`retrieval_mode` 支持：`vector` | `lexical` | `hybrid` | `graph` | `sql` | `timeseries` | `planned`。

### 响应

```json
{
  "data": {
    "candidate_count": 23,
    "items": [
      {
        "chunk_id": "chk_...",
        "document_id": "doc_...",
        "source_tier": "S",
        "chunk_type": "financial_impact",
        "text": "...",
        "score": 0.92,
        "backend": "hybrid",
        "backend_scores": {"vector": 0.91, "graph": 0.85},
        "citation": {
          "document_id": "doc_...",
          "chunk_id": "chk_...",
          "excerpt": "...",
          "locator": {"block_id": "blk_...", "char_start": 120, "char_end": 340}
        }
      }
    ],
    "filters": {...},
    "fusion_method": "rrf",
    "backend_coverage": {"vector": 10, "graph": 8, "sql": 5},
    "embedding_model_version": "openai-text-embedding-3-small-1536",
    "generated_at": "2026-08-14T01:30:00Z"
  },
  "meta": {...}
}
```

## 7. 控制面（预留）

本期不实现完整 Retrieval Control Plane，但在 `RetrievalPlan` 中预留策略字段：

- `profile_id`：租户/Agent 可绑定不同检索配置。
- `max_cost_usd` / `max_latency_ms`：预算与超时。
- `required_backends`：强制必须返回结果的后端，用于合规场景。

后续 WP-03（平台 Schema 包）将把这些策略提升到 `RetrievalProfile` 领域模型。

## 8. 验收标准

- `POST /api/v1/retrieval/retrieve` 对 `vector/lexical/hybrid/graph/sql/timeseries/planned` 均返回 200 与 `RetrievalTrace`。
- `planned` 模式能识别"美联储加息"-> 实体/事件类型 -> 同时调用 vector + graph + sql。
- Graph 召回至少覆盖：实体 -> 事件 -> 文档块；实体 -> Claim -> 事件。
- `as_of` 过滤对所有后端生效，未来数据不可进入结果。
- 新增 `tests/test_retrieval_api.py`、`tests/test_retrieval_planner.py` 全量通过。
- `uv run pytest` 与 `uv run ruff check .` 全绿。
