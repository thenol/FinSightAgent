# DD-21：事件分流 v2——评分门控与开放分类

> 状态：初稿（2026-08-15）。修订 DD-20 §4 的分类与路由语义，替代"类型白名单当门"的旧设计。

## 1. 背景与问题

DD-20 的分类设计把"事件类型"同时用作**门**（不在六类词表 → out_of_scope → 归档）
和**路由提示**（选 Schema、选 Agent、定重要度基线）。门职责导致规则词表外的重大事件
（地缘冲突、自然灾害、产业政策、供应链中断）被静默丢弃——例如"美国对伊朗开战"
不含任何 MVP 关键词，落为 `out_of_scope`（importance 0.15）直接归档，不产生任何报告。

结论：**分类本身不是目的**。系统真正需要的是三件事：

- 门控（gating）：这条信息有没有经济意义、值不值得消耗分析预算；
- 路由（routing）：该派哪些 Specialist Agent、抽哪些结构化字段；
- 排序（prioritization）：审核队列与每日简报中谁先被人看到。

三者都不依赖"先确定事件类型"。因此类型从**输入条件**降级为**输出标签与路由提示**。

## 2. 设计决策

### 2.1 门控标准变更

| 维度 | v1（旧） | v2（新） |
| --- | --- | --- |
| 归档条件 | 类型不在白名单 | Router 裁决 `relevance=irrelevant`（无经济相关性） |
| 休眠条件 | 类型为 general_market_news | Router 裁决 `unsure` 或相关但重要度不足 |
| 进入研究 | 类型在 MVP 白名单 | `relevance=relevant`（类型已知走快路径，未知走候选类型） |

`out_of_scope` 语义重定义为"无经济相关性"，是 Router 相关性裁决的结果，而非
"类型不认识"的结果。

### 2.2 Router 输出契约 v2

`event_route` operation 输出 Schema 从 v1 升级为 v2：

```json
{
  "relevance": "relevant | irrelevant | unsure",
  "event_type": "snake_case 标签（MVP 类型 / 候选类型 / general_market_news / out_of_scope）",
  "importance": 0.0,
  "confidence": 0.0,
  "required_agents": ["fact_checker"],
  "reason": "不超过 2000 字的裁决理由"
}
```

- `relevance`：唯一门控字段。`relevant` 进入事件管道；`irrelevant` 归档；`unsure` 休眠待审。
- `event_type`：标签。允许输出白名单外的 snake_case 标签（候选类型）；不得发明非 snake_case 值。
- `importance`：Router 对事件重要度的建议值，仅在候选类型（无规则基线）时作为
  `ImportanceCalculator` 的类型基线分量；MVP 类型仍以规则基线为准（可解释性优先）。
- Schema 校验失败、供应商异常、超时：回退确定性载荷（`deterministic_route_payload`），
  记录 `used_fallback=true`。

### 2.3 确定性回退语义（无 LLM 时）

| 规则 hint | relevance | event_type | 结果 |
| --- | --- | --- | --- |
| MVP 类型 | relevant | = hint | 正常进入研究 |
| general_market_news | unsure | general_market_news | dormant（保持 v1 行为） |
| out_of_scope | irrelevant | out_of_scope | archived（保持 v1 行为） |

确定性路径不放行未知类型——"宁可漏判、不可错放"只在无模型时成立；接入真实 LLM 后
由 v2 契约承担泛化职责。

### 2.4 候选类型（candidate type）生命周期

LLM 给出的非 MVP、非保留字（general_market_news/out_of_scope/unsupported）标签即候选类型：

1. **候选**：事件落库，`event_type=候选标签`，`classifier_version=event-router-v2-candidate`，
   `missing_required` 含 `candidate_type_confirmation`（强制 `needs_review` 状态，等待人工确认类型）；
   可触发工作流与影响分析，但**不进每日简报**（简报候选集仍限一等类型）。
2. **积累**：同类标签事件数达到阈值（默认 5，`FINSIGHT_CANDIDATE_TYPE_PROMOTION_THRESHOLD`）
   后，管理后台提示升格（本轮仅落库与统计，升格 UI 后续迭代）。
3. **升格**：补充 EventSchema、key_fields 抽取、标注集、重要性基线，成为一等类型，
   冷启动（LLM）切换为规模化（确定性规则）。

候选事件的 Claim 生成走无 Schema 回退路径（`document_discloses_event` 谓词），
不因缺少 claim_templates 崩溃。

### 2.5 新增一等类型：geopolitical_event

首个验证端到端链路的新类型：

- 关键词：开战、宣战、军事打击、空袭、导弹袭击、制裁、军演、政变、恐怖袭击、入侵；
- importance 基线 0.95（与 macro_policy 同级）；
- key_fields：`parties`（涉及方，必填）、`region`（地区，必填）、`action`（行动类型）、
  `commodities`（关联商品，如 原油/黄金）；
- Claim 谓词：新增 `geopolitical_action`（object_type=string），`PREDICATE_VERSION`
  升至 `controlled-v3`；
- 事件类型优先级：紧随 `macro_policy` 之后。

### 2.6 重要度合成

`ImportanceCalculator.calculate()` 新增 `type_baseline_override` 参数：候选类型事件由
Router 建议值替换 `_type_baseline`，其余分量（来源等级、时效性等）不变，保证可解释性。

## 3. 时序（新文档进入）

```
ingest → 去重/切分 → DocumentIntelligence → EventClassifier（规则 hint）
  → EventRouter.route()（LLM v2 裁决；失败回退确定性）
  → merge_classification()
     ├─ relevant + MVP 类型      → triaged/needs_review（v1 行为）
     ├─ relevant + 候选类型      → needs_review（candidate_type_confirmation）
     ├─ unsure                  → dormant（general_market_news）
     └─ irrelevant              → archived（out_of_scope）
  → EventMatcher → Claim 生成（候选走 legacy 回退）→ FactCard
  → 高重要度 → 自动工作流 → 发布后自动影响分析
```

## 4. 审计

`event.router_decision` 审计详情扩展：`relevance`、`event_type`、`importance`、
`is_candidate_type`、`rule_hint_type`、`confidence`、`used_fallback`、`model_run_id`、
`reason`、`required_agents`。

## 5. 失败与降级

- LLM 输出 Schema 校验失败 → 确定性回退（v1 行为）；
- LLM 判 relevant 但类型非法（非 snake_case / 保留字冲突）→ relevance 降级 unsure；
- 候选类型重要度缺失 → 使用 general_market_news 基线 0.35 并进 needs_review；
- 全部路径写审计，可用 `request_id` / `model_run_id` 追溯。

## 6. 测试点

- 确定性回退三种 hint 的行为与 v1 一致（dormant/archived/accept）；
- LLM stub 判 relevant + 候选类型：事件落库 needs_review、importance 取建议值、
  审计含 is_candidate_type=true；
- LLM 判 relevant 但返回非法类型标签：降级 unsure；
- 候选类型事件 Claim 生成不崩溃（legacy 谓词）；
- geopolitical_event：关键词分类、parties/region 抽取、必填缺失降级 needs_review、
  Claim 模板生成 geopolitical_action 谓词；
- 高重要度候选事件触发自动工作流（importance ≥ 阈值）；
- 标注集回归：既有 29 条样本不退化。

## 7. 范围外（后续迭代）

- 候选类型升格的管理后台 UI 与操作流程；
- 审核风险分层路由（高置信低风险自动放行）；
- 重要度动态化（来源密度/传播速度/市场反馈参与计算）；
- 事件关系图谱与跨事件影响聚合。
