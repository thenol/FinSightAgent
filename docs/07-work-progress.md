# 工作进度清单

> 最后更新：2026-08-16（进入真实研究闭环生产化准备阶段）。状态符号：`[x]` 已完成，`[ ]` 待完成，`[-]` 进行中或部分完成。

## 1. 方案与详细设计

- [x] 完成总体方案、产品需求、功能架构和 MVP 验收标准。
- [x] 完成数据接入、事件中心和物理数据模型详细设计。
- [x] 完成证据中心及 Fact Checker 输入/输出 Schema。
- [x] 完成研究工作流及 Company、Skeptic、Synthesis Schema。
- [x] 完成报告审核、评估和可观测详细设计。
- [x] 建立 ADR，接受模块化单体、PostgreSQL 真值源和延后复杂基础设施等决策。
- [-] RSS 采集、条件请求、域名白名单、HTML/PDF 解析和同步服务已实现；MarketMind 风格 Fetcher 工厂、RSS 种子源、robots/限速守卫与 RSSHub 路由已接入；HTML 正文抽取已改为 article/密度择优并清洗脚本噪音（`html_text`，证据 API 展示前 scrub）；修复 RSS 短摘要被 1.0 硬阈值误丢弃的回归 bug（`total_candidate_len < 200` 时保留全部候选）；首个交易所 S 级官方 API 与 OCR 方案仍待确认。
- [x] Document Intelligence & Indexing 基础（WP-04）已落地：`ParsedDocument`/`DocumentBlock`/`DocumentChunk`/`DisclosureGroup` 领域模型、对应 PostgreSQL 表与 Alembic 迁移；`DocumentParser` 复用 `DocumentBlockReader` 生成结构化块；`SemanticChunker` 按 `event_description`/`financial_impact`/`risk`/`footnote`/`background` 切分；`EventMatcher` 使用 `disclosure_group_id` 作为优先召回信号；`EventResearchPipeline` 在文档入库后自动调用 `DocumentIntelligenceService.process()`。
- [x] Embedding 生命周期与 pgvector 语义召回（DOC-003）已落地：`EmbeddingRecord` 领域模型与 `ingestion.embedding_records` 表；`EmbeddingService` 支持 `DeterministicEmbeddingProvider`（测试/离线）和 `OpenAIEmbeddingProvider`（生产）；按 `chunk_id` + `model_version` 幂等生成/复用；`DisclosureGroup` 新增 `representative_embedding` 与 `embedding_model_version`；`DisclosureGroupService` 在精确 canonical hash 未命中时，PostgreSQL 路径使用 pgvector `<=>` 余弦距离做 TOP-K 向量召回，SQLite/内存路径回退到应用层 brute-force；Docker Compose 改用 `pgvector/pgvector:pg16` 镜像。剩余：全量重建、蓝绿切换、失败恢复和版本回滚。
- [x] Hybrid Retrieval 基础（RAG-001/002/003/005 部分）已落地：定义 `RetrievalRequest`/`RetrievedItem`/`CitationCandidate`/`RetrievalTrace` 领域契约，`RetrievalRequest` 新增 `retrieval_mode` 支持 `vector`/`lexical`/`hybrid`，`RetrievedItem` 新增 `backend`/`backend_scores`；`Repository.find_similar_document_chunks()` 支持 PostgreSQL pgvector JOIN 召回与 SQLite/内存 brute-force 降级；`Repository.find_document_chunks_by_keywords()` 支持 PostgreSQL `to_tsvector`/`ts_rank_cd`/GIN 索引与 SQLite/内存 brute-force 降级；`FusionService` 支持 RRF 与 weighted-score 融合、min-max 归一化、chunk_id 去重、来源多样性裁剪；`RetrievalService` 支持 hybrid 模式并行调用 vector/lexical 后走 Fusion。新增 `migrations/versions/20260730_0017_document_chunks_text_search_gin.py`。剩余：Graph/Structured SQL/Time-series 检索路、Query Understanding/Planner、Retrieval Control Plane、高级 Reranker/Cross-encoder。

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
- [x] 实现 ClaimNormalizer、ClaimMatcher 和 ConflictDetector；规范化、受控谓词、事实指纹、来源独立性、六类冲突与严重度已完成，EvidencePolicyService 决策与 claim_evidence/conflicts 表已落库，claim 指纹去重已生效；**EvidenceService 在注册 Claim 后调用 ConflictDetector，critical 冲突更新双方 Claim 为 `conflicted` 并持久化 `ConflictRecord`，`FactCardService` 对冲突 Claim 生成 `needs_review` 报告及 `CLAIM_CONFLICT` 审核任务**。
- [x] 实现确定性的 EvidencePolicyService。
- [x] 接入 LangGraph Checkpointer 和 Blackboard Repository。
- [-] 实现 Supervisor、预算账本、节点重试和局部重跑；LangGraph 图编排、确定性 Supervisor 路由、Blackboard 字段填充、6 维预算账本（reserve/settle/release 软硬阈值+节点上限）、节点幂等键 `workflow_id+node+input_hash`（重放复用不重复副作用）与 Blackboard 字段写入所有权乐观锁已完成；**节点重试退避**（`MODEL_TRANSIENT` 最多 2 次、`OUTPUT_SCHEMA_INVALID` 1 次、attempt_no 递增）与**局部重跑失效传播**（`invalidation.py` 映射表 + `invalidate_node_attempts` + `WorkflowService.resume`）已完成；2026-07-23：`POST /events/{id}/workflows` 默认 `execute=true` 同步跑图，并新增 `POST /workflows/{id}/run`（pending→执行）与 Admin「启动运行」；新增高重要度事件自动进入工作流：`EventResearchPipeline` 在创建事件后，若 `importance >= FINSIGHT_WORKFLOW_AUTO_IMPORTANCE_THRESHOLD`（默认 0.7）且非 dormant/archived，自动创建 `pending` workflow run（trigger_id=auto），由 worker 或 Admin 启动执行。
- [x] 接入 Fact Checker、Company Analyst、Skeptic 和 Synthesizer（四个 Agent 节点已实化，输出经 Pydantic Schema 校验，区分事实/假设/推论，数值走工具）；2026-07-23：LLM 密钥解密失败时回退 DeterministicProvider 并审计，避免本机跑图卡死；**Company/Skeptic/Synthesizer 节点现在优先解析 LLM `response.payload` 到对应 Pydantic Schema，解析失败再回退原有确定性输出**。
- [x] 实现 Agent 工具白名单、`as_of` 校验和调用审计（ToolGateway 按 DD-50 §12 鉴权、as_of 越界拒绝、正文当不可信数据隔离、tool_calls 审计表）。
- [x] 实现事实卡片降级及人工审核恢复（预算硬限有 verified Claim → `degraded_mode=fact_only` 成功；否则 `waiting_review` + workflow ReviewTask；审核 approve/return/downgrade_to_fact_card/reject 可恢复或取消；预算 `adjust` 贷记）。

