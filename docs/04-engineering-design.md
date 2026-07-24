# 接口、工程与运行设计

## 1. 服务边界

MVP 建议采用模块化单体加异步 Worker，避免过早拆分微服务：

```text
FastAPI API
├── ingestion
├── events
├── evidence
├── research
├── reports
├── evaluations
└── admin

Worker
├── collectors
├── workflow runner
├── notification jobs
└── evaluation jobs
```

模块通过领域服务和事件消息解耦；当吞吐量或团队边界明确后再拆成独立服务。

## 2. 核心 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/documents/ingest` | 接收标准化文档或采集结果 |
| GET | `/api/v1/events` | 查询事件列表 |
| GET | `/api/v1/events/{id}` | 查询事件、证据和最新结论 |
| POST | `/api/v1/events/{id}/research` | 启动或重新分析 |
| GET | `/api/v1/workflows/{id}` | 查询运行状态与节点记录 |
| POST | `/api/v1/reviews/{id}/decision` | 提交人工审核决定 |
| GET | `/api/v1/reports/{id}` | 获取指定报告版本 |
| GET | `/api/v1/briefs/daily` | 获取每日简报 |
| POST | `/api/v1/evaluations/run` | 创建评估任务 |

所有写接口支持请求 ID 和幂等键。外部 API 返回稳定错误码，不直接暴露模型或数据库异常。

## 3. 领域事件

首期定义：`document.ingested`、`event.created`、`event.updated`、`workflow.started`、`workflow.waiting_review`、`report.published`、`event.reanalysis_requested` 和 `evaluation.completed`。

消息包含 `event_id`、`occurred_at`、`schema_version`、`trace_id` 和幂等键。消费者成功提交业务事务后再确认消息。

## 4. 工具接口规范

Agent 工具采用窄接口和严格 Schema。每次调用记录调用者、参数摘要、数据版本、延迟、结果数量、错误类型和 trace ID。行情、财务和计算工具返回 `as_of`，防止工作流读取未来数据。

工具权限按 Agent 白名单配置。例如 Fact Checker 可检索公告但不可发布报告；Synthesizer 可读取分析结果但不可进行开放式搜索。

## 5. 安全与合规

- 用户与服务采用 RBAC，研究、审核、发布和管理权限分离。
- 密钥进入专用 Secret 管理，不写入提示词、日志或数据库明文字段。
- 外部文档视为不可信输入，防止提示词注入改变工具权限或系统指令。
- 日志对个人信息、授权新闻正文和密钥进行脱敏。
- 报告显著标记生成时间、数据截止时间和“非投资建议”。
- 交易和资金操作不属于系统自动执行范围。

## 6. 可观测性

使用统一 trace 串联采集批次、事件、工作流、AgentRun、工具调用和报告版本。至少监控：采集延迟、队列积压、工作流成功率、节点 P95 延迟、单事件成本、Schema 失败率、人工审核率和引用完整率。

告警分为数据源故障、系统故障、成本异常、质量异常和合规拦截五类。

## 7. 部署建议

开发环境使用 Docker Compose 启动 API、Worker、PostgreSQL、Redis 和对象存储。生产环境将 API 与 Worker 独立扩缩容，数据库启用备份与时间点恢复，对象存储启用版本控制。

MVP 不强制引入 Kafka、ClickHouse、独立图数据库或 Temporal；只有当 Redis 队列、PostgreSQL 或 LangGraph 检查点不能满足已测量需求时再升级。

## 8. 测试策略

- 单元测试：规则、评分、状态迁移、数据转换和权限。
- Schema 测试：Agent 输入输出及工具契约。
- 集成测试：数据源适配、数据库、队列和对象存储。
- 工作流测试：成功、超时、冲突、人工审核和恢复路径。
- 回归评估：固定时间截面的典型金融事件集。
- 安全测试：提示词注入、越权工具调用、敏感信息泄露。
- 回放测试：确保历史分析不会读取后续发布的数据。

## 9. 配置与版本管理

数据源、阈值、预算和功能开关使用环境化配置；提示词、模型、工具 Schema 和工作流图使用显式版本号。报告必须关联全部版本信息，保证相同输入能够解释而不要求模型输出逐字复现。

## 10. 待决策事项

- 首期模型供应商、主模型和低成本分类模型。
- LangGraph 检查点的存储和恢复 SLA。
- 授权新闻能否保存全文或只能保存摘要与索引。
- 预警渠道首期仅站内，还是同时支持邮件和企业消息平台。

