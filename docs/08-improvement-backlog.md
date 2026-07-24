# 改进项 Backlog

## 1. 目的与优先级

本文记录当前方案和实现仍需改进的事项，作为进度清单的补充。优先级定义：

- P0：影响数据正确性、安全性或下一交付批次，必须先解决。
- P1：影响 MVP 完整性、质量和运营效率，应在影子运行前解决。
- P2：规模化或体验优化，在真实指标证明需要后实施。

## 2. 需求与文档治理

### IMP-001：建立完整需求追踪矩阵（P0）

现状：已有 `FR-*`、`AC-*` 和 `NFR-*`，但尚未关联代码模块、API、数据库迁移和测试用例。

改进：增加机器可读或表格化追踪矩阵：`需求 → 详细设计 → API/表 → 测试 → 验收证据`。PR 修改需求时必须同步影响项。

完成条件：所有 P0 需求至少关联一个实现入口和自动化测试，不存在只有设计没有验收的 P0 项。

### IMP-002：增加文档状态与变更记录（P1）

现状：详细设计只有“初稿”状态，没有评审人、批准时间和替代版本。

改进：为 DD 文档增加负责人、状态、最后评审时间、依赖 ADR 和变更摘要；过期设计显著标记。

完成条件：进入实现的 DD 均为“已评审”，跨模块变化能够追溯到 ADR 或 PR。

### IMP-003：统一术语和枚举（P0）

现状：设计中同时使用 Fact Card、Report、ReportVersion，以及 `clm_`/Claim 等不同表达；部分状态只在单一文档出现。

改进：建立术语表和枚举注册表，明确对象、ID 前缀、状态拥有者及弃用策略。

完成条件：OpenAPI、JSON Schema、Python 枚举和数据库 CHECK 来源一致。

## 3. 数据与存储设计

### IMP-010：消除逻辑模型、DD-30 与 DDL 差异（P0）

现状：初始 DDL 是纵向切片，缺少 Artifact、Revision、Source、状态历史、Conflict、Review、Workflow 和 Evaluation 表；跨域外键策略与未来拆分方式尚未最终统一。

改进：以 Alembic 迁移重建完整 MVP Schema，逐表标注所有者、唯一约束、索引、保留期和删除规则；生成 ER 图并进行查询计划评审。

完成条件：数据库可表达 AC-001～AC-011；迁移可在空库和已有切片库上执行；有升级、降级或前滚恢复方案。

进展：Alembic 迁移链已覆盖全部 29 张表（`0001` initial + `0002`～`0007` 增量）。`0007` 补齐研究运行时与简报表：`tool_calls`/`budget_ledger`/`node_attempts`（platform schema）与 `briefs`（publishing schema），并为 `workflow_runs` 加 `budget_profile` 列。新增迁移 parity 测试证明迁移产生的 schema 与 ORM `create_all` 元数据完全一致（29 表、列名无漂移），空库 upgrade→downgrade→upgrade 可逆。共 162 项测试通过。真实 PostgreSQL 容器迁移演练随镜像重建验证。2026-07-23：在 [DD-30 §12](./design/30-storage-schema.md) 增补「跨域引用与无物理 FK 清单」；同日落地只读孤儿巡检 `app/platform/orphan_audit.py` + `scripts/orphan_audit.py`（不删数据）；随后 CI 以 `FINSIGHT_REPOSITORY=memory` + `--fail-on-findings` 接线空库绿路径；同日 [DD-30 §13](./design/30-storage-schema.md) 完成 events/sources 列表查询计划评审，并加 `ix_events_occurred_at_id`（迁移 `20260723_0016`）；同日 [DD-30 §14](./design/30-storage-schema.md) 绘制对照 ORM 的 MVP ER 图（32 表、逻辑引用）。可选同 schema 物理 FK 仍待完成。

### IMP-011：实现 Artifact 与 DocumentRevision（P0）

现状：正文直接存于内存 Document，无法保存不可变原件、解析器版本和公告修订。

