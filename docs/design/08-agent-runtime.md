# DD-80 Agent Runtime 详细设计

## 1. 目标与边界

Agent Runtime 在现有固定事件研究工作流（DD-50）之上，提供**动态研究计划**、**可注册 Specialist Agent** 和**可复用执行引擎**，使系统能根据研究问题自动规划检索、分析、反证与综合步骤，而不是只依赖硬编码的 `fact_check → company → skeptic → synthesize` 序列。

覆盖：FR-005、FR-006、AC-006～AC-010、NFR-002～NFR-004、AGENT-001～AGENT-004。

WP-08 MVP 边界：
- 不替换固定工作流；固定工作流继续负责事件→FactCard 的确定性链路。
- 动态运行时先支持"研究问题→计划→执行→结果"的端到端骨架。
- 首批 Specialist Agent 以现有四个 Agent 的注册形态为主，新增 Planner、Retriever、ImpactAnalyst（复用 `ImpactAnalysisService`）作为动态计划示例。
- 不涉及 Temporal、多租户、长期记忆和完整知识图谱集成。

## 2. 设计基线

- **固定工作流与动态运行时两层分离**：固定流程负责采集、解析、Evidence Policy、发布和合规；动态流程负责问题理解、研究规划、检索选择、假设、反证和综合。
- **ResearchPlan 是第一类领域对象**：包含目标、约束、任务 DAG、工具策略、证据要求、预算和完成标准，可暂停、修改、局部重跑和复现。
- **Agent Registry 声明式注册**：每个 Agent 声明能力、输入/输出 Schema、允许工具、预算质量门和版本，不允许运行时自行扩大权限。
- **复用现有运行时基础设施**：动态计划挂靠在 `WorkflowRun` 下，复用 `Blackboard`、`BudgetManager`、`NodeAttempt`、`ReviewTask`、`Checkpointer` 和 `ToolGateway`。
- **`as_of` 约束贯穿计划全生命周期**：计划、任务、检索和工具调用均受 `as_of` 限制，防止未来数据泄漏。
- **模型只提供受 Schema 约束的建议**：Planner 可调用 LLM 生成计划建议，但 Supervisor（确定性代码）拥有最终执行决定权。

## 3. 组件划分

| 组件 | 职责 | 新增/复用 |
| --- | --- | --- |
| `AgentRegistry` | 声明式注册 Specialist Agent；按能力/输入输出/版本查找 | 新增 `app/agents/registry.py` |
| `ResearchPlanner` | 基于问题生成 `ResearchPlan`（规则为主，LLM 可选增强） | 新增 `app/workflows/planner.py` |
| `DynamicWorkflowService` | 执行动态 DAG；复用预算、检查点、节点重试和审核恢复 | 扩展 `app/workflows/service.py` |
| `ResearchState` | 扩展 TypedDict，容纳动态任务的 `task_outputs` | 扩展 `app/workflows/service.py` |
| `BlackboardGuard` | 增加动态字段所有权表 | 扩展 `app/workflows/blackboard.py` |
| `AgentExecutor` | 按 Agent 注册信息组装请求、调用 `ModelGateway`、校验输出 Schema | 扩展 `app/workflows/agents.py` |
| `ToolGateway` | 按注册白名单鉴权，记录调用审计 | 复用 `app/research/tools/gateway.py` |
| `BudgetManager` | 按 `WorkflowRun` 维度预留/结算 | 复用 `app/workflows/budget.py` |
| `Research API` | 提交研究问题、查看计划、启动执行 | 扩展 `app/api/routes.py` |

## 4. 领域模型

### 4.1 ResearchPlan

```python
@dataclass(frozen=True)
class ResearchPlan:
    id: str
    workflow_id: str
    question: str
    objective: str
    as_of: datetime
    status: str  # pending | planning | ready | running | waiting_review | succeeded | failed | cancelled
    tasks: list["ResearchTask"]
    budget_profile: str = "mvp_standard"
    completion_criteria: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

### 4.2 ResearchTask

```python
@dataclass(frozen=True)
class ResearchTask:
    id: str
    plan_id: str
    name: str
    agent_key: str
    description: str
    dependencies: list[str]  # task name list
    required: bool = True
    status: str = "pending"  # pending | ready | running | succeeded | failed | skipped | waiting_review
    input_fields: list[str] = field(default_factory=list)
    output_field: Optional[str] = None
    tool_strategy: dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[str] = None  # e.g. "company-analysis-output/1.0.0"
    input_hash: Optional[str] = None
    output_snapshot: Optional[dict[str, Any]] = None
    review_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
