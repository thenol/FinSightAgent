# 多市场行情与市场展望设计

## 目标

覆盖 A 股、港股、美股指数、行业板块和代表 ETF，提供 5 分钟行情、交易日历、市场状态和 1/3/5/20 个交易日的概率展望。系统输出研究结论，不输出自动交易指令。

## 数据边界

- 东方财富为研究版主源，AKShare 为可选回退源；供应商必须通过 `MarketDataProvider` 暴露标准化结果。
- 原始响应不可变归档，标准化行情包含 `source`、`available_at`、`ingested_at`、`as_of` 和版本。
- `as_of` 之后可用的行情、特征和事件不得进入历史预测或回测。
- 无数据、过期数据、供应商冲突和字段缺失必须返回结构化降级状态。

## 存储规划

- PostgreSQL：证券主数据、供应商代码映射、交易日历目录、采集运行和质量问题。
- ClickHouse：5 分钟/日线行情、横截面指标和市场状态快照。
- 对象存储 Parquet：供应商原始响应、回放数据和重算输入。
- Redis Streams：增量采集、补数、收盘核对和展望重算任务。

## 预测输出

`MarketOutlook` 必须包含目标、市场、`as_of`、周期、上涨/震荡/下跌概率、收益分位数、绝对与相对基准方向、置信度、主要贡献、失效条件、数据版本和模型版本。事件影响、市场状态、预期差和量价资金因子分别计算，再通过版本化校准器集成。

### 当前基线概率的计算过程

当前 `outlook-baseline-v2` 是可解释基线，不应解读为已完成历史校准的真实概率。计算步骤为：

1. 使用近 5 个收盘价相对近 20 个收盘价的强弱计算 `market_state` 趋势分数；同时使用日收益率标准差乘以 `√252` 计算年化实现波动率。
2. 将市场状态、事件影响、预期差、已定价程度限制在 `[-1, 1]`，配置权重为 `45% / 25% / 20% / 10%`。不可用因子退出本次计算，剩余因子按配置权重同比例归一化；不可用不等于中性：

   ```text
   weighted_score = market_state × 0.45
                  + event × 0.25
                  + expectation_gap × 0.20
                  + priced_in × 0.10
   ```

3. 根据波动率和预测周期计算尺度：

   ```text
   scale = max(0.08, volatility × √horizon / 2)
   ```

4. 使用指数归一化得到三分类概率：

   ```text
   up_score   = exp(weighted_score / scale)
   flat_score = exp(1 - min(2, abs(weighted_score / scale)))
   down_score = exp(-weighted_score / scale)
   P(class)   = class_score / (up_score + flat_score + down_score)
   ```

   因此，正的综合分数提高上涨概率，负的综合分数提高下跌概率；接近零的标准化分数提高震荡概率。该公式修复了 v1 中 `flat_score=1` 导致震荡类别几乎无法胜出的问题。

事件影响已通过 `forecast-factor-v1` 接入：只使用 `as_of` 之前平台已知的 approved 分析，按预测周期重算目标影响，并要求证券与影响目标之间存在已批准、有效期内的 instrument / industry / market 映射。行业映射还必须经过同一分类版本下有效的标的成员关系才能传导至证券。名称命中只生成 `proposed` 候选，不能直接进入预测。因子输出包含目标、快照、主导事件、置信度、来源哈希、映射类型和规则版本；无映射或无有效快照返回 `score=null/status=unavailable`。预期差和已定价程度尚未接入时同样退出计算。1/3/5/20 日预测分别要求至少 60/90/120/250 个交易日；若行情为空、过期、特征缺失或样本不足，`probabilities=null`，并返回实际样本、所需样本和结构化阻断原因。

## 当前实现

已完成 Provider 契约、东方财富/桥接/AKShare 适配边界、主备回退、能力 API、持久化证券目录、正式交易日历、快照/K线 API、可回放市场状态和基础测试。`MarketOutlookService` 与 `/api/v1/market/outlooks` 提供 1/3/5/20 日可解释三分类概率和收益区间；`/api/v1/market/factors` 可独立检查事件因子及映射状态。没有 published 校准版本时结果明确标记 `baseline_uncalibrated`。

### 证券与行业主数据治理

迁移 `20260822_0029` 增加 `market_instruments`、`industry_taxonomies`、`industry_classifications`、`instrument_industry_memberships` 和 `impact_target_mappings`。应用启动时幂等初始化引导数据，再从数据库中的 active 标的构建 Provider Catalog，避免静态进程目录成为事实来源。