改进：内容寻址保存原始文件；Document 关联多个 Revision；规范化正文和解析块记录 parser/OCR 版本；修订不得覆盖旧内容。

完成条件：同公告内容变化生成新 Revision；旧 Evidence 仍可定位；跨来源相同字节只存一份 Artifact，但保留各来源关系。

进展：已实现本地内容寻址 Artifact、Revision 创建及旧 Evidence 绑定；生产对象存储、原始附件和孤儿清理仍待完成，因此本项尚未关闭。

### IMP-012：数据时间语义和回放约束（P0）

现状：设计定义 `published_at/ingested_at/as_of`，但 Repository 查询尚未强制时间截面。

改进：所有研究与评估查询要求 `as_of`；数据库 Repository 默认过滤未来数据；工具网关拒绝越界结果。

完成条件：自动化测试证明后续公告修订和未来行情无法进入历史工作流。

进展：已实现 `app/platform/asof.py`（`visible_as_of` 谓词检查 published_at/ingested_at/created_at/occurred_at/as_of 五类时间戳、`AsOfViolation` 错误类型、`ensure_within_as_of` 校验函数供 ToolGateway 复用）。研究类查询（`list_events`/`get_claims_for_event`/`get_fact_card_for_event`/`find_event_by_document`/`get_latest_revision`/`find_claim_by_fingerprint`）新增可选 `as_of` 参数，传入时过滤 `> as_of` 的数据，默认 None 不过滤以兼容现有调用。InMemory 与 SqlAlchemy 双端实现已对齐（SQL 用 `where col <= as_of`，内存用 `visible_as_of`）。新增 9 项测试覆盖：未来事件被过滤、回放只读当时可用数据、修订后未来 claim 不可见、as_of=None 不过滤、越界拒绝。**ToolGateway 已强制 `as_of` 越界拒绝**（见 IMP-042 / `tests/test_tool_gateway.py`、`tests/test_asof.py`）。评估抽检 2026-07-22：相关测试通过。

### IMP-013：实体主数据（P1）

现状：证券代码以字符串保存在 Event 中，缺少公司、证券、别名、有效期和公司行动。

改进：实现 Entity/Security/Alias 主数据及来源；处理更名、上市地、退市和多证券映射。

完成条件：同名公司、历史名称和代码变化样本能够稳定映射，歧义进入审核而不是猜测。

进展：已实现 `Entity`/`Security`/`EntityAlias` 主数据表（`(market_code, valid_from)` 唯一）、`EntityResolver`（代码精确匹配 1.0、主数据缺失时自动创建 Entity+Security、`code_exact`/`code_exact_auto_created` 两种解析方法）、`event_entities` 关联表（role/confidence/resolution_method）与 `merge_review_tasks` 表。`Event.entity_ids` 保留 market_code 以兼容前端 API，同时新增 `entity_links` 指向稳定 entity_id；Claim 的 `subject_entity_id` 改用解析出的真实 entity_id。新增 8 项测试，共 60 项通过。全称(0.90)/简称(0.75)/来源主体(0.95) 评分、历史代码与别名映射、歧义审核决策逻辑仍待标注集校准后落地。

## 4. 数据接入与事件中心

### IMP-020：真实来源适配器与来源治理（P0）

现状：RSS 采集、HTML/PDF 解析和同步服务已存在，但缺少 MarketMind 风格的 Fetcher 抽象、种子源、robots/限速守卫与 RSSHub 路由。

改进：确定首个官方来源，实现 SourceAdapter、批次游标、退避、隔离队列和来源健康指标；记录许可及允许保存的内容范围。

完成条件：可连续采集、断点恢复、重复运行且不跳项；来源故障能够降级并告警。