```

### 4.3 AgentCapability / AgentRegistration

```python
@dataclass(frozen=True)
class AgentRegistration:
    agent_key: str
    version: str
    display_name: str
    capabilities: list[str]  # e.g. ["retrieve", "fact_verify", "company_analyze", "skeptic_review", "synthesize"]
    input_schema_refs: list[str]
    output_schema_ref: str
    allowed_tools: list[str]
    budget_profile: str = "mvp_standard"
    quality_gates: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
```

## 5. Agent Registry

`AgentRegistry` 是内存+持久化混合注册表：

- 启动时从 `agent_registrations` 表加载；MVP 也允许代码中预注册内置 Agent（现有四个 + Planner + Retriever + ImpactAnalyst）。
- 提供 `register(registration)`、`get(agent_key)`、`find(capabilities, input_schema, output_schema)`。
- `find` 按能力匹配度、版本兼容性和预算配置选择最合适的 Agent；多个候选时按注册优先级/版本排序。
- 注册信息中的 `allowed_tools` 直接写入 `ToolGateway.AGENT_TOOL_WHITELIST`，避免工具权限分散在两个地方。

内置 Agent 注册示例：

| agent_key | capabilities | output_schema_ref | allowed_tools |
| --- | --- | --- | --- |
| `fact_checker` | `["fact_verify"]` | `fact-check-snapshot/1.0.0` | `search_official_filings`, `get_document_blocks`, `submit_fact_check_result` |
| `company_analyst` | `["company_analyze"]` | `company-analysis-output/1.0.0` | `get_financial_statements`, `calculate_financial_metrics`, `find_similar_events` |
| `skeptic` | `["skeptic_review"]` | `skeptic-output/1.0.0` | `get_evidence`, `get_financial_statements`, `find_similar_events` |
| `synthesizer` | `["synthesize"]` | `synthesis-output/1.0.0` | `read_blackboard`, `resolve_citation` |
| `planner` | `["plan"]` | `research-plan/1.0.0` | `read_blackboard`（只读） |
| `retriever` | `["retrieve"]` | `retrieval-trace/1.0.0` | `planned_retrieval` |
| `impact_analyst` | `["impact_analyze"]` | `impact-analysis-output/1.0.0` | `get_financial_statements`, `find_similar_events` |
| `preliminary_assessor` | `["preliminary_assess"]` | `preliminary-assessment-output/1.0.0` | 无（仅使用已核验上下文） |

## 6. ResearchPlanner

`ResearchPlanner` 接收研究问题，输出 `ResearchPlan`：

1. **意图解析（规则为主）**：
   - 识别问题类型：`company_event`（公司业绩/并购）、`macro_policy`（利率/政策）、`market_risk`（流动性/地缘）、`general`。
   - 提取实体、时间窗、`as_of`（默认现在）。
2. **任务模板选择**：
   - 每个问题类型对应一个默认任务 DAG 模板。例如 `company_event` → `[retrieve, fact_verify, company_analyze, skeptic_review, synthesize]`。
   - `macro_policy` → `[retrieve, fact_verify, preliminary_assess, impact_analyze, synthesize]`。
   - `preliminary_assess` 生成不可变的事件级研究假设；下游 Agent 可引用其观点，但必须继续绑定原始 Evidence。
3. **LLM 可选增强**：
   - 调用 `planner` Agent，输入问题+模板，输出对任务的增删改建议（必须在预定义 Schema 内）。
   - Supervisor 校验建议：不允许新增未注册 Agent、不允许放宽预算、不允许设置未来 `as_of`。
4. **依赖解析**：生成任务名称→依赖集合；检测环并拒绝。

Planner 输出示例：

```json
{
  "objective": "分析某公司业绩预告对净利润和股价的影响",
  "as_of": "2026-08-14T00:00:00Z",
  "tasks": [
    {"name": "retrieve", "agent_key": "retriever", "dependencies": [], "required": true},
    {"name": "fact_verify", "agent_key": "fact_checker", "dependencies": ["retrieve"], "required": true},
    {"name": "company_analyze", "agent_key": "company_analyst", "dependencies": ["fact_verify"], "required": true},
    {"name": "skeptic_review", "agent_key": "skeptic", "dependencies": ["company_analyze"], "required": true},
    {"name": "synthesize", "agent_key": "synthesizer", "dependencies": ["skeptic_review"], "required": true}
  ],
  "completion_criteria": {"required_tasks": ["fact_verify", "company_analyze", "synthesize"]},
  "budget_profile": "mvp_standard"
}
```

## 7. 动态执行引擎

### 7.1 与固定工作流的关系

- 固定工作流 `WorkflowService` 的图是编译期确定的 LangGraph `StateGraph`。
- 动态运行时通过同一个 `WorkflowRun` 记录承载，但把计划任务展开为运行时节点；
  `DynamicWorkflowService` 在 `_invoke` 前根据 `ResearchPlan.tasks` 构建临时图或用手工拓扑调度。
- WP-08 MVP 采用**手工拓扑调度**：每次从 `ready` 任务集合选一个执行，写 Blackboard `task_outputs.{task_name}`，更新任务状态，直到无 `ready` 任务或失败/审核。

### 7.2 执行循环

```text
while ready_tasks:
    task = pop_ready(ready_tasks)
    agent = registry.get(task.agent_key)
    budget.reserve(task.name)
    attempt = create_node_attempt(...)
    try:
        output = execute_agent(agent, inputs_from_blackboard)
        schema_validate(output, agent.output_schema_ref)
        blackboard["task_outputs"][task.output_field or task.name] = output
        budget.settle(task.name)
        task.status = succeeded
    except BudgetExceeded:
        enter_waiting_review or degrade
    except RetryableError:
        retry_with_backoff
    except SchemaValidationError:
        mark_failed / review
