# EastMoney Bridge 行情接入与故障恢复

## 1. 目的

本文记录 FinSightAgent 从 `eastmoney-api-bridge` 获取行情、生成市场展望，以及处理“展望长期显示 33%”问题的标准运行方式。

2026-08-22 的运行验收中，桥接端沪深300仅有 28 条历史缓存。平台现已按请求范围检查逐标的完整度：不足时依次尝试东方财富直连与 AKShare，并选择样本更多且口径一致的一组行情。本次环境的直连被上游断开、AKShare 代理连接失败、桥接浏览器补采超时，因此最终仍保留 28 条并返回 `probabilities=null`；这是可观测的数据源阻断，不再表现为 33% 占位概率。

## 2. 标准数据链路

```text
Chrome / 东方财富页面
        ↓ 浏览器网络采集
eastmoney-api-bridge :8765
        ↓ 标准化 kline / trends
EastMoneyBridgeMarketDataProvider
        ↓ MarketBar
MarketStateService
        ↓ 趋势、波动率、覆盖率、新鲜度
MarketOutlookService
        ↓
市场展望页面 /api/v1/market/outlooks
```

FinSight 只依赖桥接项目的标准化接口，不解析东方财富原始响应。

## 3. 关键接口

| 用途 | 接口 |
| --- | --- |
| 服务健康 | `GET http://127.0.0.1:8765/health` |
| 浏览器连接 | `POST /api/v1/browser/connect` |
| 启动采集 | `POST /api/v1/browser/capture/start` |
| 采集状态 | `GET /api/v1/browser/status` |
| 日线 K 线 | `GET /api/v1/market/kline/{secid}` |
| 分时数据 | `GET /api/v1/market/trends/{secid}` |
| 最新采集 | `GET /api/v1/market/latest` |

默认标的代码：

```text
1.000001  上证指数
1.000300  沪深300
1.510300  沪深300ETF
```

## 4. 启动顺序

### 4.1 启动桥接服务

```bash
EASTMONEY_BROWSER_ENABLED=1 \
EASTMONEY_DIRECT_HTTP_ENABLED=1 \
/Users/zhaozhengpin/Workspace/experiments/FinSightAgent/.venv/bin/uvicorn \
app.main:app --host 127.0.0.1 --port 8765
```

### 4.2 启动 Chrome CDP

```bash
cd ~/Workspace/experiments/eastmoney-api-bridge
zsh scripts/start_chrome.sh
```

### 4.3 启动采集

```bash
curl -X POST http://127.0.0.1:8765/api/v1/browser/connect
curl -X POST http://127.0.0.1:8765/api/v1/browser/capture/start
```

目标标的页面必须实际发起 K 线或分时请求；只打开市场列表页通常只能得到列表行情，不能保证有历史 K 线。

## 5. “33%”问题的根因

当行情为空、过期或供应商失败时，市场状态为 `insufficient_data` / `stale_data`。历史实现将内部占位分布：

```text
up   = 0.3333
flat = 0.3334
down = 0.3333
```

这不是预测结果，只代表系统没有足够数据生成方向性结论。

已完成的修复：

1. 前端在数据不足时显示“暂无方向性结论”，不再显示三等分百分比；
2. `bridge` 模式使用“浏览器桥接首选、东方财富直连回退”；
3. 本地桥接请求使用 `trust_env=False`，避免 `HTTP(S)_PROXY` 将 `127.0.0.1` 请求转发到代理后产生 502；
4. Playwright 在没有默认 browser context 时自动创建 context，避免采集启动 `IndexError`；
5. 桥接缓存数据仍通过 `MarketBar` 标准化后才进入趋势和展望计算。

## 6. 验证方式

检查桥接日线：

```bash
curl 'http://127.0.0.1:8765/api/v1/market/kline/1.000001?allow_stale=true&limit=250'
```

检查 FinSight 展望：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8000/api/v1/market/outlooks?instrument_ids=cn:index:000001&start=2026-05-01T00:00:00Z&end=2026-08-21T00:00:00Z&horizon=1'
```

当响应的 `data_status` 为 `baseline_uncalibrated` 且概率不再接近固定三等分时，说明行情已进入计算链路。`baseline_uncalibrated` 表示规则模型尚未完成历史 walk-forward 校准，不表示数据为空。

## 7. 当前验证结果（2026-08-21）

已验证：

- 上证指数可从桥接获取日线 K 线；
- 沪深 300 可从桥接获取日线 K 线；
- FinSight 能输出非均匀上涨/震荡/下跌概率；
- 本地代理环境不会再导致桥接请求 502；
- 采集器可正常启动并持续上报 capture/heartbeat。

尚未完成：

- 沪深 300 ETF 的历史 K 线采集；
- 所有市场的历史数据回填与本地归档；
- 基于历史样本的概率校准和可靠性评估。

## 8. 后续工作

1. 为 ETF 和港股/美股标的建立可复用的行情页面导航与采集任务；
2. 将桥接标准化数据写入 `LocalMarketBatchStore` 或 ClickHouse；
3. 增加采集覆盖率、最近采集时间和标的级数据告警；
4. 完成 walk-forward 校准后，将 `baseline_uncalibrated` 替换为已评估的模型版本；
5. 在前端展示数据来源、采集时间、覆盖率和模型校准状态。
