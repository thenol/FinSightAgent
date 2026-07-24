# DD-00 共享约定

## 1. 适用范围

本约定适用于 API、异步任务、领域事件、数据库记录和 Agent 工具。业务模块可以增加约束，但不得改变这些基础语义。

## 2. 标识符

所有主对象使用 UUIDv7，以兼顾全局唯一性和时间有序索引。API 使用不透明字符串，不允许客户端解析 ID。

| 对象 | 示例前缀 | 幂等来源 |
| --- | --- | --- |
| Document | `doc_` | `source_id + external_id`，缺失时使用规范化 URL |
| Artifact | `art_` | 原始字节 SHA-256 |
| Event | `evt_` | 由事件聚类服务生成 |
| WorkflowRun | `wfr_` | `event_id + trigger_id + workflow_version` |
| Request | `req_` | 客户端提供或网关生成 |

前缀只用于日志和可读性，不表达对象类型之外的业务含义。

## 3. 时间与金额

- API 时间统一使用带时区的 ISO 8601，例如 `2026-07-12T09:30:00+08:00`。
- 数据库存储 `timestamptz`，服务内部统一转 UTC；展示层转换用户时区。
- 同时保存来源时间 `published_at` 和系统时间 `ingested_at`。
- 金额使用十进制定点数和独立币种字段，禁止使用浮点数。
- 比例在接口中使用小数，例如 `0.125` 表示 12.5%，字段名不得混用百分数。

## 4. API 信封

成功响应：

```json
{
  "data": {},
  "meta": {
    "request_id": "req_019...",
    "schema_version": "1.0"
  }
}
```

错误响应：

```json
{
  "error": {
    "code": "DOCUMENT_CONFLICT",
    "message": "Document already exists with different content",
    "retryable": false,
    "details": {}
  },
  "meta": {"request_id": "req_019..."}
}
```

错误码稳定且面向调用者；异常堆栈仅进入受控日志。

## 5. 幂等与并发

- 所有 POST 写接口接受 `Idempotency-Key`，有效期不少于 24 小时。
- 相同 Key 和相同请求体返回首次结果；相同 Key 和不同请求体返回 `409 IDEMPOTENCY_CONFLICT`。
- 聚合根更新使用整数 `version` 做乐观锁；版本冲突返回 `409 VERSION_CONFLICT`。
- Worker 使用业务幂等键保证至少一次投递下的效果等同于一次。

## 6. 事务与消息

业务写入和 outbox 记录在同一 PostgreSQL 事务中提交。独立发布器把 outbox 投递到 Redis Streams；消费方使用 inbox 表去重。不得在数据库事务提交前直接发送消息。

领域事件最小信封：

```json
{
  "event_id": "msg_019...",
  "event_type": "document.ingested.v1",
  "aggregate_id": "doc_019...",
  "occurred_at": "2026-07-12T01:30:00Z",
  "trace_id": "trc_019...",
  "payload": {}
}
```

## 7. 分页与查询

列表接口使用基于稳定排序键的游标分页，默认 20 条、最大 100 条。游标包含排序字段和 ID，不暴露数据库偏移量。查询条件必须显式定义，禁止把任意 SQL 风格过滤器暴露给客户端。

## 8. 日志与追踪

每条结构化日志至少包含 `timestamp`、`level`、`service`、`request_id` 或 `job_id`、`trace_id` 和错误码。正文、密钥、授权内容和模型完整提示词默认不写日志。

## 9. 版本兼容

- 外部 API 主版本位于路径 `/api/v1/`。
- 领域事件版本位于事件名，例如 `document.ingested.v1`。
- Agent、工具和 JSON Schema 使用独立版本，不与应用版本绑定。
- 新增可选字段视为兼容；删除、重命名或改变语义必须发布新版本。

## 10. 代码边界

建议每个功能域内部采用：

```text
domain/          # 实体、值对象、领域规则
application/     # 用例与事务编排
infrastructure/  # 数据库、队列和外部适配器
api/             # HTTP 或消息入口
```

领域层不得依赖 FastAPI、ORM、队列 SDK 或模型供应商。

