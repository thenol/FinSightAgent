# 评估 Loop 最新结果

> 评估日：2026-08-16。工作目录：`FinSightAgent`。本轮交付 EventTypeRegistry 升格治理后复跑确定性评估；未改 `.env`、未接入真实行情。

## 1. 完成度摘要

| 项 | 核对结论 |
| --- | --- |
| 测试规模 **506 passed / 1 skipped** | `uv run pytest` 全绿（含 `tests/test_event_type_registry.py` 10 项与 SQLAlchemy 注册表持久化 1 项） |
| EventTypeRegistry | 候选类型计数、阈值 `promotion_ready`、accept 去掉强制审核、reject 后续落 `cold`、API + Admin 词表页均有自动化覆盖 |
| Assessor 29 条标注集 | 分类/实体/key_fields/引用仍为 PASS（确定性路径，见 §2.1） |
| 确定性 shadow / MVP 验收脚本 | 可重放；`overall_status=NOT_PRODUCTION_VALIDATED` |
| 真实 RSS + LLM 闭环 | **未执行**：本机 `.env` 无模型供应商密钥；开放分类质量仍未知 |

相对 2026-07-23 快照：测试由 331 → 506；DD-21/22 与词表治理已落地。质量门禁仍停在 Stub，不可作生产放行。

## 2. 指标快照

门槛：分类 ≥90%、实体 ≥98%、key_fields ≥85%、引用 =100%。

### 2.1 Assessor（29 条标注集）

`scripts/mvp_acceptance.py` 读取 Assessor 默认夹具，本轮未改标注集：

| 指标 | 判定 |
| --- | --- |
| classification_accuracy | PASS |
| entity_alignment_accuracy | PASS |
| key_fields_recall | PASS |
| citation_completeness | PASS |
| **overall_passed** | PASS |

### 2.2 确定性影子运行（2026-08-16）

命令：

```bash
uv run python scripts/shadow_run.py --as-of 2026-08-16T00:00:00+00:00 --output .data/eval/shadow-20260816.json
uv run python scripts/mvp_acceptance.py --shadow-result .data/eval/shadow-20260816.json --output .data/eval/mvp-acceptance-20260816.json
```

| 项 | 结果 |
| --- | --- |
| selected_sample_count | 6 |
| 类型分布 | earnings_guidance 1 / major_contract 2 / merger_acquisition 1 / regulatory_penalty 1 / shareholder_reduction 1 |
| success_or_explicit_degradation_rate | 1.0（6/6） |
| citation_completeness | 1.0（8/8） |
| duplicate_report_rate | 0.0 |
| model_calls | 0（DeterministicProvider） |

### 2.3 MVP 验收门（`mvp-acceptance-v1`）

`overall_status=NOT_PRODUCTION_VALIDATED`（**6 PASS + 6 未生产验证**）：

| 门 | 状态 |
| --- | --- |
| DOC05-Q-CLASSIFICATION | PASS |
| DOC05-Q-ENTITY-ALIGNMENT | PASS |
| DOC05-Q-ASSESSOR-CITATION | PASS |
| DOC05-Q-WORKFLOW-COMPLETION | PASS |
| DOC05-Q-CITATION-COMPLETENESS | PASS |
| DOC05-Q-DUPLICATE-REPORT | PASS |
| DOC05-Q-CITATION-CONSISTENCY | NOT_PRODUCTION_VALIDATED |
| DOC05-Q-UNSOURCED-FACTS | NOT_PRODUCTION_VALIDATED |
| DOC05-Q-RUMOR-MISLABEL | NOT_PRODUCTION_VALIDATED |
| DOC05-NFR-LATENCY | NOT_PRODUCTION_VALIDATED |
| DOC05-NFR-COST | NOT_PRODUCTION_VALIDATED |
| DOC05-MARKET-OUTCOME | NOT_PRODUCTION_VALIDATED |

## 3. 可用性结论

| 路径 | 结论 |
| --- | --- |
| 单元/API/迁移测试 | 通过（506 passed / 1 skipped）；全仓 `uv run ruff check .` 已通过 |
| 管理端构建 | `cd web && npm test -- --run && npm run build` 通过 |
| 确定性采集→事件→卡片 | shadow 6/6 成功或显式降级 |
| 开放分类（真实 LLM） | 未跑。无供应商密钥时 Router 走确定性回退，不放行未知类型 |
| 真实 RSS 采集 | 未跑。Docker daemon 可用，但缺少 LLM 则无法验证 DD-21 开放分类与词表积累 |
| 真实行情 / 交易所 S 级源 | 仍为 Stub |

## 4. 无覆盖项（本轮明确不做）

- 真实 LLM 对「美国对伊朗开战」类样本的开放分类抽检
- 生产 Compose 全栈（含新加的 `reevaluate-worker`）恢复演练
- 真实行情、`market_signal`、OCR/交易所官方 API
- Admin SPA 窄屏手工 e2e

## 5. P0 风险

1. **质量门仍依赖 Stub**：`mvp_acceptance` 恒带 6 项 `NOT_PRODUCTION_VALIDATED`，不能当生产放行。
2. **开放分类未用真模型验收**：词表治理代码已通，但 `candidate` 标签的误判率、升格噪声未知。
3. **本机无模型密钥**：配置真实 LLM 后才能回答「开放分类是否可运营」。

## 6. 建议下一步（仅 1 个）

配置 Model Gateway 真实供应商密钥后，用种子 RSS + Router v2 跑一小批真实公告：统计候选类型出现频率、人工抽检分类对错，并在词表页练习 accept/reject。不要在密钥就绪前扩平台功能。