进展：已实现 `BaseFetcher` + `get_fetcher` 工厂（默认 `rss` 适配器包装 `RssFeedClient`）、`FetchGuard`（robots.txt 可选校验）、本地异步 `RateLimiter`、`IngestSyncService`（列表→详情→主流水线、连续失败自动禁用）、`seed_sources` 脚本（国家统计局 S 级、华尔街见闻/东方财富/中新网 A 级 MarketMind 种子）、Source 模型扩展 `adapter_type`/`rate_limit_per_minute`/`extra_config`/`crawl_interval_seconds`（含 `rsshub_route`）、迁移 `20260721_0009` 与 `20260722_0012`。已补齐 MarketMind 风格调度：`app.worker source`（APScheduler + 60s 热重扫）、`platform.ingest_runs`、`POST /sources/sync-all`、`GET /sources/{id}/runs` 与 Admin 来源页运维面。2026-07-23：新增 `app/ingestion/html_text.py`（跳过 script/nav、优先 article、质量评分择优）；RSS `extract_body` 在详情页噪音时回退摘要；证据 API 对历史脏正文做展示前 scrub。交易所 S 级官方 API Fetcher 与 OCR 仍待确认。

### IMP-021：五类事件专用 Schema（P0）

现状：事件识别依赖关键词，只输出类型、重要度和实体，未抽取关键业务字段。

改进：分别定义业绩预告、重大合同、并购重组、股东减持和监管处罚 Schema、Claim 模板、必填字段和冲突规则。

完成条件：每类事件有标注规范、正反例和 Schema 测试；缺失关键字段时进入审核或降级。

进展：已实现五类事件 `EventSchema`（key_fields 名单 + 必填 + Claim 模板谓词 + 冲突规则，`event-schema-v1`）、`EventClassifier` 确定性 key_fields 抽取（正则抽取期间/业绩金额区间/同比变化幅度/减持比例/监管主体，数字字符串十进制）、必填缺失降级为 `needs_review`、置信度由已抽取字段比例计算、多关键词优先级（监管处罚>并购>减持>合同>业绩，避免误判）。Event 加 `key_fields`/`confidence`/`classifier_version`/`missing_required` 字段落库。每类事件 Claim 模板复用受控谓词词表（IMP-031）。新增 14 项测试，共 74 项通过。复杂字段（合同对手方/并购标的/减持股东名）已有确定性抽取与单测；标注集 `mc_pos_001`/`ma_pos_001`/`sr_pos_001` 已纳入上述字段期望（2026-07-22 开发 loop）。2026-07-23：五类未命中时分层为 `general_market_news`（dormant）或 `out_of_scope`（archived）；Admin 事件列表默认筛「五类研究」。同日落地 **Event Router**（`app/events/router.py`）：规则只作 hint，`ModelGateway` 操作 `event_route` 输出 accept/reject/unsure；主流水线写 `event.router_decision` 审计；DeterministicProvider 跟随 hint 保证单测可复放；真实 LLM 绑定时可替换确认逻辑。更多正反例与实体主数据对齐、Router 标注集仍待扩集。

### IMP-022：事件聚类与纠错（P1）

现状：每个 Document 都创建新 Event，无法合并转载、阶段更新或公告修订。

改进：实现候选召回、特征评分、否决条件和人工合并；提供错误合并拆分操作并保留审计。

完成条件：同事件多来源只产生一个主 Event；并发消息和人工纠错不会丢失已发布报告关系。

### IMP-023：重要度规则校准（P1）

现状：重要度为事件类型固定值，未结合公司规模、金额占比、业绩变化和监管严重度。

改进：使用确定性特征生成基础分，Router 仅在受限范围内调整；保存特征、规则版本和解释。

完成条件：重要度排序在标注集上达到约定的 Top-K 召回率，并能解释每个分值。

## 5. 证据与事实核验

### IMP-030：真实文档块和稳定 Locator（P0）

现状：Evidence 固定使用首段和虚拟 HTML block，尚不能定位 PDF 页码、表格单元格或 OCR 内容。

改进：实现 PDF/HTML Parser、Block ID、字符偏移、BBox、表头和脚注关联；解析结果绑定 Revision 和解析器版本。