## 5. 报告、审核与产品输出

- [x] 实现 ReportAssembler、CitationResolver 和 GuardrailEngine（ReportAssembler 从 Blackboard 投影 report-draft，摘要数字来自 verified Claim、核心判断关联 Analysis ID、fact_only 降级事实卡片；GuardrailEngine 6 条规则带 pass/fail/warn 与修复建议；CitationResolver 按角色与来源等级返回 full/excerpt/entry）；**Guardrail 未通过且 `review_required=true` 时工作流进入 `waiting_review` 并创建 workflow 审核任务，阻断性失败标记 `failed`；通过后调用 `FactCardService.create_from_draft()` 持久化完整报告快照，fact_only 降级也会生成事实卡片**。
- [-] 已实现事实卡片审核、发布、撤回状态转换、当前版本查询及角色分离；历史替代版本、批量审核和完整审核中心仍待完成。
- [-] Review RBAC 已覆盖 reviewer/publisher/admin；职责分离的组织级策略和异常审批仍待完成。
- [x] 实现每日 Top 10 简报和稳定重放（BriefService 按 brief_score 排序，同 Event 最新版本、同公司最多 2 条（critical 例外）、保存候选集/分数/规则版本/顺序不调 Agent；GET /api/v1/briefs/daily）。
- [x] 实现报告版本差异和证据跳转（管理后台报告详情对接 `GET /reports/{a}/diff/{b}`；事件 Claim 可跳转 `GET /evidence/{id}` 高亮原文摘录）。
- [-] 构建事件列表、事件详情、审核中心和运行质量前端；管理后台七 tab 已加深：来源同步/启停/种子、事件证据定位、审核详情与 workflow 降级决定、报告详情/流转/差异、工作流预算与节点尝试/启动与 resume、简报日期选择、审计；无障碍与独立 SPA 工程化仍待评估。

## 6. 评估与上线准备

- [x] 建立单元、API、Repository、消息、Revision、聚类、RSS、PDF、认证、审核、Agent、工具网关、预算、幂等、Blackboard、报告装配、Guardrail、每日简报、持久化代理、迁移、离线评估、安全基线、节点重试、工作流降级与恢复、Fetcher/种子源/守卫、管理后台 API、LLM 配置、来源调度、Document Parser、Semantic Chunker、Disclosure Group、Pipeline 文档智能接入、Embedding 生命周期与语义去重、pgvector 语义召回、Hybrid Retrieval 向量召回与过滤等测试；`uv run pytest` 当前 **506 passed，1 skipped**（2026-08-16，含 EventTypeRegistry 治理用例）；`tests/conftest.py` 全局强制 `FINSIGHT_REPOSITORY=memory` 并默认关闭工作流自动触发，避免本机 postgresql 环境让 `create_app()` API 测试假红。
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

不受当前交付范围限制的金融智能平台目标、能力域和建设依赖见
[平台待开发清单](./10-platform-development-backlog.md)；后续平台方案以该清单为长期规划基线，
不因 MVP 或当前数据规模降低目标架构标准。

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
| 模型供应商与预算 | [-] DeepSeek provider 已配置并通过连接测试；真实预算、SLA 与供应商切换策略仍待确认 | 技术/产品 | 不再阻塞 Agent 接入，但预算治理仍待完成 |
| PDF/OCR 组件 | 未确认 | 技术 | 阻塞公告稳定证据定位 |
| 授权正文展示范围 | 未确认 | 合规/产品 | 阻塞审核页面展示规则 |

## 10. 更新规则

- 任务完成后勾选，并同步测试数量或其他验收证据。
- 任务范围变化时先更新需求、详细设计或 ADR，再调整清单。
- 部分实现使用 `[-]`，并在同一项说明剩余工作。
- 不把“已编写设计”标记为“已实现功能”。

## 11. 下一交付批次：事件影响分析（Impact Analysis）

目标：让系统能对宏观/重大事件做经济金融影响传导分析，并以图+表形式展现，例如“美联储加息 25BP”→ 影响链 → 银行/地产/成长股等板块方向与强度。

### 11.1 后端

- [x] 修复 `DefaultReviewerAgent.operation` 与 `LLM_AGENT_KEYS` 绑定 key 不一致 bug。
- [x] 扩展事件类型词表：新增 `macro_policy`（利率决策/货币政策），支持加息/降息/FOMC/央行/LPR/存款准备金率等关键词，并添加对应 key_fields 抽取。
- [x] 新增 `ImpactAnalysis` 领域模型、`impact_analyses` 表与 Alembic 迁移，版本链挂在 Event 上。
- [x] 新增 `app/analysis/schemas.py`：版本化 `ImpactAnalysisOutput`（transmission_chains / impacts / macro_assumptions / watch_items / summary）。
- [x] 新增 `ImpactAnalystAgent`（operation=`impact_analysis`）与 `ImpactAnalysisService`；LLM 失败时降级为规则模板输出并标记 `degraded=true`。
- [x] 新增 API：`POST /api/v1/events/{id}/impact-analysis`、`GET .../impact-analysis`、按版本列表查询。
- [x] `FactCardService` 在事实卡片发布（`published`）后自动为高重要度事件生成影响分析：
  - 接入点覆盖 `create`（claim 已 verified 直接 published）、`create_from_draft`（workflow 降级为 fact_only 直接 published）和 `transition`（approved→published）。
  - 触发条件：`FINSIGHT_AUTO_IMPACT_ANALYSIS_ENABLED=true`（默认）、事件 `importance >= FINSIGHT_AUTO_IMPACT_ANALYSIS_IMPORTANCE_THRESHOLD`（默认 0.7）、事件非 `dormant`/`archived`、且该事件尚无影响分析。
  - PostgreSQL 环境下写入 Outbox（`impact_analysis.requested.v1`），由独立 `impact-analysis` worker 异步消费生成；内存/测试环境下保持同步生成。
  - 生成失败被吞掉，不阻塞报告发布；异步路径支持指数退避与死信。