影响目标映射采用审核状态机：研究员可手工创建或通过精确名称/别名命中生成 `proposed` 候选，reviewer/admin 才能批准或拒绝，已批准映射可以退休。每次审核转换写入 AuditLog。在线因子同时检查 `created_at <= as_of`、`valid_from/valid_to`、映射状态和行业成员关系状态，从而支持历史截面回放且避免未来主数据泄漏。

查询与治理接口包括：

- `GET /api/v1/market/instruments`
- `GET /api/v1/market/industry-taxonomies`
- `GET /api/v1/market/industry-classifications`
- `GET/POST /api/v1/market/impact-target-mappings`
- `POST /api/v1/market/impact-target-mappings/suggest`
- `POST /api/v1/market/impact-target-mappings/{id}/transition`

管理端入口为 `/admin/market-master-data`，研究员可查看主数据并生成候选，reviewer/admin 可在同一工作台完成批准、拒绝和退役。

行业分类导入采用完整快照和两阶段发布，详见 [市场主数据版本化导入](./82-market-master-data-import.md)。迁移 `20260823_0030` 保存每次导入的来源哈希、计数、校验错误、警告和发布状态；相同来源快照幂等复用。未来生效版本提前发布时，旧成员关系保留到切换时点，新关系在同一时点开始生效。

当 Outbox 消费暂停、旧版部署遗漏投影或恢复历史数据时，管理员可调用 `POST /api/v1/impact-projections/backfill`，或运行 `python -m app.worker impact-backfill`。该任务只扫描请求 `as_of` 时刻已知的 approved 分析，使用固定 `as_of` 保证重复执行不重复写快照；响应会分别报告活跃、已过期和未来生效的贡献。已过期或不匹配预测周期的影响仍保持不可用，而不是被回填任务错误重启。

前端通过“市场展望”工作台消费该接口，按市场和预测周期查看指数/ETF 卡片，并在同一视图中呈现概率、收益区间、贡献因子和风险提示。页面不隐藏供应商降级或未校准状态。

工作台按周期自动选择足够长的自然日回看窗口（120/180/240/500 日），避免固定 45 日请求永远无法达到最低交易日样本门。贡献拆解显示因子覆盖率、不可用原因，并可从事件因子直接跳转到对应目标影响详情。

`app.market.evaluation` 已提供 `forecast-evaluation-v1` 评估内核：三分类 Brier Score、Log Loss、方向命中率、预测覆盖率、可靠性分箱/ECE、带 purge/embargo 的扩展窗口切分，以及带最小样本门的温度校准。预测运行、真实结果和校准草稿已持久化；发布治理与在线应用仍待完成。

### 预测生命周期与评估治理

迁移 `20260822_0027` 增加三类不可变记录：

- `market_forecast_runs`：固化预测时点、概率、收益分位数、规则版本、因子来源哈希和完整输入快照；`source_hash` 唯一，重复请求幂等。
- `market_forecast_outcomes`：在第 N 个后续交易日行情可用后写入真实收益与三分类标签；每个预测最多一个结果。
- `market_calibration_versions`：保存市场/周期级校准参数、训练区间、样本数、评估指标和 draft/published 状态。

API 分为预览与留痕两条路径：`GET /api/v1/market/outlooks` 不写数据；`POST /api/v1/market/forecast-runs` 正式签发并固化预测。`POST /api/v1/market/forecast-outcomes/settle` 可人工触发结果回填，`python -m app.worker forecast-outcomes` 支持持续回填；`GET /api/v1/market/evaluations` 输出覆盖率、命中率、Brier、Log Loss、ECE、可靠性分箱和排除原因。数据不足预测同样进入分母，避免只评估成功预测造成幸存者偏差。

前端“预测评估”页面按 A/H/US 与 1/3/5/20 日切片展示评估卡片、可靠性图、样本构成及校准版本。无预测样本时覆盖率返回 `null` 而不是 100%；只有样本存在但尚无可评估结果时才返回 0%。

校准版本发布必须通过治理门：可评估样本不少于 200、覆盖率不低于 75%、Brier Score 不高于 0.75、ECE 不高于 0.10，且训练区间已经闭合。发布同一市场/周期的新版本会自动退休旧发布版本并写入审计日志。迁移 `20260822_0028` 在预测运行上固化 `calibration_version_id`；在线预览和正式签发只读取 published 版本，使用温度缩放后标记为 `ready/calibrated`，未发布草稿绝不会影响线上结果。

`GET /api/v1/market/model-comparisons` 提供规则版本的冠军/挑战者对比。系统只在不同版本拥有相同 `(instrument_id, as_of, horizon)` 已结算样本时计算共同指标；样本不足、没有两个版本或挑战者未同时改善 Brier/Log Loss、未恶化 ECE 和命中率时，结论保持 `insufficient_*` 或 `retain_incumbent`。它只生成推荐，模型进入线上仍须走校准发布质量门和人工审核。

