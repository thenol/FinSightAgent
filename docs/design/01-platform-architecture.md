# DD-01 目标平台架构

> 状态：草案（WP-01）
> 最后更新：2026-07-27
> 适用范围：金融智能 Agent 平台长期目标架构，当前 MVP 作为第一阶段实现基线。

## 1. 设计目标

建设一个**多租户、事件驱动、证据可追溯、时态一致**的金融 Agentic Intelligence Platform。MVP 已经验证的模块化单体、PostgreSQL 真值源、Outbox/Inbox、LangGraph 工作流和 Agent Schema 约束应作为平台演进的初始基线，而不是临时方案。

目标架构必须满足：

- **证据优先**：任何结论可回溯到 Claim、Evidence、Revision 和原始来源。
- **双时态一致**：业务有效时间（`valid_at`）与系统获知时间（`recorded_at`）分开建模，研究和评估严格服从 `as_of`。
- **控制面与数据面分离**：配置、策略、发布治理不与高吞吐执行路径耦合。
- **可评测发布**：模型、Prompt、Embedding、Reranker、索引和规则变更均受质量门禁约束。
- **租户隔离**：数据、缓存、对象、向量和图谱均按租户命名空间隔离。

## 2. 服务边界

平台由五类服务组成。当前 MVP 以模块化单体形式部署，但接口应保持未来拆分能力。

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         控制面 (Control Plane)                       │
│  Admin API  │  Schema Registry  │  Policy Engine  │  Orchestrator   │
└─────────────────────────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────────────────────────────────────────┐
│                         在线服务 (Online Services)                   │
│  Public API  │  Research API  │  Notification API  │  Search API    │
└─────────────────────────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────────────────────────────────────────┐
│                         异步作业 (Async Workers)                     │
│  Ingestion  │  Parsing/Chunking  │  Embedding  │  Workflow Runner   │
│  Retention  │  Orphan Audit  │  Sync Dispatcher  │  Report Publisher │
└─────────────────────────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────────────────────────────────────────┐
│                         数据面 (Data Plane)                          │
│  PostgreSQL  │  Object Store  │  Redis  │  Kafka/Pulsar  │  Vector   │
│  ClickHouse  │  Knowledge Graph  │                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.1 控制面服务

| 服务 | 职责 | MVP 现状 |
|---|---|---|
| Admin API | 来源、用户、LLM provider、策略、审核管理 | 已具备基础 CRUD |
| Schema Registry | 管理 API、领域事件、Agent、Tool、Retrieval Schema 版本与兼容性 | 未建立 |
| Policy Engine | 租户配额、数据许可、工具权限、发布策略、预算策略 | 部分硬编码 |
| Orchestrator | 动态 ResearchPlan 编排、长事务状态机、补偿 | 当前为 LangGraph + WorkflowService |

### 2.2 在线服务

| 服务 | 职责 | MVP 现状 |
|---|---|---|
| Public API | 外部客户查询、订阅、Webhook、数据导出 | 未建立 |
| Research API | 交互式研究、问题理解、计划展示、证据下钻 | 管理端已有只读视图 |
| Notification API | 预警、通知、订阅去重与确认 | 未建立 |
| Search API | 事件、报告、Claim、文档统一检索 | 仅列表/详情 API |

### 2.3 异步作业

| Worker | 职责 | MVP 现状 |
|---|---|---|
| Ingestion Worker | 来源同步、Fetcher 调度、去重、隔离队列 | `app.worker source` 已实现 |
| Parsing Worker | HTML/PDF/OCR 解析、Block/Chunk 生成 | 仅 HTML Block Reader |
| Embedding Worker | Chunk/Claim/事件/实体 embedding 生成与索引 | 未建立 |
| Workflow Runner | 执行 LangGraph/Temporal 研究计划 | 当前同步执行 + 独立 `run` API |
| Retention Worker | 软删归档、Purge、许可变更传播 | `retention:auto_purge` 已实现 |
| Orphan Audit Worker | 派生数据一致性巡检与告警 | `scripts/orphan_audit.py` 已落地 |
| Report Publisher | 报告签发、撤回、替代、导出 | 状态流转已有，签发/导出未建立 |