- [x] 新增 `ImpactAnalysisWorker` 与 `uv run python -m app.worker impact-analysis` 命令。
- [x] 修复 `LlmAgentBindingRequest` 的 `agent_key` pattern 未包含 `impact_analysis` / `default_reviewer` 的 bug。
- [x] 优化 `ImpactAnalystAgent` system prompt：补充完整输出 schema、枚举值、约束与中文要求，提升真实 LLM 输出质量。
- [x] 后端测试覆盖：macro_policy 分类、schema 校验、版本化、降级、API 权限、自动触发条件与防重复、低重要度/禁用/失败路径、异步 worker 消费与死信、API pending 202 状态。
- [x] 真实 LLM 验证：修复 OpenAI-compatible/Anthropic provider 向解析后的 JSON 注入 `operation`/`input` 导致 `extra='forbid'` schema 校验失败的问题；使用 DeepSeek `deepseek-chat` 重新生成“美联储加息 25BP”事件影响分析，确认 `degraded=false`、`model_run_id` 已关联、输出包含完整传导链与板块影响。

### 11.2 前端

- [x] 引入 `echarts`。
- [x] 事件详情页新增「影响分析」面板：
  - 传导图：echarts `graph`（force layout），节点颜色映射利好/利空，节点大小映射强度，边标签显示传导机制。
  - 板块影响表：对象/类型/方向/强度/时域/置信度/依据，按影响强度排序。
  - 顶部 summary + watch_items + degraded 提示。
- [x] 空状态提示：说明系统会在事实卡片发布后自动为高重要度事件生成影响分析，并提供手动生成按钮。
- [x] 异步生成中状态：当后端返回 HTTP 202 `{"status":"pending"}` 时显示「系统正在自动生成影响分析，请稍候…」并每 3 秒轮询。
- [x] 前端测试：ImpactAnalysis → echarts nodes/edges 的纯函数转换测试。

### 11.3 文档

- [x] 新增 `docs/design/schemas/impact-analysis-output.schema.json`。
- [-] 更新 `AGENTS.md` 如模块约定有变化（模块边界未变，无需更新）。

### 11.4 验证

- [x] `uv run pytest -q` 全绿（新增 `tests/test_impact_analysis_worker.py` 5 个用例；当前 `tests/test_impact_analysis.py` 11 个用例 + `tests/test_impact_analysis_auto_trigger.py` 10 个用例；全量 `420 passed / 1 skipped`）。
- [x] `uv run ruff check .` 全绿（变更文件检查通过；存量测试文件 lint 问题未在本次范围修复）。
- [x] `cd web && npm run build && npm test -- --run` 全绿（echarts 引入后构建成功）。
- [x] 手动验证：ingest“美联储加息 25BP”样例 → 事件分类为 `macro_policy` → FactCard published 后自动生成影响分析 → 前端展示传导图与板块影响表。

完成条件：宏观事件可进入研究管道；影响分析可独立版本化生成；前端能用图+表展示事件对股市、板块、宏观变量的传导影响；所有变更通过测试与 lint。

## 11.5 影响分析专业化升级（当前开发中）

- [-] 已新增 `ImpactAnalysisOutputV2`：因果节点/边、证据绑定、情景、影响维度和质量报告契约。
- [-] 已新增 `validate_impact_output()` 结构门禁，检查悬空边、情景引用、目标路径、证据覆盖和无证据定量结论。
- [x] 已接入 V2 快照/质量报告字段、PostgreSQL/内存 Repository 和 Alembic `20260816_0022`；Agent 优先解析 V2 并由服务层投影兼容字段。
- [-] 已实现 `draft → needs_review → approved → superseded` 基础状态流转与审核 API，降级结果不得自动批准；事务并发测试待补齐。
- [-] 已新增并接入 `ImpactContextBuilder`、`MechanismGenerator` 和 `ImpactCritic`：按事件 `as_of` 过滤 verified Claim、事实卡片和混合检索结果，并执行因果边、循环和目标图谱一致性检查；Expectation Analyzer、Scenario Engine 和 Synthesizer 尚未拆分。
- [-] 已实现稳定分层因果 DAG、节点类型语义、情景切换、时间范围过滤、节点路径聚焦、因果边证据入口和版本对比；证据详情权限化展示待完善。

详细执行项见 [Impact Analysis V2 待开发清单](./11-impact-analysis-v2-todo.md)。

## 12. 下一交付批次：Hybrid Retrieval（WP-07）

目标：在已有向量/关键词/混合 DocumentChunk 检索基础上，补齐 Graph-like、Structured SQL、Time-series 检索与 Query Planner，并暴露统一检索 API。

### 12.1 后端

- [x] 撰写 `docs/design/05-hybrid-retrieval.md` LLD。
- [x] 新增 `app/retrieval/planner.py`：规则化 Query Planner，支持实体解析、时间窗提取、事件类型识别、意图分类与后端选择。
- [x] 扩展 `RetrievalService`：新增 `graph` / `sql` / `timeseries` / `planned` 检索模式；`planned` 模式调用 Planner 生成多路计划并用 `FusionService` 融合。
- [x] Graph-like 检索基于 Event/Entity/Document 关系在 PostgreSQL 关系模型上实现，不引入图数据库；按跳数衰减打分。
- [x] Structured SQL 检索按事件类型、时间范围、重要度过滤事件。
- [x] Time-series 检索按时间窗倒序列出事件及相关文档块。
- [x] 新增 `POST /api/v1/retrieval/retrieve` API，支持全部模式与 `as_of` 过滤。
- [x] 补齐 `SqlAlchemyRepository.get_entity/save_entity` 包装方法，使 Planner 在 PostgreSQL 环境下可用。
- [x] 新增 `tests/test_retrieval_planner.py`（4 用例）、`tests/test_retrieval_api.py`（5 用例）。

### 12.2 文档

- [x] 新增 `docs/design/05-hybrid-retrieval.md`。

### 12.3 验证

- [x] `uv run pytest -q` 全绿（全量 `433 passed / 1 skipped`，新增 9 个检索用例）。
- [x] `uv run ruff check .` 变更文件通过。
- [x] `cd web && npm run build && npm test -- --run` 全绿。
- [x] 手动调用 `POST /api/v1/retrieval/retrieve` planned 模式返回带 `RetrievalTrace` 的多路融合结果。