完成条件：随机抽样引用可回到完全一致原文；解析器升级不破坏历史 Evidence。

进展：已实现 HTML 段落 `DocumentBlockReader`、稳定 `block_id`、0-based 字符偏移、原文一致性校验（`content[char_start:char_end] == excerpt`）和 parser 版本绑定（`html-blocks-v1`）；Evidence 绑定不可变 `revision_id` 与 `extraction_version`。新增 8 项测试，共 27 项通过。PDF/表格 BBox/OCR 仍待解析组件确认。

### IMP-031：Claim 规范化和政策引擎（P0）

现状：当前只生成通用 `document_discloses_event` Claim，验证只判断来源是否为 S 级。

改进：实现受控谓词、类型化值、单位和期间标准化、事实指纹、来源独立性、支持/反驳关系及 EvidencePolicyService。

完成条件：关键数字携带单位、期间和口径；转载不被误计为独立来源；政策版本可回放。

进展：已实现 `ClaimNormalizer`（受控谓词、类型化值、十进制数字、单位/期间/口径规范化）、`ClaimFingerprint`（主体+谓词+值+单位+期间，不含措辞/置信度/Evidence ID）、`ClaimMatcher`（duplicate/new）、`ConflictDetector`（value/unit/period/subject/scope，critical/major/minor）和 `EvidencePolicyService`（DD-40 §7 决策表：S 直接/A+A 独立/B-C unverified/冲突 conflicted；来源独立性 key 按 §6 优先级；policy_version 版本化）。Claim 表加 fingerprint/qualifiers/policy_version/subject_entity_id 与 `(event_id, fingerprint)` 唯一约束；新增 claim_evidence、conflicts 表。claim 指纹去重生效（同指纹复用 Claim、追加证据关系）。新增 25 项测试，共 52 项通过。受控谓词词表目前 7 个，五类事件专用 Claim 模板待 IMP-021 补齐。

### IMP-032：冲突检测与审核（P1）

现状：没有数值、主体、期间和会计口径冲突检测。

改进：实现 ConflictDetector、严重度、自动可解冲突和人工解决流程；Claim 值变化使用替代关系。

完成条件：critical 冲突阻止完整报告；解决操作不覆盖历史事实。

## 6. Agent 与工作流

### IMP-040：模型网关和供应商隔离（P0）

现状：已有 Agent Schema，但没有模型调用实现、供应商策略或成本采集。

改进：定义统一 ModelGateway，支持模型白名单、超时、重试、用量、内容安全、可替换供应商和测试 Stub。

完成条件：业务代码不依赖供应商 SDK；每次运行可追溯模型、Prompt、Schema、Token 和费用。

进展：已实现 `ModelGateway`（统一 invoke 接口、请求 Hash 去重与重放、`DeterministicProvider` 桩、用量/费用/延迟采集、`ModelRun` 审计表）。业务代码仅依赖 `ModelProvider` Protocol，未绑定供应商 SDK。模型/Prompt/Schema 版本经 `ModelRequest` 字段持久化，可追溯。另已落地 Admin LLM Provider CRUD、预设、Agent 绑定、密钥加密轮换与 `openai_compatible`/`anthropic`/`deterministic` 协议（`app/model_gateway/`、`tests/test_llm_config.py`）。`create_app(llm_config_path=...)` 可注入临时路径；Admin CRUD 测试已强制 `memory` + `tmp_path`，不再污染 `.data/llm_config.json`（2026-07-22 开发 loop 修复评估报告退化项）。2026-07-23：`LLM_API_KEY_DECRYPT_FAILED` / 其它 `LlmConfigError` 在 `resolve_provider_for_operation` 明确回退 `deterministic-fallback`，并写审计 `llm.provider_fallback`（`tests/test_llm_config.py`）；同日修复 SqlAlchemy `save_model_run` 对 `estimated_cost_usd` 重复传参导致回退后仍 `NODE_EXECUTION_ERROR` 的问题。接真实供应商生产用量/内容安全、以及错密钥轮换修复仍待确认。

