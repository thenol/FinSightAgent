# 工作进度清单

> 最后更新：2026-07-23（评估 Loop 核对）。状态符号：`[x]` 已完成，`[ ]` 待完成，`[-]` 进行中或部分完成。

## 1. 方案与详细设计

- [x] 完成总体方案、产品需求、功能架构和 MVP 验收标准。
- [x] 完成数据接入、事件中心和物理数据模型详细设计。
- [x] 完成证据中心及 Fact Checker 输入/输出 Schema。
- [x] 完成研究工作流及 Company、Skeptic、Synthesis Schema。
- [x] 完成报告审核、评估和可观测详细设计。
- [x] 建立 ADR，接受模块化单体、PostgreSQL 真值源和延后复杂基础设施等决策。
- [-] RSS 采集、条件请求、域名白名单、HTML/PDF 解析和同步服务已实现；MarketMind 风格 Fetcher 工厂、RSS 种子源、robots/限速守卫与 RSSHub 路由已接入；HTML 正文抽取已改为 article/密度择优并清洗脚本噪音（`html_text`，证据 API 展示前 scrub）；修复 RSS 短摘要被 1.0 硬阈值误丢弃的回归 bug（`total_candidate_len < 200` 时保留全部候选）；首个交易所 S 级官方 API 与 OCR 方案仍待确认。

## 2. 工程基础

- [x] 初始化 FastAPI、Pydantic、Uvicorn、uv、Pytest 和 Ruff。
- [x] 按 `ingestion/events/evidence/publishing/platform/api` 拆分代码。
- [x] 配置 Dockerfile、Docker Compose、PostgreSQL 和 Redis 服务。
- [x] 创建 PostgreSQL 初始 DDL。
- [x] 实现 UUIDv7 风格 ID、健康检查和统一 API 数据信封。
- [x] 使用 SQLAlchemy 2.x 实现 PostgreSQL Repository，并保留内存测试适配器。
- [x] 使用 Alembic 接管数据库迁移，并覆盖升级/降级测试。
- [x] 实现事务 Outbox、Redis Streams Publisher、持久化 Inbox 去重、退避和死信。
- [-] 已有 JSON 请求日志、`Settings` 与可插拔 `Observability`（内存 Sink / no-op）；生产 OpenTelemetry 导出与完整配置治理仍待完成。

## 3. 首条确定性链路

- [x] 实现公告输入、文本标准化和 URL 清洗。
- [x] 实现同来源精确去重及幂等冲突检测。
- [x] 实现五类事件规则识别和 A 股证券代码对齐。
- [x] 实现 Evidence 定位、来源等级 Claim 和事实卡片。
- [x] 实现事件列表、事件详情和报告查询 API。
- [x] 验证不同来源相同内容不会丢失来源文档。
- [x] 实现本地内容寻址 Artifact、DocumentRevision 和历史 Evidence Revision 绑定。
- [-] EventMatcher、匹配决定审计、自动合并/新建/审核路由已接入主流水线；人工合并决定、错误拆分和标注集阈值校准仍待完成。
- [-] 实现实体主数据、别名及历史证券代码；Entity/Security/EntityAlias 表、EntityResolver（代码精确对齐 1.0、自动建主数据）、event_entities 关联与 merge_review_tasks 已完成，全称/简称评分与历史代码仍待标注集校准。
- [-] 实现五类事件专用 Schema；EventSchema 定义、EventClassifier 确定性 key_fields 抽取（期间/金额区间/同比变化/比例/对手方/标的/股东名）、必填缺失降级 needs_review、Claim 模板谓词已完成；标注集已含复杂字段正例；2026-07-23：未命中五类时分层 `general_market_news`/`out_of_scope`，Admin 默认筛五类研究；同日接入 **Event Router**（规则提名 + ModelGateway `event_route` 确认，Deterministic 跟随 hint）；仅 accept 的五类进入可研究状态，综合资讯 dormant；更多语料与实体主数据对齐仍待扩集。
- [-] 实现 as_of 时间截面；研究类查询（list_events/get_claims_for_event/get_fact_card_for_event/find_event_by_document/get_latest_revision/find_claim_by_fingerprint）支持 as_of 过滤、AsOfViolation 与 ensure_within_as_of 供 ToolGateway 复用已完成，回放防未来数据泄漏测试已覆盖，SQL 与 InMemory 双端已对齐。