### 时间点安全的历史预测回放

本地不可变行情归档使用 `market-archive-v2`：文件保存采集运行、标准化 K 线和对规范 JSON 内容计算的 SHA-256。严格回放读取器拒绝旧格式、缺少哈希或内容被修改的文件；同一标的/周期/观察时点出现多个修订时，只选取预测 `as_of` 当时已经摄取且可用的最新版本。历史回放绝不回退到东方财富、桥接或 AKShare 实时接口。

管理员通过 `POST /api/v1/market/forecast-replays` 提交标的、预测日期范围、周期、回看长度、收盘后发布延迟和最大槽位。服务使用交易日历为每个开市日生成决策时点，复用正式 `ForecastLifecycleService` 签发不可变预测，并使用同一归档截至 `evaluation_as_of` 可见的后续行情自动结算。重复执行复用相同来源哈希的预测，不重复写结果；响应报告计划/处理槽位、新建/复用、数据不足、已结算/待结算、归档告警和规则版本，操作写入 AuditLog。

历史预测选择校准版本时同时要求 `created_at <= as_of`、`published_at <= as_of` 和 `train_end < as_of`。因此今天发布的校准器不能修改昨天的模拟决策，训练样本也不能穿过预测时点。结算后的样本自动进入 `/market/evaluations` 和 `/market/model-comparisons`，但冠军/挑战者仍要求共同预测槽位和最小样本门，不因批量生成数据而降低治理标准。

市场状态还会计算 `freshness_lag_seconds`。A股、港股和美股分别使用 `exchange_calendars` 的 XSHG、XHKG、XNYS 日历计算预期样本和日线新鲜度，支持节假日、半日市和美股夏令时；5 分钟线超过 30 分钟未更新即为 `stale_data`。Python 3.9 未安装该可选依赖时保留工作日参考日历并明确降级。

当供应商请求失败或返回空序列时，状态快照会保留按标的归属的 `data_warnings`，并透传到展望风险字段；前端因此可以区分“真实中性”与“行情不可用导致的均匀概率”。

`/api/v1/market/quality` 提供查询级质量摘要，包括覆盖率、缺失/陈旧标的数量、最大新鲜度延迟和结构化告警。该摘要是后续 market-data worker、ClickHouse 落库和监控告警的稳定边界。

`/api/v1/market/providers/health` 进一步区分供应商“已配置”和“最近真实请求成功”：未发生成功请求时返回 `operational_status=unknown`，不可用时返回 `unavailable`，避免把包安装或配置存在误报为行情可用。

`MarketIngestService` 已将供应商查询封装为可重放的采集批次，冻结 `as_of`、时间范围、供应商、运行状态、告警和标准化 `MarketBar`；持久化适配器可以在不修改 Provider 或研究层的情况下写入 ClickHouse 与 MinIO。

当前已提供 `MarketBatchStore` 端口、本机原子 JSON 归档适配器、ClickHouse `ReplacingMergeTree` 插入适配器和 `market-data` Worker。`MARKET_DATA_STORE` 支持 `local`、`clickhouse`、`dual`：dual 先完成本地不可变归档，再镜像 ClickHouse，镜像失败返回 degraded receipt 而不丢失主归档。Worker 支持 `MARKET_DATA_INSTRUMENT_IDS`、`MARKET_DATA_INTERVAL`、`MARKET_DATA_LOOKBACK_DAYS` 和 `MARKET_DATA_WORKER_INTERVAL_SECONDS`；持久化异步回放 Job、MinIO、断点续传、缺口补数和收盘核对仍是下一步工作。

新增 `EastMoneyBridgeMarketDataProvider`，读取本机 `eastmoney-api-bridge` 的标准化 K 线与分时接口；通过 `MARKET_DATA_PROVIDER=bridge` 启用，桥接服务返回的 `fresh/stale/captured_at` 会保留到平台质量告警和 `as_of` 校验中。该模式不直接依赖浏览器桥接项目的 SQLite 或原始东方财富协议。

主备路由按标的和请求范围检查日线完整度，而不再把“状态为 ok 但只有少量缓存”视为成功。桥接历史不足时依次尝试东方财富直连和 AKShare，并为每个标的选择样本更完整的数据集；不会混合不同复权口径的两段 K 线。所有 Provider 的 `limit` 均按单标的执行，避免批量查询时后一个标的挤掉前一个标的历史。
