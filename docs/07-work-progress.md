# 工作进度清单

> 最后更新：2026-08-15（WP-08 Agent Runtime 已交付，含动态研究工作台前端）。状态符号：`[x]` 已完成，`[ ]` 待完成，`[-]` 进行中或部分完成。

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

- [x] 建立单元、API、Repository、消息、Revision、聚类、RSS、PDF、认证、审核、Agent、工具网关、预算、幂等、Blackboard、报告装配、Guardrail、每日简报、持久化代理、迁移、离线评估、安全基线、节点重试、工作流降级与恢复、Fetcher/种子源/守卫、管理后台 API、LLM 配置、来源调度、Document Parser、Semantic Chunker、Disclosure Group、Pipeline 文档智能接入、Embedding 生命周期与语义去重、pgvector 语义召回、Hybrid Retrieval 向量召回与过滤等测试；`uv run pytest` 当前 **394 passed，1 skipped**（2026-07-30，新增 `tests/test_fusion.py` 与 `tests/test_retrieval.py` hybrid 召回用例）；`tests/conftest.py` 全局强制 `FINSIGHT_REPOSITORY=memory` 并默认关闭工作流自动触发，避免本机 postgresql 环境让 `create_app()` API 测试假红。
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
