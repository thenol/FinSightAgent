# 生产运维与恢复手册

## 1. 适用范围与边界

本手册覆盖单套 Docker Compose 部署的启动、停止、逻辑备份、恢复和常见故障处置。PostgreSQL、Redis AOF 与 artifact 使用独立 Docker volume；`stop.sh` 不删除 volume。

当前备份是 PostgreSQL 逻辑 dump 与 artifact 文件归档。Redis Streams 可由 PostgreSQL Outbox 重新发布，Redis AOF 只提供本机持久化，不等同于异地备份。

> **重要限制：** 当前配置不是 PostgreSQL 时间点恢复（PITR）方案，也没有证明跨机房灾备能力。真实 PITR 需要 WAL 归档、基础备份、恢复目标与恢复演练；灾备还需要异地复制、密钥可用性、DNS/流量切换及 RPO/RTO 演练。这些能力必须在目标生产环境中单独部署并验收，不能以本手册的脚本通过代替。

## 2. 启动、验收与停止

在仓库根目录的 `.env` 或进程环境中至少配置：

- `FINSIGHT_JWT_SECRET`：随机且不少于 32 个字符。
- `POSTGRES_PASSWORD`：PostgreSQL 容器密码，不提交到版本库。
- `FINSIGHT_COMPOSE_DATABASE_URL`：容器内连接地址，例如 `postgresql+psycopg://finsight:<URL 编码后的密码>@postgres:5432/finsight`。
- `FINSIGHT_BOOTSTRAP_ADMIN_USERNAME`、`FINSIGHT_BOOTSTRAP_ADMIN_PASSWORD`：首次部署的管理员凭据。

数据库 URL 中的用户名、数据库名应与 `POSTGRES_USER`、`POSTGRES_DB` 一致。密码含 URL 保留字符时必须进行百分号编码。生产环境应由密钥管理系统注入，不在命令历史、Compose 文件或备份中保存明文。

```bash
scripts/start.sh --build
FINSIGHT_ACCEPTANCE_USERNAME=admin \
FINSIGHT_ACCEPTANCE_PASSWORD='<password>' \
scripts/acceptance.sh
```

验收脚本检查 `/health/ready`、匿名采集返回 401、管理员登录、Alembic 当前版本等于唯一 head，以及五个 Compose 服务均为 running/healthy。

优雅停止并保留所有数据：

```bash
scripts/stop.sh
```

Compose 对 API 和 Worker 发送 SIGTERM，并为 PostgreSQL、Redis 和应用保留停止宽限期。禁止用 `docker compose down --volumes` 处理普通停机。

## 3. 备份

在磁盘空间充足且权限受控的目录执行：

```bash
scripts/backup.sh /secure/finsight-backups
```

每次生成一个权限受 `umask 077` 保护的时间戳目录，包含：

- `postgres.dump`：custom format PostgreSQL 逻辑 dump；
- `artifacts.tar.gz`：artifact volume 归档；
- `SHA256SUMS`：两个文件的完整性校验。

备份脚本从容器环境读取数据库用户和数据库名，不要求也不写入生产密码。备份期间仍可能有写入；要求数据库与 artifact 严格业务一致时，先停止 API 和 Worker，保留 PostgreSQL 运行，完成备份后再启动并执行验收。备份目录应加密后复制到独立故障域，并按组织策略执行保留和恢复抽检。

## 4. 恢复

恢复会替换当前数据库和 artifact 内容。先隔离流量，确认备份来源、恢复目标、变更单和回退点。脚本要求固定确认词，校验文件类型、SHA-256、archive 路径和 `pg_restore` 清单后才停止应用服务：

```bash
scripts/restore.sh /secure/finsight-backups/finsight-YYYYmmddTHHMMSSZ \
  --confirm RESTORE
scripts/acceptance.sh
```

不要把生产密码写入参数；Compose 从部署环境注入。恢复失败时脚本会尝试重新启动服务，但值班人员仍须检查日志、数据库和 artifact 完整性。恢复成功后，entrypoint 会执行待应用的 Alembic migration，因此只能用与目标应用版本兼容的备份。