## 3. 存储选型

### 3.1 真值源与事务存储：PostgreSQL

- **角色**：业务真值源，保存 Document、Event、Claim、Report、Review、Workflow、Audit、Outbox/Inbox、租户元数据。
- **选型理由**：ACID、成熟运维、行级租户隔离、时态查询支持。
- **MVP 现状**：已实现 SQLAlchemy Repository + Alembic 迁移。
- **演进**：单库按租户分 schema 或读写分离；跨租户聚合走分析存储。

### 3.2 对象存储：S3 / MinIO

- **角色**：保存原始 Artifact、规范化 Revision、PDF/OCR 结果、导出报告、审计包。
- **选型理由**：内容寻址、不可变、版本控制、低成本。
- **MVP 现状**：本地内存 ArtifactStore；生产应替换为 S3/MinIO。
- **关键约定**：
  - 对象键包含 `tenant_id`、`artifact_hash` 和版本。
  - 删除通过生命周期策略，禁止物理删除审计链对象。

### 3.3 消息与缓存：Redis

- **角色**：
  - **Streams**：Outbox 投递、Worker 任务队列、通知。
  - **Cache**：会话、限流、幂等键 TTL、热点查询。
- **选型理由**：低延迟、与现有 Outbox/Inbox 模式兼容。
- **MVP 现状**：已实现 Redis Streams Outbox publisher 和 Inbox 去重。
- **演进**：当吞吐超过 Redis Streams 单实例能力时，引入 Kafka/Pulsar 作为跨服务事件总线，Redis 保留缓存和轻量队列。

### 3.4 平台事件总线：Kafka / Pulsar（延后引入）

- **角色**：跨服务领域事件传播（文档、解析、Chunk、Embedding、实体、事件、Claim、索引、研究、报告、许可、删除）。
- **引入条件**：
  - 日事件量 > 1000 万；
  - 需要多消费者组独立重放；
  - Redis Streams 出现持久化或消费延迟瓶颈。
- **设计约束**：
  - Outbox/Inbox 仍作为事务一致性边界；
  - Kafka/Pulsar 只接收已经事务提交的 Outbox 事件；
  - 领域事件必须带 Schema 版本、幂等键和 `as_of`。

### 3.5 向量索引：Milvus / Qdrant（延后引入）

- **角色**：Document Chunk、Event、Claim、Company、Regulation、Metric description 的语义检索。
- **选型对比**：
  - **Milvus**：分布式、租户隔离友好、适合大规模。
  - **Qdrant**：轻量、易运维、单机到集群平滑。
- **引入条件**：
  - 语义检索成为核心路径；
  - pgvector 在单 PostgreSQL 实例上无法满足 recall/延迟要求。
- **MVP 过渡**：先用 `pgvector` 或内存向量索引验证效果，再迁移。

### 3.6 分析与时序存储：ClickHouse（延后引入）

- **角色**：行情、财务指标、工作流指标、质量评估、审计日志分析。
- **引入条件**：
  - 行情数据量超过 PostgreSQL 处理能力；
  - 需要复杂时序聚合和宽表分析。

### 3.7 知识图谱：Neo4j / 属性图扩展（延后引入）

- **角色**：Entity、Security、Person、Product、Industry、Event、Claim、Regulation、RiskFactor 之间的关系。
- **设计约束**：
  - 事实边与推断边逻辑或物理隔离；
  - 边带来源、有效时间、系统时间、置信度、版本和审核状态；
  - PostgreSQL 中的主数据和 Claim 仍是真值源，图谱为派生视图。

## 4. 事件架构

### 4.1 领域事件分层

| 层级 | 示例 | 消费者 |
|---|---|---|
| 文档层 | `document.ingested.v1` | Parser、EventMatcher、Audit |
| 解析层 | `document.parsed.v1` | Chunker、EvidenceService |
| 知识层 | `claim.verified.v1`、`conflict.detected.v1` | ReportAssembler、ResearchPlanner |
| 研究层 | `workflow.started.v1`、`node.succeeded.v1` | Budget、Audit、Notification |
| 发布层 | `report.published.v1`、`report.withdrawn.v1` | Brief、Subscription、Export |
| 治理层 | `license.changed.v1`、`tenant.policy.updated.v1` | Retention、CitationResolver、Search Index |