### IMP-041：LangGraph 检查点与 Blackboard（P1）

现状：工作流只有详细设计，没有运行时、NodeAttempt 和检查点实现。

改进：实现确定性 Supervisor、字段写入所有权、节点幂等、预算账本、暂停恢复和最小失效传播。

完成条件：任一节点中断后可从检查点恢复；重放不重复工具副作用；并发写触发版本冲突。

进展：已实现 LangGraph `StateGraph` 图编排（context->fact_check->company->skeptic->synthesize->guardrail->draft）、`MemorySaver` Checkpointer（按 thread_id 恢复）、确定性 Supervisor 路由、Blackboard 字段填充（`event_snapshot`/`fact_check_snapshot`/`company_analysis`/`counter_analysis`/`synthesis`/`guardrail_result`/`report_draft_ref`）、`WorkflowRun` 持久化与状态迁移（pending/running/succeeded/failed/waiting_review）。六个节点全部接入 `_execute_node` 包装：节点幂等键 `workflow_id+node+input_hash`（input_hash 由节点读取的 Blackboard 字段生成，重放命中复用结果不重复模型调用与工具副作用）、`NodeAttempt` 审计表、6 维预算账本（model_calls/tool_calls/input_tokens/output_tokens/cost_minor_units/elapsed_seconds，reserve/settle/release 追加写，软阈值 80% 与硬阈值、节点上限防止单 Agent 垄断）、硬阈值耗尽转 `waiting_review`。`BlackboardGuard` 字段写入所有权表（DD-50 §7）+ `expected_state_version` 乐观锁（`BLACKBOARD_VERSION_CONFLICT`/`BLACKBOARD_OWNERSHIP_VIOLATION`）。四个 Agent 节点已实化（替换 lambda 占位），输出经 Pydantic Schema 校验。**节点重试退避**（`MODEL_TRANSIENT` 最多 2 次、`OUTPUT_SCHEMA_INVALID` 1 次、`attempt_no` 递增；`tests/test_node_retry.py`）与**局部重跑失效传播**（`invalidation.py` + `invalidate_node_attempts` + `WorkflowService.resume`；`tests/test_workflow_resume.py`）已落地。2026-07-23：HTTP 创建工作流默认同步 `execute=true` 调用 `WorkflowService.run()`；新增 `POST /api/v1/workflows/{id}/run`（仅 pending）与 Admin「启动运行」；`tests/test_workflow_api.py` 覆盖 create→attempts/budget 与 `/run` 409。Postgres Checkpointer 生产演练与真实供应商预算校准仍待完成。

### IMP-042：工具权限与 Prompt 注入防护（P0）

现状：权限白名单已设计，尚未强制执行；外部文档可能携带恶意指令。

改进：ToolGateway 按 Agent、Workflow、`as_of`、预算和参数 Schema 鉴权；正文始终作为不可信数据隔离；敏感工具需要显式策略。

完成条件：越权工具、未来数据和文档注入测试全部被拒绝并产生安全审计。

进展：已实现 `ToolGateway`（`app/research/tools/gateway.py`）按 DD-50 §12 Agent 工具白名单鉴权（fact_checker/company_analyst/skeptic/synthesizer 各自允许集合）、`FORBIDDEN_TOOLS` 禁止清单（发布/改 Claim 状态/交易/调仓）、`as_of` 越界拒绝（复用 `ensure_within_as_of`，未来数据抛 `AsOfViolation` 不重试）、参数校验（正文/指令字段 `instructions`/`system_prompt`/`role` 被拒，防提示词注入）、`ToolCall` 审计表（脱敏 content/body/excerpt）。Synthesizer 只能读 Blackboard 与引用解析，禁止搜索/行情/写业务数据。另新增 `tests/test_security_baseline.py` 14 项端到端对抗测试：文档注入不扩大工具权限、参数指令字段拒绝、正文审计脱敏、所有 Agent 禁止工具参数化、Synthesizer 工具隔离、跨 Agent 工具隔离、外部角色不返回全文、交易/保证收益措辞拦截、未来证据不可进入工作流。共 181 项通过。