完成条件：检索 API 支持多后端计划执行；Graph/SQL/Time-series 召回可落地；新增测试覆盖；进度文档同步更新。

## 13. 下一交付批次：Agent Runtime（WP-08）

目标：在现有固定 LangGraph 工作流基础上，建设动态 ResearchPlan、Agent Registry 与可复用的 Agent Runtime，使系统能根据问题自动规划检索、分析与审核步骤，而不是硬编码节点序列。

### 13.1 已交付

- [x] 撰写 `docs/design/08-agent-runtime.md` LLD。
- [x] 定义 `ResearchPlan`、`ResearchTask`、`AgentRegistration` 领域模型（`app/domain.py`）。
- [x] 新增 `app/agents/registry.py`：声明式注册 Specialist Agent（fact_checker、company_analyst、skeptic、synthesizer、planner、retriever、impact_analyst、market_analyst、industry_analyst、regulatory_analyst），声明能力、输入/输出 Schema、允许工具、预算配置和质量门。
- [x] 新增 `app/workflows/planner.py`：基于规则的问题分类与默认任务 DAG 模板，生成动态 ResearchPlan；支持可选 LLM 增强，调用 `ModelGateway` 的 `plan` operation 输出受 Schema 约束的任务调整建议。
- [x] 新增 `app/workflows/dynamic.py`：`DynamicWorkflowService` 执行动态 DAG，复用 `WorkflowRun`、`Blackboard`、`BudgetManager`、`NodeAttempt`、`ReviewTask`；任务状态独立，失败语义与固定工作流一致。
- [x] 新增 `POST /api/v1/research` API：提交研究问题，返回 ResearchPlan 与 WorkflowRun；`POST /api/v1/research/{id}/execute` 启动动态计划；`GET /api/v1/research/{id}`、`GET /api/v1/research/{id}/tasks`、`GET /api/v1/research/{id}/blackboard` 查询计划、任务与黑板输出；`GET /api/v1/research` 支持按状态筛选与游标分页。
- [x] 新增 `ResearchPlanListResponse` / `ResearchBlackboardResponse` schema，补充 `Repository.list_research_plans` 实现。
- [x] 管理后台新增「动态研究」页面（`web/src/pages/ResearchPage.tsx`）：左侧研究计划列表、右侧计划详情；详情页含「任务时间线」与「研究黑板」两个 Tab，可查看任务状态/Agent/依赖/输出快照，以及 Blackboard 的 JSON 输出；支持在研究计划状态为 `ready`/`pending`/`waiting_review` 时点击「执行研究」。
- [x] 扩展 `app/workflows/blackboard.py` 字段所有权表，增加 `research_plan`、`task_outputs`、`plan_status`。
- [x] 扩展 `app/platform/repository.py` 与 `app/platform/db_models.py`，新增 `agent_registrations`、`research_plans`、`research_tasks` 表与迁移 `20260814_0019_agent_runtime.py`；PostgreSQL 路径已实现真实 ORM 持久化。
- [x] 补齐测试：`tests/test_agent_registry.py`、`tests/test_research_planner.py`（含 LLM Planner 用例）、`tests/test_dynamic_workflow.py`、`tests/test_research_api.py`。

### 13.2 验证

- [x] `uv run pytest -q` 全绿（463+ passed / 1 skipped，WP-08 新增 research API 与 dynamic workflow 用例均通过）。
- [x] `uv run ruff check .` 变更文件通过（WP-08 新增/修改文件无 lint 错误）。
- [x] `cd web && npm run build && npm test -- --run` 全绿；新增「动态研究」导航与页面通过构建。
- [x] 手动调用 `POST /api/v1/research` 可生成计划并执行成功；管理后台 `/admin#/research` 可查看计划列表、任务时间线与黑板输出。

### 13.3 后续迭代

- [x] PostgreSQL 持久化：已切换到 ORM 模型读写。
- [x] 接入真实 LLM Planner：已实现，受 Schema 校验。
- [x] 扩展 Specialist Agent：已新增 Market、Industry、Regulatory；Macro、Citation Auditor 等后续补充。
- [x] 动态研究工作台前端：在 Admin SPA 展示 ResearchPlan、任务状态、Blackboard 输出。
- [ ] 动态执行与 LangGraph 检查点深度集成，支持更细粒度的暂停/恢复。
- [ ] 研究记忆与分层 Blackboard：区分运行时状态、工作区记忆和已验证知识。

完成条件：研究问题可生成动态计划；Agent Registry 可注册/查找 Specialist Agent；动态计划可被工作流引擎执行并产生可追溯结果。

## 14. 下一交付批次：事件分流 v2（DD-21）

目标：事件门控从"类型白名单"改为"相关性 + 重要度评分"，类型降级为输出标签；规则词表外的重大事件（地缘冲突、自然灾害等）不再被静默归档。

### 14.1 已交付

- [x] 撰写 `docs/design/21-event-triage-v2.md` LLD 与 ADR-021。
- [x] Router 契约升级 v2：`RouterOutput` 改为 `relevance`（relevant/irrelevant/unsure）+ `event_type`（标签）+ `importance` + `confidence`；新增 `ROUTER_SYSTEM_PROMPT` 明确开放分类职责；`ROUTER_SCHEMA_VERSION=v2`。
- [x] 门控语义变更：`irrelevant` → archived（`out_of_scope` 语义重定义为"无经济相关性"）；`unsure` → dormant；`relevant` 进入研究管道。确定性回退与 v1 行为完全一致（不放行未知类型）。
- [x] 候选类型（candidate type）：LLM 产出的一等词表外 snake_case 标签落库为 `event_type`，`classifier_version=event-router-v2-candidate`，强制 `needs_review`（`candidate_type_confirmation`）；重要度类型基线由 Router 建议值替代（`ImportanceCalculator.type_baseline_override`）；非法标签（保留字冲突/非 snake_case）自动降级 unsure；候选事件 Claim 生成走 legacy 谓词回退，不崩溃；候选事件不进每日简报（`BriefService` 过滤）。
- [x] 新增一等类型 `geopolitical_event`：关键词（开战/宣战/制裁/军演/政变/恐怖袭击等）、key_fields 抽取（parties/region/action/commodities）、importance 基线 0.95、优先级紧随 macro_policy；新增受控谓词 `geopolitical_action`，`PREDICATE_VERSION` 升至 `controlled-v3`。
- [x] `event.router_decision` 审计扩展：`relevance`/`importance`/`is_candidate_type`/`router_schema_version`。