### 4.2 Outbox / Inbox 模式

```text
[API/Worker] -> [PostgreSQL 事务：业务写入 + Outbox 记录]
                       |
                       v
            [Outbox Publisher Worker]
                       |
         +-------------+-------------+
         |                           |
         v                           v
 [Redis Streams]        [Kafka/Pulsar 未来]
         |                           |
         v                           v
    [Consumer]              [Consumer Group]
         |                           |
         v                           v
    [Inbox 去重]             [Inbox 去重]
```

关键约束：

- 业务写入和 Outbox 记录在同一事务提交。
- 不得在事务提交前直接发送消息到外部队列。
- 消费者用业务幂等键保证至少一次投递下效果等同于一次。
- 领域事件 Schema 由 Schema Registry 管理，禁止无 Schema 的事件进入总线。

## 5. 部署拓扑

### 5.1 第一阶段（MVP 扩展）

保持模块化单体，但把异步 Worker 独立部署：

```text
                    ┌─────────────┐
                    │   Load      │
                    │  Balancer   │
                    └──────┬──────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            v              v              v
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │  FastAPI     │ │  FastAPI     │ │   Worker     │
    │  Public API  │ │  Admin API   │ │  (Source/    │
    │  (read-heavy)│ │  (control)   │ │   Workflow/  │
    └──────────────┘ └──────────────┘ │   Retention) │
                                      └──────────────┘
            │              │              │
            └──────────────┼──────────────┘
                           v
                    ┌─────────────┐
                    │ PostgreSQL  │
                    │   (主库)     │
                    └─────────────┘
                           │
                    ┌──────┴──────┐
                    v             v
                 Redis        Object Store
               (Streams/     (S3/MinIO)
                Cache)
```

### 5.2 目标平台阶段

当容量或团队规模要求拆分服务时，按功能域拆分为独立部署单元：

```text
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Public API  │ │ Research API│ │ Admin API   │ │ Notification│
└──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
       │               │               │               │
       └───────────────┴───────────────┴───────────────┘
                           │
                    ┌──────┴──────┐
                    │   Gateway   │
                    │  Auth/Rate  │
                    └──────┬──────┘
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
       v                   v                   v
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Ingestion   │    │ Research    │    │ Publishing  │
│ Workers     │    │ Workers     │    │ Workers     │
└─────────────┘    └─────────────┘    └─────────────┘
```

拆分触发条件（ADR-004 退出条件）：

- 独立扩容需求（如 Ingestion Worker CPU 密集型，Research Worker GPU 密集型）；
- 发布节奏冲突（控制面更新不应影响数据面）；
- 数据库负载隔离需求；
- 单体故障域无法满足 SLA。

## 6. 容量模型

### 6.1 假设

- 日均公告：10 万条（峰值 3 倍）。
- 每条公告平均 5 KB 正文，10% 含 PDF 附件。
- 每公告平均生成 3 个 Claim，1 个 Event，0.5 个 Workflow。
- 每条 Claim 平均生成 2 个 Chunk。

### 6.2 存储估算

| 数据 | 日增量 | 年增量 | 备注 |
|---|---|---|---|
| 原始 Artifact | ~500 GB | ~180 TB | PDF 占大头，需生命周期归档 |
| PostgreSQL 业务数据 | ~10 GB | ~3.6 TB | 含 Document/Event/Claim/Report |
| 向量索引 | ~20 GB | ~7 TB | 取决于 embedding 维度和 Chunk 数量 |
| ClickHouse 分析 | ~5 GB | ~1.8 TB | 行情/指标/审计分析 |

### 6.3 计算估算

| 路径 | QPS | 关键资源 | 备注 |
|---|---|---|---|
| 文档摄入 | ~1/s 平均，~5/s 峰值 | CPU / 网络 | 解析和 embedding 占主要成本 |
| 工作流执行 | ~0.1/s 平均 | GPU / LLM token | 高重要度事件才触发 |
| 查询 API | ~100/s | PostgreSQL / 缓存 | 热点事件走 Redis |
| 语义检索 | ~50/s | 向量库 / GPU | 可降级为关键词检索 |

