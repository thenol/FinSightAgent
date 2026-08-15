# DD-22：全量记忆与监听重估（Watch Triggers）

> 状态：初稿（2026-08-15）。修订 DD-20/DD-21 的事件状态语义，引入"无终态、可重估"的全量记忆模型。

## 1. 背景与问题

DD-21 把门控从类型白名单改为相关性裁决，但仍保留了终态：`irrelevant → archived`。
任何入口裁决都建立在信息最少的时刻，误判不可消除——历史上最大的市场事件
（2019 年末的不明原因肺炎通报、2021 年苏伊士运河搁浅）在入口处都呈现为
"地方简讯/航运事故"形态。终态归档使系统在这些事件上**永久失明**。

参考专业平台实践：Dataminr 全量处理 Twitter Firehose，事件靠多来源碎片化信号的
聚合检测；RavenPack 对每条新闻打标并附 relevance/novelty 等连续分数，过滤发生在
消费端。共同原则：**准入控制不等于信息销毁；门在来源层和展示层，不在文档层**。

## 2. 设计决策

### 2.1 Agent 动作空间

事件入口 Agent 的动作空间只有四个，不再有"丢弃"：

1. **记住**：落库 + 全文/向量索引 + 实体链接（无条件执行，DD-10 已有）；
2. **理解与打标**：规则分类（可审计证据）+ LLM 开放分类（标签 + 类比检索）；
3. **决定当前深度**：由 relevance/importance 分数映射到分析深度（连续，可升可降）；
4. **设置监听条件**：显式声明"什么信号会改变我的判断"，落库为 `watch_triggers`。

### 2.2 事件状态机变更

```
relevant  → triaged / needs_review（不变）
unsure    → dormant（不变）
irrelevant → cold（新，替代 archived 成为路由默认落点）
```

- `cold`：信息完整保留、可被检索与动态研究触达、挂有监听条件、可被重估升级。
  **不是终态**。
- `archived`：保留为人工显式归档的语义（合规/重复/作废），Router 不再自动产出。
- 合法迁移：`cold → needs_review / triaged`（重估升级）；`cold → archived`（人工）；
  `dormant → needs_review`（重估升级）；任何状态 → archived（人工）。

### 2.3 WatchTrigger 模型

```python
WatchTrigger(
    id: str,
    event_id: str,          # 监听对象
    trigger_type: str,      # source_cluster | source_upgrade | market_signal | user_query
    condition: dict,        # 触发条件参数（类型相关）
    status: str,            # armed | fired | cancelled
    created_at: datetime,
    fired_at: datetime | None,
)
```

首轮实现两类确定性可判定的条件：

- `source_cluster`：同一披露组（disclosure_group）或同一事件的独立来源数
  `>= min_sources`（默认 3）。对应 Dataminr 的聚集检测。
- `source_upgrade`：同一事件/披露组出现更高信任等级来源（如 B → S）的报道。

`market_signal`（行情异动回扫）与 `user_query`（动态研究命中冷文档触发正式事件化）
依赖行情数据接入与检索埋点，列为后续迭代。

注册时机：`EventService._persist_event` 落点为 `cold` 或 `dormant` 时，注册默认
`source_cluster`（min_sources=3）与 `source_upgrade` 两个监听条件。

### 2.4 重估流程（ReevaluationService）

```
扫描 armed 触发器 → 逐条检查条件（确定性查询）
  → 命中：trigger.status=fired，事件状态升级 cold/dormant → needs_review
    （重跑 ImportanceCalculator，标注 missing_required 含 reevaluation_confirm），
    写审计 event.reevaluated（含 trigger_id、trigger_type、evidence）
  → 未命中：保持 armed
```

重估是**常态运行**，不是"复活"特例。执行方式：`uv run python -m app.worker reevaluate`
（单次扫描）或由 outbox worker 周期调用；触发器检查全部为只读查询，升级操作走
Repository 事务。

### 2.5 检索覆盖确认

Hybrid Retrieval 以 DocumentChunk 为粒度，不按事件状态过滤——`cold` 事件的文档
天然可被检索与动态研究命中，无需额外改动。本设计在 DD-22 中显式声明该不变量，
并补测试锁定。

## 3. 不变量与边界

- 任何文档都不因相关性裁决而不可检索；
- `cold` 事件不进每日简报、不自动触发工作流（与 archived 同等对待）；
- 重估升级必须留审计（含触发器证据），升级后走正常 needs_review 人工确认；
- 历史 `archived` 数据不迁移，仅新流程不再自动产出；
- 本轮不实现：market_signal / user_query 触发器、展示层排序视图改造、
  分类法治理后台。

## 4. 测试点

- Router 判 irrelevant → 事件状态 `cold`（非 archived），且注册两个 armed 触发器；
- 触发器条件命中（同披露组 ≥3 来源）→ 事件升级 needs_review、触发器 fired、
  审计含 trigger 证据；
- 条件未命中 → 触发器保持 armed，事件状态不变；
- `cold` 事件可被 Retrieval 命中（不变量测试）；
- `cold` 事件不触发自动工作流、不进简报；
- 全量回归：既有 dormant/archived 相关测试语义对齐。