### IMP-043：Agent 回归与置信度校准（P1）

现状：只有结构 Schema，没有冻结事件集、模型升级门禁和校准指标。

改进：建立离线回归集，比较事实、引用、方向、反证、成本和延迟；模型升级先影子运行。

完成条件：模型/Prompt 变更达到预设质量门槛且无关键指标回退后才能上线。

## 7. API、安全与合规

### IMP-050：统一 API 错误和分页（P0）

现状：成功响应使用信封，但 FastAPI 默认校验错误和 HTTPException 尚未转换为统一错误格式；列表没有游标分页。

改进：增加全局异常处理、稳定错误码、Request ID、游标分页、过滤白名单和 OpenAPI 示例。

完成条件：所有 4xx/5xx 响应符合 DD-00；事件列表在并发新增数据时无重复或遗漏。

进展：已实现统一异常处理、错误信封、Request ID 传播和响应头；事件/报告/审核等列表已支持游标分页与过滤白名单。2026-07-22/23 开发 loop：来源/`ingest_runs`/LLM providers 游标分页；OpenAPI 注册 DD-00 `ErrorEnvelope` 与共享 `Error400`–`Error500` 示例；当前全部 API 写路径（含 `POST /events/{id}/workflows`、来源 sync 族、documents DELETE/PATCH、LLM、review/report/resume 等）已挂 `$ref` 路由级错误响应。

### IMP-051：身份、RBAC 与职责分离（P0）

现状：API 无认证，任何调用者都能写入和查询内部数据。

改进：接入身份提供方或内部 JWT，执行 researcher/reviewer/publisher/admin 权限；高风险报告审核与发布分离。

完成条件：未授权和越权测试通过；所有审核、发布和管理操作记录操作者。

### IMP-052：内容授权与数据保留（P0）

现状：许可与展示范围只存在设计中，尚无字段级或 API 级执行。

改进：来源配置保存许可策略；CitationResolver 根据角色和渠道返回全文片段、摘要或入口；定义删除、归档和对象锁策略。

完成条件：授权内容不会出现在普通日志、公开 API、指标或无权限页面。

进展：CitationResolver 已按角色/来源等级输出 `full`/`excerpt`/`entry`（external 不回正文；researcher 按 S/A/B/C）。2026-07-23：`GET /api/v1/evidence/{id}` 接入 `authorized_document_content`（publisher 映射为 external）；同日增加 Source.`license`（`inherit|full|excerpt|entry_only`，迁移 `20260723_0013`）并优先覆盖 tier 默认；`entry_only` 强制 entry。同日 Admin 来源页支持创建/列表编辑 `license`（PATCH）。同日 Document.`retention_hold`/`deleted_at`/`purged_at` 与 EvidenceSpan.`deleted_at`（迁移 `20260723_0014`/`0015`）：软删隐藏读路径；`purge_document` 仅在已软删且无 hold 时清空正文并物理删除证据；Admin `DELETE`/`PATCH`/`POST .../purge` 已落地；`FINSIGHT_DOCUMENT_PURGE_MIN_AGE_SECONDS`（默认 7 天）未到期则 409 `PURGE_RETENTION_WINDOW`；Admin SPA `/documents` 文档保留页可操作上述 API。同日：软删即归档；`purge_expired_documents` + source worker 定时任务 `retention:auto_purge`（`FINSIGHT_DOCUMENT_PURGE_INTERVAL_SECONDS` 默认 3600，`0` 关闭；批次 `FINSIGHT_DOCUMENT_PURGE_BATCH_SIZE`）。独立冷归档对象存储策略仍可后续扩展。

### IMP-053：Secret 和依赖安全（P1）

改进：使用 Secret 管理、依赖锁文件、漏洞扫描、容器非 root 用户、只读文件系统和最小网络权限。