### 14.2 验证

- [x] `tests/test_event_router.py` 重写为 v2 契约（12 用例，含 LLM stub 候选类型、非法标签降级、irrelevant 归档、候选类型端到端）。
- [x] `tests/test_classifier.py` 新增 geopolitical 抽取/必填缺失/Claim 模板用例（3 个）。
- [x] `tests/test_briefs.py` 新增候选类型排除用例。
- [x] `uv run pytest -q` 全绿；已修复 15 个存量测试文件 lint 错误，当前全仓 `uv run ruff check .` 通过并纳入 CI 门禁。

### 14.3 后续迭代

- [x] 候选类型积累阈值统计与升格操作（`FINSIGHT_CANDIDATE_TYPE_PROMOTION_THRESHOLD`，默认 5；`events.event_type_registry` + accept/reject API + Admin 词表页）。
- [ ] 审核风险分层路由（高置信低风险自动放行）。
- [ ] 重要度动态化（来源密度/传播速度/市场反馈参与计算）。
- [ ] 真实 LLM 端到端验证（DeepSeek 对"美国对伊朗开战"类样本的开放分类质量）。

完成条件：规则词表外的重大经济事件不再被静默丢弃；LLM 开放分类产出受 Schema 约束；确定性回退与 v1 完全兼容；所有变更通过测试与 lint。

## 15. 下一交付批次：全量记忆与监听重估（DD-22）

目标：移除事件入口的终态裁决——`irrelevant` 不再归档，落 `cold`（可检索、挂监听、可重估）；Agent 动作空间收敛为"记住 / 理解打标 / 决定深度 / 设置监听"。

### 15.1 已交付

- [x] 撰写 `docs/design/22-watch-triggers.md` LLD 与 ADR-022。
- [x] 事件状态机：`irrelevant`（out_of_scope）落 `cold` 而非 `archived`；`archived` 仅保留人工显式归档语义；`cold` 不触发自动工作流（pipeline）、不自动生成影响分析（publishing）、不进每日简报（briefs 同时排除 cold/archived）。
- [x] 新增 `WatchTrigger` 领域模型、`events.watch_triggers` 表与迁移 `20260815_0020`；Repository 协议 + InMemory + SqlAlchemy 三层实现。
- [x] `EventService._persist_event` 对 cold/dormant 事件自动注册两个监听条件：`source_cluster`（独立来源 ≥3）与 `source_upgrade`（更高信任等级来源）。
- [x] 新增 `ReevaluationService`：扫描 armed 触发器，命中即升级事件 cold/dormant → needs_review（`reevaluation_confirm`）、触发器 fired、审计 `event.reevaluated` 含证据；事件已不可重估时触发器自动 cancelled。
- [x] 新增 Worker 命令 `uv run python -m app.worker reevaluate`（周期扫描，`FINSIGHT_REEVALUATE_INTERVAL_SECONDS`）。
- [x] 显式声明检索不变量：Hybrid Retrieval 以 DocumentChunk 为粒度不按事件状态过滤，cold 事件文档天然可被检索与动态研究触达。

### 15.2 验证

- [x] 新增 `tests/test_reevaluation.py`（7 用例：cold 落位、触发器注册、聚集触发升级、来源升级触发、未命中保持 armed、人工归档后触发器取消、cold 不触发自动工作流）。
- [x] `uv run pytest -q` 全绿（494 passed / 1 skipped）；`uv run ruff check` 变更文件全绿。

### 15.3 后续迭代

- [ ] `market_signal` 触发器（行情异动回扫冷文档，依赖真实行情数据接入）。
- [ ] `user_query` 触发器（动态研究检索命中冷文档时触发正式事件化，需检索埋点）。
- [ ] 展示层排序视图（简报/审核按分数排序 + 可展开全部，替代状态过滤）。
- [x] 大型受控分类法治理流程（候选类型评审入词表：accept/reject API 与 Admin 页；完整 Schema 发版仍待后续）。
- [ ] 重估升级后自动重启研究管道的深度衔接（当前停在 needs_review 人工确认）。

完成条件：Router 不再自动产出终态归档；cold/dormant 事件挂监听条件并可被信号升级；重估全程留审计；所有变更通过测试与 lint。

## 16. 下一交付批次：候选类型词表治理（DD-21 §2.4）

目标：让 LLM 发明的类型标签成为可计数、可升格、可拒绝的受控词表。

### 16.1 已交付

- [x] `events.event_type_registry` 迁移 `20260815_0021` 接通 Repository 三层（protocol / InMemory / SqlAlchemy）。
- [x] 新事件落库时对开放分类标签 `upsert` 并累加 `event_count`（合并到已有事件不重复计数）。
- [x] `FINSIGHT_CANDIDATE_TYPE_PROMOTION_THRESHOLD`（默认 5）；列表接口标记 `promotion_ready`。
- [x] `GET /api/v1/event-types`、`POST .../accept`、`POST .../reject`；审计 `event_type.promoted` / `event_type.rejected`。
- [x] accepted 去掉强制 `candidate_type_confirmation`；rejected 后续同类事件落 `cold` 并挂默认 watch trigger。
- [x] Admin「事件类型」页；Compose 补 `reevaluate-worker`。

### 16.2 验证

- [x] `tests/test_event_type_registry.py` 覆盖计数、升格、拒绝、简报准入、API 与阈值配置。
- [x] `uv run pytest` **506 passed / 1 skipped**；`uv run ruff check` 变更文件通过；`cd web && npm test -- --run && npm run build` 通过。
- [x] 确定性 `scripts/shadow_run.py` + `scripts/mvp_acceptance.py` 复跑：6 PASS + 6 `NOT_PRODUCTION_VALIDATED`。真实 RSS + LLM 闭环因本机无模型密钥未执行，见 [09-eval-latest.md](./09-eval-latest.md)。

### 16.3 后续迭代

- [ ] 升格时自动补齐 EventSchema / key_fields / 标注集（当前只改运行时门控）。
- [ ] 配置真实 LLM 后抽检开放分类质量与候选类型噪声。