## 4. 证据与研究工作流

- [-] HTML 段落及文本型 PDF 的页码、0-based 偏移、归一化 BBox、原文一致性校验和 parser 版本绑定已完成；表格、OCR、PDF Revision 接入和实际来源验证仍待完成。
- [-] Source 持久化、RSS 手动/定时同步、JWT 登录、角色校验、审计查询和用户 provisioning 命令已完成；`crawl_interval_seconds`、source worker（APScheduler 热重扫）、`ingest_runs` 与 sync-all/runs API 已落地；生产密钥、显式增量迁移和完整审核 API 仍待完成。
- [-] 实现 ClaimNormalizer、ClaimMatcher 和 ConflictDetector；规范化、受控谓词、事实指纹、来源独立性、六类冲突与严重度已完成，EvidencePolicyService 决策与 claim_evidence/conflicts 表已落库，claim 指纹去重已生效。
- [x] 实现确定性的 EvidencePolicyService。
- [x] 接入 LangGraph Checkpointer 和 Blackboard Repository。
- [-] 实现 Supervisor、预算账本、节点重试和局部重跑；LangGraph 图编排、确定性 Supervisor 路由、Blackboard 字段填充、6 维预算账本（reserve/settle/release 软硬阈值+节点上限）、节点幂等键 `workflow_id+node+input_hash`（重放复用不重复副作用）与 Blackboard 字段写入所有权乐观锁已完成；**节点重试退避**（`MODEL_TRANSIENT` 最多 2 次、`OUTPUT_SCHEMA_INVALID` 1 次、attempt_no 递增）与**局部重跑失效传播**（`invalidation.py` 映射表 + `invalidate_node_attempts` + `WorkflowService.resume`）已完成；2026-07-23：`POST /events/{id}/workflows` 默认 `execute=true` 同步跑图，并新增 `POST /workflows/{id}/run`（pending→执行）与 Admin「启动运行」；新增高重要度事件自动进入工作流：`EventResearchPipeline` 在创建事件后，若 `importance >= FINSIGHT_WORKFLOW_AUTO_IMPORTANCE_THRESHOLD`（默认 0.7）且非 dormant/archived，自动创建 `pending` workflow run（trigger_id=auto），由 worker 或 Admin 启动执行。
- [x] 接入 Fact Checker、Company Analyst、Skeptic 和 Synthesizer（四个 Agent 节点已实化，输出经 Pydantic Schema 校验，区分事实/假设/推论，数值走工具）；2026-07-23：LLM 密钥解密失败时回退 DeterministicProvider 并审计，避免本机跑图卡死。
- [x] 实现 Agent 工具白名单、`as_of` 校验和调用审计（ToolGateway 按 DD-50 §12 鉴权、as_of 越界拒绝、正文当不可信数据隔离、tool_calls 审计表）。
- [x] 实现事实卡片降级及人工审核恢复（预算硬限有 verified Claim → `degraded_mode=fact_only` 成功；否则 `waiting_review` + workflow ReviewTask；审核 approve/return/downgrade_to_fact_card/reject 可恢复或取消；预算 `adjust` 贷记）。

## 5. 报告、审核与产品输出

- [x] 实现 ReportAssembler、CitationResolver 和 GuardrailEngine（ReportAssembler 从 Blackboard 投影 report-draft，摘要数字来自 verified Claim、核心判断关联 Analysis ID、fact_only 降级事实卡片；GuardrailEngine 6 条规则带 pass/fail/warn 与修复建议；CitationResolver 按角色与来源等级返回 full/excerpt/entry）。
- [-] 已实现事实卡片审核、发布、撤回状态转换、当前版本查询及角色分离；历史替代版本、批量审核和完整审核中心仍待完成。
- [-] Review RBAC 已覆盖 reviewer/publisher/admin；职责分离的组织级策略和异常审批仍待完成。
- [x] 实现每日 Top 10 简报和稳定重放（BriefService 按 brief_score 排序，同 Event 最新版本、同公司最多 2 条（critical 例外）、保存候选集/分数/规则版本/顺序不调 Agent；GET /api/v1/briefs/daily）。
- [x] 实现报告版本差异和证据跳转（管理后台报告详情对接 `GET /reports/{a}/diff/{b}`；事件 Claim 可跳转 `GET /evidence/{id}` 高亮原文摘录）。
- [-] 构建事件列表、事件详情、审核中心和运行质量前端；管理后台七 tab 已加深：来源同步/启停/种子、事件证据定位、审核详情与 workflow 降级决定、报告详情/流转/差异、工作流预算与节点尝试/启动与 resume、简报日期选择、审计；无障碍与独立 SPA 工程化仍待评估。