完成条件：仓库无明文密钥；CI 有依赖和镜像扫描；生产容器不使用 root。

## 8. 报告、审核与前端

### IMP-060：报告与 Guardrail 实现（P1）

现状：当前事实卡片由字符串模板生成，未执行完整引用、措辞、许可和置信度规则。

改进：实现 ReportAssembler、CitationResolver、GuardrailEngine、不可变 ReportVersion 和撤回/替代流程。

完成条件：无引用、未来证据、低置信度强措辞和越权内容均不能发布。

进展：已实现 `ReportAssembler`（从 Blackboard 投影 report-draft，摘要数字来自 verified Claim、核心判断关联 Analysis ID、`fact_only` 降级事实卡片、as_of 取 WorkflowRun）、`GuardrailEngine`（6 条规则：引用完整性/as_of 可用/禁止措辞/分区明确/低置信度强信号降级/必填字段，输出 pass/fail/warn + 修复建议，`guardrail-v1` 版本化）、`CitationResolver`（Claim ID -> Evidence 定位，按角色 external/researcher 与来源等级 S/A/B/C 返回 full/excerpt/entry 展示范围，外部角色不返回正文）。工作流 draft 节点用 ReportAssembler 装配草稿，guardrail 节点用 GuardrailEngine 检查并决定 published/review_required。另实现 `BriefService` 每日 Top-10 简报（`brief_score = 0.40*importance + 0.20*urgency + 0.20*confidence + 0.10*novelty + 0.10*recency`，同 Event 最新版本、同公司最多 2 条（critical 不受配额限制）、保存候选集/分数/规则版本/顺序不调 Agent 实现稳定重放，`GET /api/v1/briefs/daily?date=` 端点）。新增 21 项测试（assembler/guardrail/citations/briefs），共 158 项通过。管理后台已接入报告详情、状态流转与版本差异对比，以及 Claim→Evidence 原文高亮；共 197 项测试通过。不可变 ReportVersion 撤回/替代发布事务与独立前端工程化仍待完成。

### IMP-061：审核可用性（P1）

改进：审核页面同时展示原文定位、Claim、冲突、Agent 差异和允许决定；支持键盘操作、批量筛选和 SLA 标记。

完成条件：审核员无需查询日志即可完成决定；所有修改通过追加意见或新版本表达。

进展：管理后台审核队列支持按 allowed_decisions 动态按钮（含 downgrade_to_fact_card）、任务详情加载报告/工作流对象、工作流预算与节点尝试及 resume API。批量筛选、键盘操作与 SLA 标记仍待完成。

### IMP-062：无障碍和风险表达（P1）

改进：不用颜色单独表达方向；显著显示数据截止时间、置信度原因、冲突和免责声明；支持中文数字/币种一致展示。

完成条件：核心页面通过基本键盘、对比度和屏幕阅读器检查。

## 9. 测试、评估与可观测

### IMP-070：测试分层和 CI（P0）

现状：已有 7 项单元/API 测试，但没有数据库、消息、并发、安全和迁移测试，也没有 CI。

改进：建立单元、契约、集成、工作流、回放、安全和端到端测试；CI 运行 Ruff、Pytest、迁移和文档检查。

完成条件：P0 路径在干净环境可重复验证；失败测试阻止合并。

进展：CI（`.github/workflows/ci.yml`）已跑 Ruff、Pytest、Alembic 空库升降级、Markdown 链接、Compose config 与前端 lint/test/build；2026-07-23 增补 memory 空库 `orphan_audit --fail-on-findings`；同日 `tests/conftest.py` autouse 强制 `FINSIGHT_REPOSITORY=memory`，隔离本机 postgresql 导出对 `create_app()` 测试的污染。分层集成/生产影子仍待扩展。

### IMP-071：质量评估集（P1）

改进：按事件类型、来源、公司规模和难度建立开发、回归和冻结集；保存标注指南和一致性检查。