```

### 7.3 Blackboard 扩展

动态字段所有权（`app/workflows/blackboard.py` 增加）：

```python
FIELD_OWNERS.update({
    "research_plan": "planner",
    "task_outputs": "dynamic_engine",
    "plan_status": "dynamic_engine",
})
```

`task_outputs` 内部按任务名分子字段，子字段所有权归对应 Agent；`dynamic_engine` 负责元数据更新。

### 7.4 复用基础设施

- **预算**：每个动态任务作为一个 "node_name" 走 `BudgetManager.reserve/settle/release`。
- **节点尝试**：`NodeAttempt` 的 `node_name` 用 `dynamic:{task.name}` 前缀，便于与固定节点区分。
- **审核恢复**：动态任务失败或需要人工决定时，创建 `ReviewTask(object_type="workflow", object_id=workflow_id)`，复用现有审核 API。
- **检查点**：WP-08 先利用 `WorkflowRun.blackboard` + `NodeAttempt` 实现粗粒度检查点；细粒度 LangGraph checkpoint 后续迭代。

## 8. API 设计

### 8.1 提交研究问题

```http
POST /api/v1/research
```

请求：

```json
{
  "question": "分析美联储 2026 年 8 月加息 25BP 对 A 股银行与地产板块的影响",
  "as_of": "2026-08-14T00:00:00Z",
  "event_id": "evt_xxx",
  "budget_profile": "mvp_standard",
  "execute": false
}
```

响应：

```json
{
  "data": {
    "plan_id": "rpl_xxx",
    "workflow_id": "wfr_xxx",
    "status": "ready",
    "tasks": [...]
  },
  "meta": {"request_id": "..."}
}
```

### 8.2 启动/继续执行

```http
POST /api/v1/research/{plan_id}/execute
```

响应返回 `WorkflowResponse`（或专用 `ResearchPlanResponse`）。

### 8.3 查询计划

```http
GET /api/v1/research/{plan_id}
GET /api/v1/research/{plan_id}/tasks
```

## 9. 持久化

新增 Alembic 迁移：

- `research_plans` 表：id, workflow_id, question, objective, as_of, status, budget_profile, completion_criteria(JSON), metadata(JSON), created_at, updated_at。
- `research_tasks` 表：id, plan_id, name, agent_key, description, dependencies(JSON), required, status, input_fields(JSON), output_field, tool_strategy(JSON), output_schema, input_hash, output_snapshot(JSON), review_reason, started_at, ended_at, created_at。
- `agent_registrations` 表：agent_key, version, display_name, capabilities(JSON), input_schema_refs(JSON), output_schema_ref, allowed_tools(JSON), budget_profile, quality_gates(JSON), config(JSON), created_at, updated_at。

Repository 协议扩展对应 `save/get/list/update` 方法。

## 10. 与现有系统的协作

- **固定工作流触发**：事件进入固定工作流后，若配置启用 `dynamic_research_on_high_importance`，可在 `synthesize` 成功后自动为同一 `WorkflowRun` 生成 `ResearchPlan`，扩展研究深度（WP-08 先预留接口，不默认启用）。
- **影响分析复用**：`impact_analyst` Agent 直接调用 `ImpactAnalysisService`，避免重复实现宏观传导逻辑。
- **检索复用**：`retriever` Agent 调用 `RetrievalService.planned()`，输出 `RetrievalTrace` 写入 Blackboard。
- **Model Gateway 复用**：动态 Agent 的 `operation` 使用注册表中的 `agent_key`，`LlmAgentBinding` 已支持新增 key。

## 11. 安全与治理

- Planner 输出必须经 Pydantic Schema 校验，禁止模型直接生成工具调用或预算变更。
- 动态任务只能调用注册时声明的工具；`ToolGateway` 按 `agent_key` 白名单鉴权。
- `as_of` 在计划生成时确定，任务执行时若发现输入数据晚于 `as_of` 则失败并记录安全事件。
- 新增 Agent 注册需要写入 `agent_registrations` 表（admin 权限），不允许运行时任意注册。

## 12. 可观测性

扩展指标：

- `research_plans_total{status, question_type}`
- `research_task_duration_seconds{agent_key, result}`
- `research_plan_generation_duration_seconds{result}`
- `agent_registry_lookup_total{agent_key, result}`

Trace 串联 ResearchPlan → ResearchTask → NodeAttempt → AgentRun → ToolCall。

## 13. 测试设计

- Agent Registry：注册、查找、版本选择、未授权 Agent 工具拒绝。
- ResearchPlanner：规则模板选择、依赖无环、LLM 增强建议受 Schema 约束、未来 `as_of` 拒绝。
- 动态执行：正常 DAG 执行、任务失败传播、预算硬限进入 waiting_review、节点重试、局部重跑。
- API：`POST /api/v1/research` 生成计划、`POST /api/v1/research/{id}/execute` 启动、查询任务状态、权限校验。
- 兼容性：固定工作流不受影响；新增动态字段不破坏现有 Blackboard 所有权。

## 14. 待确认事项

- 是否允许同一 `WorkflowRun` 同时存在固定图状态和动态计划状态；建议默认互斥，通过 `workflow_type` 区分。
- Planner LLM 增强的默认 Provider 与 Fallback 策略。
- 动态任务失败时是否自动创建新的 `WorkflowRun` 重跑，还是在同一 Run 内重试；MVP 采用同一 Run 内重试。
- 长期记忆与知识图谱集成在 WP-08 中仅预留字段，不实现。

## 15. 与平台 Backlog 的对应

- AGENT-001：固定与动态分层 ✅
- AGENT-002：ResearchPlan + 动态 DAG ✅（MVP 手工拓扑调度）
- AGENT-003：Specialist Agent Registry ✅（MVP 内置注册）
- AGENT-004：Tool Gateway 扩展为能力网关（部分 ✅，完整治理后续迭代）
- AGENT-005：研究记忆与分层 Blackboard（预留字段，后续实现）
- AGENT-006：LangGraph/Temporal/事件总线协作（LangGraph 继续承载，Temporal 后续评估）