## 17. 下一交付批次：真实研究闭环生产化（12 周）

目标：以 A 股公司事件和宏观重大事件为双场景，将真实官方数据、Hybrid Retrieval、动态 Agent、证据约束报告和影子运行连成可验收闭环。本批次不将行情收益分析标记为生产能力，行情仅完成供应商无关接口和 `unavailable` 能力状态。

### 17.1 第 1–2 周：工程基线与真实数据入口

- [x] 修复全仓 Ruff 错误，统一 CI 与进度文档的测试/生产验证口径。
- [ ] 固化 EventTypeRegistry、cold 监听和候选类型治理的当前未提交变更。
- [ ] 接入上交所、深交所公告及人民银行/国家统计局宏观源；保存原始响应、`available_at`、许可和解析版本。
- [ ] 冻结 120 个公司事件、80 个宏观事件的人工评测集。

### 17.2 第 3–4 周：真实 Hybrid Retrieval

- [x] Structured Retrieval 查询下推 SQL，移除大范围内存过滤；Repository 的 InMemory/SQLAlchemy 路径统一支持事件类型、实体、时间范围和 `as_of` 过滤，并新增 API 回归测试。
- [x] 将当前 Graph-like 路径明确为 Relation Retrieval，补齐 Entity→Event→Document→Chunk 关联、关系路径/跳数审计和 `as_of` 版本过滤；保留 `graph` API 兼容别名。
- [x] 将 Time-series 改为正式 `MarketDataProvider` 能力接口；默认无行情源时返回结构化 `unavailable`，禁止事件时间排序伪装行情。
- [ ] 引入 `RetrievalPlanV2`、`RetrievedItemV2`、`ContextBundleV2`、Reranker 和完整检索轨迹。

### 17.3 第 5–6 周：统一动态 Agent Runtime

- [ ] 动态 ResearchPlan 使用 LangGraph Checkpointer，收敛自定义 DAG 与固定工作流的状态/恢复语义。
- [ ] 所有任务使用版本化输入输出 Schema，移除 `object()` 占位依赖和宽泛静默降级。
- [ ] Fact/Company/Industry/Regulatory Agent 消费真实检索和证据；Market Agent 无数据时只能输出 `insufficient_data`。
- [ ] Specialist 输出统一写入 Claim/Evidence/Provenance。

### 17.4 第 7–8 周：证据约束影响分析与报告

- [ ] Impact Analysis 的每条传导链和影响目标绑定 Claim、RetrievedItem 或显式 `inferred` 标记。
- [ ] 报告分离事实、推断、假设、风险和观察项；数字和结论必须可引用。
- [ ] Guardrail 增加无依据影响、能力缺失却给出确定结论、未来数据泄漏和推断冒充事实检查。
- [ ] 前端展示 Query Plan、检索轨迹、证据链、截止时间和降级原因。

### 17.5 第 9–10 周：真实模型与评测

- [ ] 使用 DeepSeek 主模型完成真实 Router、Planner、Agent 和报告抽检；Schema 失败最多修复一次，之后转人工或显式降级。
- [ ] 建立分类、实体、Recall@10、nDCG@10、引用完整率、无来源事实率、时间泄漏率、成本和延迟评测。
- [ ] 验证 candidate type 噪声、accept/reject、cold 重估和真实 LLM 与确定性 Router 的差异。

### 17.6 第 11–12 周：影子运行与生产演练

- [ ] 完成 PostgreSQL/pgvector、Redis、API、Outbox、Source、Reevaluation、Impact Analysis 和 Dynamic Research Worker 部署演练。
- [ ] 连续影子运行不少于 14 天；公司和宏观场景各形成至少 30 个真实研究结果。
- [ ] 完成重启、重复消息、模型超时、检索失败、预算耗尽、人工恢复、文档修订和删除演练。
- [ ] 形成运行仪表盘、告警、备份恢复、模型回退、索引重建和数据源故障手册。

### 17.7 本批次验收门

- [x] 全仓 Ruff 零错误，后端/迁移/前端测试通过。
- [ ] 官方源采集成功率 ≥99%；Retrieval Recall@10 ≥0.85；nDCG@10 ≥0.75。
- [ ] 时间泄漏率为 0；引用完整率 100%；无来源关键数字为 0。
- [ ] 真实模型 Schema 有效率 ≥99%；必需任务成功率 ≥95%。
- [ ] 检索 P95 ≤2 秒；异步研究 P95 ≤180 秒；连续 14 天无未处理死信。

行情收益分析、市场异动监听、Neo4j、Kafka、ClickHouse、Temporal 和完整多租户不属于本批次生产验收范围。

### 17.13 多市场行情与市场展望基础（2026-08-18）