完成条件：质量指标报告包含样本量、分布和置信区间，人工标签具备复核记录。

进展：已实现 `app/evaluation/assessor.py` 离线评估器与标注集 `tests/fixtures/labeled_events/samples.json`（29 条五类事件正/反/边界样本）。评估跑四项指标并输出 Wilson 95% 置信区间 + MVP 质量门槛对比。2026-07-22 复跑：分类准确率 100%、实体对齐 100%、key_fields 召回 100%、引用完整率 100%，总体 PASS（门槛 90%/98%/85%/100%）；冻结集 `mvp-frozen-v1` `overall_passed=True`（3 样本）。`scripts/shadow_run.py` + `scripts/mvp_acceptance.py` 可产出 `NOT_PRODUCTION_VALIDATED`（6 PASS + 6 未生产验证门）。标注集已覆盖合同对手方/并购标的/减持股东名等复杂 key_fields 正例。同日开发 loop：`local-quality-contract-v1` + `evaluate_local_quality_contract` 为一致性/无来源事实/谣言误标提供可重放本地证据，且恒标 `suitable_for_production_acceptance=False`（门仍为 `NOT_PRODUCTION_VALIDATED`）。人工标签复核与生产影子仍待完善。

### IMP-072：端到端可观测性（P1）

改进：接入 OpenTelemetry，串联采集、Event、Workflow、Agent、Tool、Report；增加数据源、队列、成本、质量和审核仪表盘。

完成条件：从异常报告可下钻到对应 Trace、证据和模型版本；指标不存在 Event ID 等高基数标签。

### IMP-073：市场验证方法（P1）

改进：确定交易日历、复权、停牌、公告时点、行业基准和异常收益算法；将“分析错误”和“已定价”分开。

完成条件：1/3/5/20 日结果可重放，且不会读取事件当时不可用的数据。

进展：已实现 `DeterministicMarketDataProvider` + `evaluate_market_returns`（1/3/5/20、停牌/缺失跳过、`FutureDataLeakError`、异常收益）。`build_acceptance_market_stub` / `acceptance_market_payload` 为 `mvp_acceptance` 规范 Stub（`real_market_data=False`，门 `DOC05-MARKET-OUTCOME` 恒为 `NOT_PRODUCTION_VALIDATED`）；契约元数据见 `tests/fixtures/market/acceptance_stub_meta.json`（2026-07-22 开发 loop）。真实行情适配器与生产验收仍未启用。

## 10. 运维、性能与扩展

### IMP-080：生产运行基线（P1）

改进：数据库备份/PITR、Redis 持久化策略、对象存储版本控制、健康/就绪检查、优雅关闭、运行手册和故障演练。

完成条件：完成备份恢复、Worker 中断恢复和数据库故障演练。

### IMP-081：性能容量基线（P1）

改进：测量公告峰值吞吐、端到端 P95、单事件成本、数据库热点和对象存储增长；基于数据设定 SLA。

完成条件：有可复现压测脚本、容量报告和扩容阈值。

### IMP-082：复杂组件引入门槛（P2）

仅在测量结果满足以下条件时评估升级：全文检索压力引入 OpenSearch；时序评估压力引入 ClickHouse；消息吞吐/保留超出 Redis Streams 引入 Kafka；长流程恢复 SLA 超出 LangGraph 能力引入 Temporal。

完成条件：每次引入均有 ADR、基准数据、迁移计划和退出方案。

## 11. 建议实施顺序

1. IMP-001、003、010～012：先统一需求、模型和持久化真值。
2. IMP-020、021、030、031：建立真实公告到可信 Claim 的链路。
3. IMP-050～052、070：补齐 API、安全和持续验证底线。
4. IMP-022、032、040～043：接入事件聚类和受控 Agent 工作流。
5. IMP-060～062、071～073：完成审核产品和质量闭环。
6. IMP-080～082：影子运行后按真实指标扩展。

任何 P1/P2 工作不得绕过尚未关闭的相关 P0 数据正确性或安全问题。
