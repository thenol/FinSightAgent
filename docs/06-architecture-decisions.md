# 架构决策记录

## 1. 使用方式

本文件记录影响多个模块的关键取舍。状态为“已接受”的决策是当前实现约束；“建议”仍需负责人确认。决策被替代时保留原记录，并链接新的决策编号。

## 2. 决策摘要

| ID | 决策 | 状态 | 主要原因 |
| --- | --- | --- | --- |
| ADR-001 | 以事件作为工作流和审计的基本单位 | 已接受 | 支持去重、唤醒、版本和回放 |
| ADR-002 | 采用 Supervisor + Blackboard 编排 | 已接受 | 限制自由对话造成的漂移和失控 |
| ADR-003 | 代码按业务功能域组织 | 已接受 | Agent 只是研究域组件，不能代替系统边界 |
| ADR-004 | MVP 采用模块化单体和异步 Worker | 已接受 | 降低部署复杂度，同时保留拆分边界 |
| ADR-005 | PostgreSQL 是业务真值源 | 已接受 | 事务、关系和版本查询满足 MVP 需求 |
| ADR-006 | 原始文档与报告版本采用不可变追加写 | 已接受 | 保证证据审计和无未来泄漏回放 |
| ADR-007 | 事实核验是专项分析的强前置节点 | 已接受 | 阻止未验证信息进入分析链路 |
| ADR-008 | Synthesis Agent 禁止开放式检索 | 已接受 | 保证最终结论只综合已审计材料 |
| ADR-009 | 自动交易不属于系统能力范围 | 已接受 | 隔离资金与不可逆操作风险 |
| ADR-010 | Kafka、ClickHouse、Temporal 延后引入 | 已接受 | 应由真实规模和恢复 SLA 驱动升级 |
| ADR-016 | 目标平台采用分阶段存储与事件驱动架构 | 已接受 | 对齐平台 backlog，保留 MVP 基线 |
| ADR-017 | 向量索引延后引入，Phase 2 先用 pgvector 验证 | 已接受 | 避免过早引入 Milvus/Qdrant 运维负担 |
| ADR-018 | 领域事件总线分层：PostgreSQL Outbox + Redis Streams + 可选 Kafka/Pulsar | 已接受 | 保证事务一致性并保留扩展路径 |
| ADR-019 | 多租户优先采用 PostgreSQL 行级隔离 | 建议 | 降低初期复杂度，大客户再 schema 隔离；租户模型尚未引入 |
| ADR-020 | Agent Runtime 分层：LangGraph 负责推理图，Temporal 负责跨服务编排 | 建议 | 发挥各自优势，避免 LangGraph 承担长事务；当前仍由 WorkflowService 封装 LangGraph |

## 3. 关键决策说明

### ADR-001：事件是基本单位

文档是信息载体，不是研究对象。同一事件可能关联多篇公告和新闻，也会在数周后发生变化。稳定的 Event ID 用于连接证据、工作流、报告和评估。

影响：事件合并不能删除来源；重分析必须形成新的运行和报告版本。

### ADR-003：按功能域组织代码

采集、事件、证据、研究、发布和评估拥有不同数据与权限边界。若顶层目录按 Agent 划分，确定性逻辑会散落并形成跨 Agent 重复实现。

影响：Agent 放在 `app/research/` 内；跨域写入必须经过领域服务。

### ADR-004：模块化单体作为 MVP 形态

首期团队和流量尚未证明微服务收益。FastAPI API 与异步 Worker 可以独立运行，但共享同一代码库和 PostgreSQL，通过明确模块接口保持未来拆分能力。

退出条件：出现独立扩容需求、发布节奏冲突、数据库负载隔离需求，或单体故障域无法满足 SLA。

状态于 2026-07-12 接受，作为首个工程骨架的实现基线。

### ADR-006：不可变版本

金融评估要求复现“当时知道什么”。Document、AgentRun 和 ReportVersion 采用追加写；纠错通过新版本和 `supersedes_id` 表达。

影响：存储与清理策略必须保留审计链，回测查询强制使用 `as_of`。

## 4. 平台级建议决策

### ADR-016：目标平台采用分阶段存储与事件驱动架构

- 状态：已接受
- 日期：2026-07-27
- 背景：MVP 已验证核心链路，但后续需向多租户、Hybrid Retrieval、动态 Agent Runtime 演进。需要明确目标架构边界，避免临时实现替代正式领域模型。
- 决策：
  - 目标架构分为控制面、在线服务、异步作业和数据面四层；
  - 当前模块化单体和 PostgreSQL 真值源作为 Phase 1 基线保留；
  - 向量库、图谱、时序分析、事件总线在满足明确容量/质量门槛后分阶段引入。
- 备选：一步到位微服务 + Kafka + ClickHouse + Milvus + Neo4j。
- 影响：新增 `docs/design/01-platform-architecture.md`；Phase 2 起按 WP 顺序补齐 LLD 后再编码。
- 实现状态：WP-04 Document Intelligence 已按 Phase 1 基线实现，未引入独立向量库/图谱/时序组件。
- 复审条件：任意组件达到 ADR-010 引入条件，或团队规模/发布节奏触发 ADR-004 退出条件。

