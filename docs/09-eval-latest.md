# 评估 Loop 最新结果

> 评估日：2026-07-23（重启后首轮之后的 20m 调度 tick）。工作目录：`FinSightAgent`。未改业务代码、未 commit、未触碰 `.env`；未修改 MarketMind/stock。调度：评估每 20m。

## 1. 完成度摘要

| 项 | 文档状态 | 核对结论 |
| --- | --- | --- |
| 测试规模 **331** | 07 §6（已由 327 更正） | **属实**：`pytest --collect-only` = **331**（含 `tests/test_workflow_api.py` 4 项） |
| IMP-041 工作流 create/run/Admin 启动 | 08 §6 / 07 §4 | **属实**：`WorkflowCreateRequest.execute` 默认 `True`；`POST /api/v1/workflows/{id}/run`；Admin `WorkflowsPage`「启动运行」；`tests/test_workflow_api.py` 覆盖 create→attempts/budget 与 `/run` 409 |
| IMP-041 重试/resume（服务层） | 08 §6 | **属实**：`invalidation.py` + `tests/test_workflow_resume.py` / `test_node_retry.py` 通过 |
| IMP-042 ToolGateway / 安全基线 | 08 §6 | **属实**：`tests/test_tool_gateway.py` + `tests/test_security_baseline.py` 通过 |
| IMP-040 ModelGateway | 08 §6 | **属实（代码）/ 本机可用性阻塞**：网关与 Admin LLM CRUD 存在；本机 PG 跑图因 API key 解密失败（见 §3） |
| IMP-043 Agent 回归门禁 | 08 §6 | **如实未完成**：无专用 Agent 回归/置信度校准门禁 |

相对上一版 09：先前「创建后停留 pending / 无 `/run`」已由开发 loop 修复；文档 07/08 已记载，本轮代码与 OpenAPI 核对一致。

## 2. 指标快照

门槛：分类 ≥90%、实体 ≥98%、key_fields ≥85%、引用 =100%。

### 2.1 Assessor（29 条标注集）

| 指标 | 结果 | 判定 |
| --- | --- | --- |
| classification_accuracy | 100%（29/29） | PASS |
| entity_alignment_accuracy | 100%（24/24） | PASS |
| key_fields_recall | 100%（24/24） | PASS |
| citation_completeness | 100%（46/46） | PASS |
| **overall_passed** | **True** | PASS |

### 2.2 冻结集 / 脚本 / 相关单测

- `FINSIGHT_REPOSITORY=memory uv run pytest tests/test_mvp_evaluation.py -q`：**通过**（6）
- 同环境 Agent/工作流：`test_agents` + `test_tool_gateway` + `test_security_baseline` + `test_workflow_resume` + `test_node_retry` + `test_workflow_api`：**全部通过**（40）
- `scripts/shadow_run.py --as-of 2026-07-22T00:00:00+00:00`：selected=5，`success_or_explicit_degradation_rate=1.0`，citation=7/7 → `.data/eval/shadow-20260723-092620.json`
- `scripts/mvp_acceptance.py --shadow-result ...`：`overall_status=NOT_PRODUCTION_VALIDATED`（**6 PASS + 6 未生产验证**）
- **无指标退化**（相对上一版 09）

## 3. 可用性结论（含 Agent/工作流）

| 路径 | 结论 | 证据 |
| --- | --- | --- |
| API `/health` / `/health/ready` | **通过** | HTTP 200；`ok` / `ready`（127.0.0.1:8000） |
| 登录（researcher/reviewer） | **通过** | `/api/v1/auth/login` 200（`secret`） |
| 创建/查看 workflow（默认 execute） | **可点通但跑图失败** | `POST /events/{id}/workflows` → **201**，`status=failed`，`error_code=NODE_EXECUTION_ERROR`（非 pending）；attempts≥1、budget≥1 |
| `POST /workflows/{id}/run` | **可点通但同上失败** | `execute=false` 创建 → pending；`/run` → 200 `failed` + `NODE_EXECUTION_ERROR` |
| 节点 attempts / 预算 | **通过（有数据）** | context succeeded；fact_check failed；budget 含 context reserve/settle |
| 根因（本机） | **LLM 密钥解密** | `ValueError: LLM_API_KEY_DECRYPT_FAILED`（`cryptography.fernet.InvalidToken`）于 `fact_check` → ModelGateway `resolve_provider` |
| 审核列表与详情 | **通过（列表级）** | reviewer：`GET /reviews` 有 `allowed_decisions`；`GET /reviews/{id}` 200 |
| 证据展开 | **通过** | `GET /evidence/{id}` 200，excerpt 可读中文 |
| Admin SPA 窄屏 / e2e | **无评估覆盖** | 无 e2e；代码侧有「启动运行」按钮 |
| 真实行情 / 交易所 S 级源 | **无评估覆盖** | 可对照 `stock` / MarketMind |

Agent 缺口优先结论：HTTP create/run/attempts/budget **已打通**（不再依赖 workflow worker）；本机研究主路径在 **ModelGateway 绑定/密钥解密** 处失败，Blackboard 仍空，无法到 succeeded/waiting_review。memory 单测不覆盖该 Fernet 失败模式。

## 4. 退化项

无指标 FAIL。  
可用性：相对上一版「创建即 pending」已改善；相对「跑通图并见 Blackboard」仍阻塞（新暴露的 LLM 解密问题，非 Assessor/冻结集回退）。**未在 docs/08 记 FAIL 标签**；已在 IMP-040 进展段记录复现与异常类型。

## 5. 无覆盖项

- Admin SPA 窄屏 / 手工 e2e 登录与审核决定写操作
- 生产 workflow worker 常驻与 stale reclaim 演练
- IMP-043 Agent 回归集 / 模型升级影子门禁
- 可选同 schema 物理 FK（IMP-010）
- 真实行情接入（可参考 `.../stock`）与交易所 S 级官方源（可参考 MarketMind）
- 生产 OTel / Docker 恢复 / `mvp_acceptance` 生产门

## 6. P0 风险

1. **本机 LLM 密钥无法解密**：已绑定非 deterministic 供应商时，`fact_check` 即 `NODE_EXECUTION_ERROR`；研究主路径看似已启动实则必败。
2. **质量门仍依赖 Stub**：`mvp_acceptance` 恒 `NOT_PRODUCTION_VALIDATED`，不可作生产放行。
3. **逻辑外键无物理约束**：依赖 orphan_audit（CI memory 空库 + 生产需定期跑）。

## 7. 建议开发 Loop 下一项（仅 1 个）

修复本机 Agent 跑图阻塞：排查并修复 LLM Provider API key 的 Fernet 解密（`LLM_API_KEY_DECRYPT_FAILED` / 密钥轮换与 `FINSIGHT_*` 主密钥不一致），或在解密失败时明确回退 `DeterministicProvider` 并打审计；使 `POST /events/{id}/workflows`（默认 execute）能到 `succeeded`/`waiting_review` 且 Blackboard/attempts 完整可读。不要重复做已完成的 create/run/Admin 启动。