## 6. 评估与上线准备

- [x] 建立单元、API、Repository、消息、Revision、聚类、RSS、PDF、认证、审核、Agent、工具网关、预算、幂等、Blackboard、报告装配、Guardrail、每日简报、持久化代理、迁移、离线评估、安全基线、节点重试、工作流降级与恢复、Fetcher/种子源/守卫、管理后台 API、LLM 配置、来源调度等测试；`pytest --collect-only` 当前 **338** 项（2026-07-23，含 `tests/test_workflow_api.py`、新增 `tests/test_workflow_auto_trigger.py` 与 `tests/test_admin_metrics.py`）；`tests/conftest.py` 全局强制 `FINSIGHT_REPOSITORY=memory` 并默认关闭工作流自动触发，避免本机 postgresql 环境让 `create_app()` API 测试假红。
- [x] Ruff、JSON 语法、Markdown 链接和 Compose 配置检查通过。
- [x] 完成 Uvicorn `/health` 烟雾测试。
- [x] 建立五类事件标注集（29 条正/反/边界样本）与离线评估任务（Assessor 跑分类/实体/key_fields/引用四指标，当前标注集基线全部 PASS：分类 100%、实体 100%、字段召回 100%、引用 100%）。
- [-] 分类/实体/key_fields/引用 Assessor 与冻结集 `mvp-frozen-v1`（3 样本，`overall_passed=True`）已落地；`local-quality-contract-v1` 提供一致性/谣言本地证据（非生产验收）；标注集扩展与生产影子门禁仍待完善。
- [-] 市场验证 Stub 已实现 1/3/5/20 日契约、未来数据拒绝与 `mvp_acceptance` 可重放 Stub（`build_acceptance_market_stub`，明确非真实行情）；真实行情验收未启用。
- [-] 完成显式 Alembic 初始 DDL、空库升级/降级与旧 `create_all` 基线前滚测试；真实 PostgreSQL/Redis 容器恢复演练仍待 Docker daemon 可用。
- [x] 完成提示词注入、越权工具和敏感信息安全测试（14 项对抗用例覆盖文档注入不扩大权限、参数指令字段拒绝、正文审计脱敏、禁止工具参数化、Synthesizer 工具隔离、外部角色不返回全文、交易措辞拦截、未来证据不可进入工作流）。
- [-] 离线 `scripts/shadow_run.py` + `scripts/mvp_acceptance.py` 可跑通（确定性 Stub）；生产影子运行、SLA 校准、备份和回滚演练仍待完成。

## 7. 下一交付批次：专业化前端工程

目标：以独立 `web/`（Vite + React + TypeScript）重建管理端，生产由 FastAPI 托管 `web/dist`，开发用 Vite 代理 `/api`。

### 7.1 前端工程与托管

- [x] 新建 `web/`：Vite、React、TypeScript、React Router、TanStack Query、Vitest、oxlint。
- [x] 设计系统（tokens + 研究工作台布局）与 AppShell / 鉴权会话。
- [x] FastAPI `/admin` 托管 SPA；Docker 多阶段构建前端产物；CI 跑 `npm ci/lint/test/build`。

### 7.2 核心页面

- [x] 运营总览：待审积压、异常来源、等待恢复工作流快捷入口。
- [x] 审核工作台：筛选、SLA、双栏 Claim + Evidence 轨道、确认对话框决定。
- [x] 事件：`key_fields` / `confidence` / `missing_required` 与证据展开。
- [x] 报告：结构化 `content` / `provenance`、版本 diff、角色化流转。

### 7.3 运维页面

- [x] 来源：种子、同步结果、启停、采集间隔与许可策略（`license`）编辑。
- [x] 文档保留：按 `document_id` 软删 / `retention_hold` / purge（admin）。
- [x] 工作流：节点时间线、预算条、恢复/降级、Blackboard 折叠。
- [x] 简报：分数构成；审计：action 筛选与对象跳转。

### 7.4 验证