### ADR-017：向量索引延后引入，Phase 2 先用 pgvector 验证

- 状态：已接受
- 日期：2026-07-27
- 背景：跨渠道去重、语义检索、相似事件聚类都需要向量能力，但直接引入 Milvus/Qdrant 会增加运维和租户隔离复杂度。
- 决策：
  - Phase 2 在 PostgreSQL 内用 `pgvector` 验证语义召回效果；
  - 达到明确容量或 recall/延迟门槛后，再迁移到 Milvus 或 Qdrant。
- 备选：直接部署 Milvus/Qdrant。
- 影响：Embedding Worker 先写入 PostgreSQL 向量列；Schema 设计预留 `collection_name`、`embedding_model_version`。
- 实现状态：DOC-003 已落地 `EmbeddingRecord` 表与 `EmbeddingService`；`DisclosureGroupService` 在 PostgreSQL 路径使用 pgvector `<=>` 余弦距离做 TOP-K 向量召回，SQLite/内存路径回退到应用层 brute-force；Docker Compose 已改用 `pgvector/pgvector:pg16`。全量重建、蓝绿切换、失败恢复和版本回滚待完善。
- 复审条件：向量索引日写入 > 100 万条，或 pgvector 查询 P95 > 200ms。

### ADR-018：领域事件总线分层

- 状态：已接受
- 日期：2026-07-27
- 背景：MVP 已用 PostgreSQL Outbox + Redis Streams 实现事务消息。未来跨服务事件增多，需要可扩展的事件总线。
- 决策：
  - 事务边界内仍用 PostgreSQL Outbox；
  - 当前 Outbox Publisher 投递到 Redis Streams；
  - 当吞吐或消费者组复杂度超过 Redis Streams 能力时，引入 Kafka/Pulsar 作为跨服务领域事件总线。
- 备选：直接用 Kafka/Pulsar 替代 Redis Streams。
- 影响：领域事件 Schema 必须带版本、幂等键和 `as_of`；消费者去重逻辑保持不变。
- 实现状态：PostgreSQL Outbox、Redis Streams Publisher、持久化 Inbox 去重、退避和死信已实现并在生产路径运行；Kafka/Pulsar 尚未引入。
- 复审条件：日事件量 > 1000 万，或需要多消费者组独立重放。

### ADR-019：多租户优先采用 PostgreSQL 行级隔离

- 状态：建议
- 日期：2026-07-27
- 背景：平台目标包含多租户，但 MVP 尚未引入租户模型。
- 决策：
  - 所有业务表增加 `tenant_id`；
  - 应用层和 Repository 强制过滤；
  - 大客户或合规要求独立 schema 时，再按 schema 隔离。
- 备选：一开始就用 schema-per-tenant 或独立数据库。
- 影响：需要在 Schema Registry、对象存储、向量库、图谱中同步引入 `tenant_id` 命名空间。
- 实现状态：租户模型尚未引入；当前 Repository 与 API 未过滤 `tenant_id`。
- 复审条件：单个租户数据量超过单库容量 30%，或合规要求物理隔离。

### ADR-020：Agent Runtime 分层

- 状态：建议
- 日期：2026-07-27
- 背景：LangGraph 适合 Agent 推理图，但不擅长跨服务长事务、超时和补偿。
- 决策：
  - LangGraph 负责单个 Agent 或子图的推理执行；
  - 跨服务、跨长时间窗口的编排由 Temporal（或同类工作流引擎）承担；
  - 两者通过领域事件和 Blackboard 状态协调。
- 备选：所有编排都用 LangGraph 或所有编排都用 Temporal。
- 影响：ResearchPlan 和动态 DAG 在 Phase 4 引入；当前 WorkflowService 作为 LangGraph 的封装继续保留。
- 实现状态：当前 WorkflowService 仍封装 LangGraph；Temporal 尚未引入，跨服务 Saga 与小时级长流程需求未出现。
- 复审条件：出现跨服务 Saga、外部人工审批或小时级长流程需求。

## 5. 待确认决策

| 候选 ID | 问题 | 需要的输入 | 决策负责人 |
| --- | --- | --- | --- |
| ADR-011 | LangGraph 与模型网关的最终组合 | 恢复原型、模型切换需求、成本测试 | 技术负责人 |
| ADR-012 | 行业分类标准 | 研究团队现有口径及数据授权 | 研究负责人 |
| ADR-013 | 授权新闻存储策略 | 供应商合同、合规意见 | 产品与合规负责人 |
| ADR-014 | 首期预警渠道 | 用户调研、权限与送达要求 | 产品负责人 |
| ADR-015 | 人工审核的编辑权限 | 审计要求和研究流程 | 研究与合规负责人 |

## 5. 新增决策模板

```markdown
### ADR-NNN：标题

- 状态：建议 / 已接受 / 已废弃 / 已替代
- 日期：YYYY-MM-DD
- 背景：需要解决的问题和约束。
- 决策：选择的方案。
- 备选：考虑过但未选择的方案。
- 影响：对模块、数据、运维和迁移的影响。
- 复审条件：何时需要重新评估。
```