## 7. 容灾与一致性

### 7.1 数据库

- PostgreSQL 使用同步复制到跨可用区从库。
- RPO：0（同步复制）或 1 分钟（异步复制，取决于成本）。
- RTO：5 分钟（自动故障转移）。
- 每日逻辑备份 + 连续 WAL 归档。

### 7.2 对象存储

- 多可用区冗余（S3 Standard / MinIO 纠删码）。
- 跨区域复制用于灾难恢复。
- 禁止物理删除审计链对象，生命周期转冷存储。

### 7.3 消息队列

- Redis AOF + RDB 持久化。
- Kafka/Pulsar 引入后使用多副本 + 跨可用区部署。

### 7.4 派生数据一致性

- 所有派生数据（向量索引、图谱、分析表、缓存）必须有明确真值源和一致性巡检。
- `orphan_audit` 已作为 MVP 巡检模板，目标平台应扩展为按租户、按索引类型的定期巡检。
- 删除、许可变更、租户策略更新必须传播到所有派生存储。

## 8. 安全边界

- **网络**：控制面服务部署在私有子网，仅通过 Gateway 暴露；Worker 不直接暴露公网。
- **身份**：OIDC/SSO + 服务身份（mTLS）。
- **密钥**：LLM API key、数据库密码、加密密钥走 Secret Manager，禁止进入 Git 或日志。
- **数据驻留**：租户数据按配置驻留特定区域，跨境分析需显式授权。
- **模型出境**：向外部 LLM 发送的提示词和上下文必须经过策略审查和审计。

## 9. 演进路线图

| 阶段 | 目标 | 关键工作 |
|---|---|---|
| **Phase 1（当前）** | MVP 验证 | 模块化单体、PostgreSQL、Redis Streams、LangGraph、React 管理端 |
| **Phase 2** | 平台化数据与检索 | Document Intelligence LLD、Embedding 生命周期、pgvector、语义 Chunking |
| **Phase 3** | 知识图谱与金融数据 | Entity Master、Event Cluster、Knowledge Graph、Financial Data Platform |
| **Phase 4** | 动态 Agent Runtime | ResearchPlan、Specialist Agent Registry、LangGraph/Temporal 协作 |
| **Phase 5** | 多租户与开放平台 | Tenant 模型、Public API/SDK、Webhook、数据导出、Quota |
| **Phase 6** | 规模化基础设施 | Kafka/Pulsar、ClickHouse、Milvus/Qdrant、跨区域容灾 |

## 10. 待确认决策

以下决策需要负责人输入后才能定稿：

| 决策 | 选项 | 影响 | 建议 |
|---|---|---|---|
| 向量数据库 | Milvus vs Qdrant vs pgvector | 运维复杂度、租户隔离、容量 | Phase 2 先用 pgvector 验证，规模到后再迁 Qdrant |
| 长事务编排 | LangGraph vs Temporal | 跨服务恢复、超时、补偿 | LangGraph 保留 Agent 推理；Temporal 在 Phase 4 引入 |
| 事件总线 | Redis Streams vs Kafka/Pulsar | 吞吐、重放、运维 | 当前 Redis Streams 够用；引入条件见 3.4 |
| 对象存储 | S3 vs MinIO vs 国内云 | 成本、合规、数据驻留 | 生产用 S3/兼容对象存储；开发用 MinIO |
| 多租户隔离 | 行级 vs schema vs 分库 | 复杂度、性能、成本 | 先 PostgreSQL 行级 + 应用层过滤；大客户再 schema |

## 11. 相关文档

- [DD-00 共享约定](./00-shared-conventions.md)
- [DD-10 采集](./10-ingestion.md)
- [DD-20 事件中心](./20-event-center.md)
- [DD-30 存储 schema](./30-storage-schema.md)
- [DD-40 证据中心](./40-evidence-center.md)
- [DD-50 研究工作流](./50-research-workflow.md)
- [DD-60 报告与审核](./60-reporting-review.md)
- [DD-70 评估与可观测](./70-evaluation-observability.md)
- [ADR 记录](../06-architecture-decisions.md)
- [平台待开发清单](../10-platform-development-backlog.md)