## 5. Worker 中断恢复

查看状态和日志：

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --since=30m outbox-worker workflow-worker
```

- `outbox-worker` 在 SIGTERM 后结束循环；未发布记录保留在 PostgreSQL，重启后按退避策略继续发布，Inbox 去重保护消费者。
- `workflow-worker` 使用 PostgreSQL advisory lock。进程退出或连接断开时锁由 PostgreSQL释放；`pending` 任务可立即重新领取，旧 `running` 任务在 `FINSIGHT_WORKFLOW_STALE_SECONDS` 后成为恢复候选。
- 先确认 PostgreSQL、Redis healthy，再执行 `docker compose -f deploy/docker-compose.yml restart <worker>`。随后观察积压、重复副作用告警和工作流状态，不要直接批量改状态。

## 6. 应用回滚

1. 隔离写流量并执行备份。
2. 确认目标版本与当前数据库 schema 向后兼容。
3. 部署上一已验收的不可变镜像或从上一发布提交重新构建，启动后运行 `scripts/acceptance.sh`。
4. 若 migration 不兼容，不要盲目执行 Alembic downgrade；按变更设计恢复对应备份，再验收后放量。

回滚必须记录应用版本、migration revision、备份目录、操作人和验收结果。Compose 本地构建不是镜像供应链或制品留存方案，生产发布仍需外部镜像仓库、签名和不可变 tag。

## 7. 密钥轮换

- **JWT secret：** 在维护窗口注入新值并重建 API 和 Worker。现有 token 会立即失效，通知用户重新登录。需要无感轮换时，应先实现多 key 验签；当前单 key 配置不支持。
- **PostgreSQL 密码：** 先建立可回退的管理连接，在 PostgreSQL 修改角色密码，再原子更新 `POSTGRES_PASSWORD` 与 `FINSIGHT_COMPOSE_DATABASE_URL`，重建应用和数据库连接并验收。不要只改一处。
- **管理员密码：** bootstrap 变量仅用于引导，不应被视为持续轮换接口。通过受审计的账户管理流程轮换；若当前版本缺少该流程，应创建运维变更并在数据库侧使用项目认可的密码哈希工具，禁止写入明文。

轮换前后检查日志脱敏，清理临时环境和 CI 输出；不得把 secret 放入工单正文或 shell history。

## 8. Outbox 死信处理

先停留在“检查”阶段，确认 Redis 状态、错误类型、消息幂等性和下游是否已产生副作用：

```bash
docker compose -f deploy/docker-compose.yml exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
   SELECT id,event_type,aggregate_id,attempts,last_error,dead_lettered_at
   FROM platform.outbox
   WHERE dead_lettered_at IS NOT NULL
   ORDER BY dead_lettered_at
   LIMIT 100;"'
```

仅在根因修复、业务负责人批准且单条消息可安全重放后，在事务中按明确 `id` 清除死信标记：

```sql
BEGIN;
SELECT id, event_type, aggregate_id, payload
FROM platform.outbox
WHERE id = '<approved-id>' AND dead_lettered_at IS NOT NULL
FOR UPDATE;

UPDATE platform.outbox
SET attempts = 0,
    next_attempt_at = CURRENT_TIMESTAMP,
    last_error = NULL,
    dead_lettered_at = NULL
WHERE id = '<approved-id>' AND dead_lettered_at IS NOT NULL;
COMMIT;
```

不要无条件批量重放。保留变更单、原错误、消息 ID、审批、SQL 影响行数和重放结果；重启或观察 `outbox-worker`，确认 `published_at` 更新且下游没有重复副作用。

## 9. 定期演练

至少定期验证备份校验和、在隔离环境恢复、Worker 强制中断后续跑、死信单条重放和应用回滚。记录实际 RPO/RTO。PITR、异地灾备、宿主机丢失和密钥系统不可用场景只有在目标环境完成演练并留存证据后，才能声明可用。