- [x] `cd web && npm test && npm run build`；后端 pytest / ruff；SPA 路由回退测试。
- [ ] 手工覆盖登录、审核决定、证据展开、报告流转与窄屏布局（部署后执行）。

完成条件：`/admin` 提供 React SPA；开发代理与生产托管均可用；核心审核/事件/报告路径可结构化展示后端字段；前端构建进入 CI。

## 8. 改进项

详细分析、优先级、建议方案和完成条件见[改进项 Backlog](./08-improvement-backlog.md)。当前需要纳入后续排期的重点：

- [-] P0：Alembic 已覆盖 MVP 表与迁移 parity；DD-30 §12 跨域/无物理 FK 清单与只读孤儿巡检已落地（CI memory `--fail-on-findings`）；§13 查询计划评审 + `ix_events_occurred_at_id`；§14 MVP ER 图（32 表、逻辑引用）；同 schema 物理 FK 可选补齐仍待完成（见 IMP-010）。
- [-] P0：PostgreSQL 事务 Repository、Outbox/Inbox 已实现；生产 Compose 集成演练待可用 Docker daemon。
- [-] P0：已完成错误信封、Request ID、请求 Hash、基础 RBAC、用户 provisioning、审计查询和报告状态转换；事件/报告/来源/`ingest_runs`/LLM providers 游标分页与过滤白名单已落地；OpenAPI 已含 DD-00 ErrorEnvelope、共享 Error* 组件，**全部写路径**（含 create workflow）已挂 `$ref` 错误响应；新增 `GET /api/v1/admin/metrics` 运营指标端点，聚合工作流成功率、模型调用成本/延迟、来源健康、人工审核率、死信/Outbox、用户与引用完整率（claims with evidence / total claims）；完整审核 API 仍待完成。
- [-] P0：本地 Artifact/Revision/HTML Block Locator 已落地；PDF/OCR/表格与生产对象存储仍待完成。
- [-] P0：五类事件 Schema/确定性 key_fields/Claim 模板已落地；复杂字段抽取与标注正例已覆盖，扩集与实体主数据对齐仍待完成。
- [-] P0：提示词注入/越权工具/正文脱敏等安全基线测试已落地；CitationResolver + evidence API 已按角色限制正文；Source.`license` 与 Admin 来源编辑已落地；Document 软删/`retention_hold`/`purge`（含可配置最短软删保留窗）Admin API 与 SPA「文档保留」页已落地；软删即归档，source worker 已挂定时自动 purge（`retention:auto_purge`，`FINSIGHT_DOCUMENT_PURGE_INTERVAL_SECONDS`）。
- [-] P1：EventMatcher/自动合并路由与实体主数据已部分落地；错误拆分、别名评分校准仍待完成。
- [x] P1：实现 LangGraph 工作流、模型网关、预算、检查点和人工审核恢复（降级/重试/失效传播已落地；真实供应商仍待确认）。
- [-] P1：离线 Assessor、冻结集与 shadow/mvp_acceptance 脚本已落地；质量仪表盘与生产影子流程仍待完成。
- [-] P1：管理后台 React SPA 核心页已交付；无障碍与窄屏手工验收仍待完成。
- [ ] P2：在有真实规模数据后评估 OpenSearch、ClickHouse、Kafka 或 Temporal。

改进项只有在对应设计、实现、测试和验收证据同时完成后才能关闭。

## 9. 外部依赖与待决策

| 事项 | 当前状态 | 负责人待定 | 对进度的影响 |
| --- | --- | --- | --- |
| 首个交易所公告源及授权 | 未确认 | 产品/数据 | 阻塞真实采集适配器 |
| 财务和行情数据源 | 未确认 | 研究/数据 | 阻塞 Company Agent 和市场评估 |
| 模型供应商与预算 | 未确认 | 技术/产品 | 阻塞真实 Agent 接入 |
| PDF/OCR 组件 | 未确认 | 技术 | 阻塞公告稳定证据定位 |
| 授权正文展示范围 | 未确认 | 合规/产品 | 阻塞审核页面展示规则 |

## 10. 更新规则

- 任务完成后勾选，并同步测试数量或其他验收证据。
- 任务范围变化时先更新需求、详细设计或 ADR，再调整清单。
- 部分实现使用 `[-]`，并在同一项说明剩余工作。
- 不把“已编写设计”标记为“已实现功能”。