- [x] 统一 `app.market.provider` 行情契约，增加 A/H/US、5 分钟/日线、交易日历、快照、供应商来源和 `as_of` 字段。
- [x] 增加东方财富 JSON 适配器，参考 `stock` 项目的 push2/push2his 接口和字段归一化，但不引入其 MySQL、Cookie 文件和 DataFrame 运行时依赖。
- [x] 增加 AKShare 可选适配边界、主源/回退路由和结构化 `degraded/unavailable` 能力状态。
- [x] 增加行情能力、快照和 K 线查询 API：`/api/v1/market/capabilities`、`/api/v1/market/snapshots`、`/api/v1/market/bars`。
- [x] 增加供应商回退、网络异常、OHLC 校验、未来数据拒绝和 API 参数校验测试。
- [x] 增加版本化参考证券目录和 `/api/v1/market/instruments`，首批覆盖三地宽基指数与代表 ETF。
- [x] 增加多市场交易时段日历 API，明确标记节假日源未配置时的 `degraded` 状态。
- [x] 增加可回放的市场状态服务和 `/api/v1/market/states`，输出趋势、波动、覆盖率和数据状态，不伪装成预测。
- [x] 增加版本化可解释展望基线和 `/api/v1/market/outlooks`，支持 1/3/5/20 个交易日、上涨/震荡/下跌概率、收益分位数、贡献拆解及行情不足降级。
- [x] 新增“市场展望”前端研究工作台，支持 A/H/US 切换、预测周期切换、概率与收益区间、贡献拆解和未校准状态提示。
- [x] 增加行情新鲜度质量门：日线超过 3 个自然日、5 分钟线超过 30 分钟未更新时标记 `stale_data`，展望自动降级为无方向结论。
- [x] 增加行情质量摘要服务和 `/api/v1/market/quality`，统一返回覆盖率、缺失/陈旧数量、最大延迟和结构化告警，为采集 worker 与监控接入预留契约。
- [x] 按 AKShare 官方接口接入可选 CN 回退：指数/ETF 日线与 5 分钟历史统一转换为 `MarketBar`；运行环境需额外安装 `akshare` 才启用。
- [x] 增加供应商运行健康投影和 `/api/v1/market/providers/health`，区分 configured、operational unknown/unavailable/healthy；AKShare加入 `market` 可选依赖并更新锁文件。
- [x] 增加可重放 `MarketIngestService` 采集契约：统一校验时间范围/时区/`as_of`，输出采集运行元数据、质量告警和标准化行情批次，后续可接入 ClickHouse/MinIO 存储。
- [x] 增加 `MarketBatchStore` 存储端口、本机原子归档适配器和 ClickHouse 插入适配器/DDL；新增行情归档、ClickHouse、MinIO 配置项，尚待实际 Worker 和服务连接演练。
- [x] 新增 `market-data` Worker：支持单次采集、持续循环、可配置标的/周期/回看窗口，复用采集契约与本机归档，并支持 SIGTERM 停止。
- [x] 统一行情数据入口：`EastMoneyBridgeMarketDataProvider` 读取桥接标准化 `/api/v1/market/quote/{secid}`、`/kline/{secid}` 与 `/trends/{secid}`；`.env.example`/Compose 默认使用 `bridge`，桥接模式不再静默回退直连东方财富或 AKShare，报价快照也统一通过桥接获取。
- [x] 桥接运行策略升级为“浏览器桥接唯一默认数据边界”；行情不足时前端显示“暂无方向性结论”，不再把三等分基线误显示为 33% 预测概率。
- [x] 修复本地桥接请求受 `HTTP(S)_PROXY` 影响导致 502 的问题；FinSight 对 loopback bridge 使用 `trust_env=False`，并修复桥接 Playwright 无默认 context 时无法启动采集的问题。
- [x] 将桥接启动、目标标的采集、33%问题根因、验证结果和恢复步骤整理至 [EastMoney Bridge 接入与恢复](./design/81-eastmoney-bridge-operation-and-recovery.md)。
- [x] 在市场展望页面增加“如何计算这些概率？”说明面板，并在市场数据设计文档中记录基线公式、权重、波动率尺度和数据不足规则。
- [x] 市场展望契约升级至 `schema_version=2.0` / `outlook-baseline-v2`：移除数据不足时的三等分伪概率，增加 `forecast_status`、阻断原因、样本门槛、覆盖率和最近观察时间。
- [x] 修复震荡类别无法胜出的基线公式；按 1/3/5/20 日分别要求 60/90/120/250 个交易日，并将覆盖率和日线新鲜度切换为工作日近似，周末不再误判过期。
- [x] 市场展望卡片展示实际/所需样本、覆盖率、最近行情和结构化阻断原因；后端市场专项测试与前端生产构建通过。
- [x] 修复 PostgreSQL/SQLAlchemy 路径首次保存因果图个人布局时 `new_id()` 缺少前缀导致的 500，并增加创建后更新布局的持久化回归测试。
- [-] 建立证券主数据、供应商代码映射、多市场交易日历和行业分类导入；迁移 `20260822_0029` 已持久化证券目录、版本化行业分类、标的行业成员关系和影响目标映射，启动时从数据库构建 Catalog，并已接入 `exchange_calendars` 的 XSHG/XHKG/XNYS 正式交易时段。权威分类供应商导入、历史版本换代和企业级全量标的仍待完成。
- [-] 接入 ClickHouse/对象存储行情湖仓、market-data worker、5 分钟增量采集和收盘核对；Worker 已支持 `local/clickhouse/dual` 存储模式、ClickHouse DDL 自动初始化和本地优先镜像降级，MinIO、增量缺口扫描与收盘核对仍待完成。
- [-] 接入事件影响、预期差、已定价程度和资金/量价因子；已完成 `forecast-factor-v1` 事件因子快照、时间点安全重算、来源哈希与独立查询 API。标的/行业/市场映射已升级为 proposed → approved/rejected → retired 审核状态机，只有已批准且在知识时间/业务有效期内的映射进入预测；名称命中只生成候选。预期差、已定价和资金因子仍待完成。
- [x] 市场展望缺失因子不再按 0 分冒充中性：不可用因子退出计算、其余权重归一化，并输出配置权重、实际权重、状态、置信度、来源与因子覆盖率。
- [x] 新增银行 ETF 与房地产 ETF 行业代理标的，行业影响目标可按标准代码或规范名称映射；事件贡献可从市场展望跳转至目标影响详情。
- [x] 修复市场展望前端固定 45 日窗口无法满足最低样本门的问题，1/3/5/20 日周期分别请求 120/180/240/500 个自然日并放宽至 500 条。
- [x] 修复桥接 `ok` 但仅返回 28 条缓存时不触发降级的问题；桥接按标的完整度返回结构化缺口告警，批量 K 线 `limit` 按单标的生效，不混合不同供应商或复权口径。
- [!] 2026-08-22 运行验收：沪深300桥接缓存仍只有 28 条；平台保留 28 条并返回 `probabilities=null` 与结构化告警，需通过桥接采集任务补齐历史后再重算。
- [-] 使用历史行情进行 walk-forward 校准、概率可靠性评估和模型版本发布；已完成 `forecast-evaluation-v1` 评估内核（Brier、Log Loss、命中率、覆盖率、ECE、可靠性分箱、purge/embargo 切分和温度校准最小样本门）。
- [x] 新增迁移 `20260822_0027`，持久化不可变预测运行、到期真实结果和校准版本；预测按输入哈希幂等，到期结果按预测唯一。
- [x] 新增正式预测签发、运行列表、结果结算、评估汇总、校准创建/列表 API；数据不足预测保留在覆盖率分母，无样本时覆盖率为 null。
- [x] 新增 `forecast-outcomes` Worker，按交易周期持续回填真实收益标签；前端增加“预测评估”页面，展示评估指标、可靠性分箱、样本排除原因和模型校准版本。
- [x] 新增校准版本状态转换与发布质量门：最少 200 个样本、覆盖率/Brier/ECE 阈值、训练期闭合检查；新发布版本自动退休同切片旧版本并写审计日志。
- [x] 新增迁移 `20260822_0028` 固化预测采用的校准版本；市场展望和正式预测仅应用 published 温度校准器，输出 `ready/calibrated` 与校准来源，草稿不进入线上。
- [x] 新增迁移 `20260822_0029` 和主数据治理 API：持久化行情标的、行业分类/成员关系，支持影响目标映射创建、精确候选生成、审核转换和审计；移除按规范名称直接进入预测的隐式行业匹配。
- [x] 前端新增“市场主数据”工作台，集中展示标的/分类统计、行业分类明细和影响目标映射，支持候选生成及 reviewer/admin 批准、拒绝、退役操作。
- [x] 新增 approved 影响投影回填服务、管理员 API 与一次性 `impact-backfill` Worker：扫描截至 `as_of` 可见的 approved 分析，幂等补齐贡献和目标快照，并输出 active/expired/future 诊断。贡献知识时间固定为分析批准时间，回填不向历史截面泄漏未来信息。
- [x] 历史批量预测回放数据集基础：本地行情归档升级为带规范内容哈希的 `market-archive-v2`，严格回放读取器按 `ingested_at/available_at/as_of` 过滤修订；管理员可通过 `POST /api/v1/market/forecast-replays` 按交易日批量签发并自动结算，任务按预测输入哈希幂等、限制最大槽位并写审计日志。
- [x] 历史回放校准防泄漏：预测时只能读取在 `as_of` 前已创建、已发布且训练截止早于预测时点的校准版本；未来才发布或训练窗口越界的版本不可见。
- [x] “预测评估”页面新增管理员回放控制台，显示计划槽位、新建/复用、已结算/待结算、数据不足、归档源和完整性告警；回放完成后自动刷新评估与冠军/挑战者结果。
- [x] 新增冠军/挑战者公平比较：按同一标的/预测时点/周期的已结算样本交集比较规则版本，基于 Brier、Log Loss、ECE、命中率和最小样本门输出建议，不自动切换线上模型；预测评估页面已展示决策及逐模型指标。
- [x] 新增迁移 `20260823_0030`、导入运行记录和行业分类完整快照导入：校验失败不落业务主数据，校验通过后暂存 draft/proposed，人工发布时按 `effective_from` 无缝切换旧/新成员关系；支持来源哈希幂等、审计和管理端 JSON 上传。
- [ ] 下一步为实际许可数据源实现 Connector，将供应商增量合成为完整分类快照；将回放任务升级为持久化异步 Job（进度、取消、重试、分片和 SLA），并建设带人工签发的模型版本晋级工作流。

### 17.8 因果图专业化编辑闭环（2026-08-17）

- [x] React Flow + ELK Layered 替换 ECharts 图层，支持语义节点、方向/置信度边、MiniMap 与交互聚焦。
- [x] 图表升级为研究员工作台：核心路径/置信度过滤、图例、关系详情、全屏及用户布局快照；旧版链路自动补齐影响对象节点。
- [x] 传导关系增加双层注解：图面摘要标签与可追溯的逻辑、条件、风险和证据详情。
- [x] 增加 V2.1 图接口、旧版链路只读适配、草稿派生、乐观锁编辑及个人布局快照。
- [x] 迁移 `20260817_0023` 增加编辑修订号、派生来源和布局表；全量后端与前端测试通过。

### 17.9 多事件目标组合影响（2026-08-17）

- [x] 增加标准目标注册、事件影响关系、单事件贡献和目标组合快照模型。
- [x] 增加 `ImpactAggregationService`：只使用 approved 分析，采用有界正负聚合、事件重要度/置信度/路径置信度、依赖权重和时间衰减。
- [x] 增加组合影响 Outbox 事件、Worker、目标查询/重算 API 和目标影响前端工作台。
- [x] 增加方向反转、未批准排除、时间衰减和迁移兼容测试；前端生产构建通过。
- [ ] 下一步接入标准行业分类导入、事件关系审核和多事件汇聚 DAG/时间轴。

### 17.10 未来行业影响窗口（2026-08-17）

- [x] 新增 `ForwardImpactWindow`、`ForwardCatalyst`、未来贡献和时间点快照模型，迁移 `20260817_0025` 已应用。
- [x] 支持 scheduled / conditional / hypothetical 三类催化剂，基准与压力情景隔离。
- [x] 实现 `as_of` 校验、窗口范围、自动时间粒度、条件概率和 conditional/expected 双结果。
- [x] 新增前瞻窗口 API、催化剂审核接口、Outbox 和 `forward-impact` Worker。
- [x] 前端增加行业前瞻页面，支持自定义未来日期范围和时间点状态。
- [ ] 下一步接入官方日历、指标触发器和不确定性带/DAG 专业可视化。

### 17.11 目标影响决策工作台（2026-08-18）

- [x] 新增只读 `dashboard` 聚合接口，返回事件标题、分析版本、贡献占比、时间权重、依赖权重、路径置信度和来源入口。
- [x] 新增目标影响时间线接口，支持自动/日/周/月粒度，并在回放时过滤平台知识截止时间之后产生的贡献。
- [x] 目标详情页升级为“总览与归因 / 聚合传导图 / 计算与版本”三视图；事件贡献支持点击查看通俗解释和计算拆解。
- [x] 聚合传导图接入现有 React Flow 工作台，目标视图默认只读，保留筛选、注解和证据详情能力。
- [ ] 下一步补齐维度级快照、不可变计算因子表、情景聚合和通用图布局存储。

### 17.12 全局未来事件研究日历（2026-08-18）

- [x] 完成全局研究日历的产品语义：事件目录、日期选择、当日事件与当日有效影响分区。
- [x] 确定事件修订、时区、时间精度、概率和事件实现的领域规则。
- [ ] 第一阶段实现兼容现有 ForwardCatalyst 的未来事件目录和单日研究 API。
- [ ] 第一阶段实现研究日历前端及从目标影响页带筛选进入。
- [x] 新增 FutureEvent / FutureEventRevision / FutureEventTargetImpact 规范化模型与迁移 `20260818_0026`；旧 ForwardCatalyst 保留兼容路径。
- [x] 新增未来事件创建、详情、修订状态转换 API；日历服务优先读取规范化事件，空数据时回退旧投影。
- [ ] 接入官方源适配器并补齐事件改期、取消、实现的完整审核界面。
