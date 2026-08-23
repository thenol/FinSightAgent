# 市场主数据版本化导入

## 目标

将许可行业分类和标的成分作为完整快照导入，经过校验、暂存和审核发布后才影响在线研究。导入不接受增量半成品；供应商增量应先在 Connector 内合成为完整版本快照。

## 状态与切换

1. `POST /api/v1/market/master-data/imports` 校验来源快照并创建 ImportRun。
2. 校验失败的运行记录为 `rejected`，不创建分类和成员关系。
3. 校验成功后创建 draft taxonomy、active classifications 和 proposed memberships。
4. reviewer/admin 调用 `/imports/{id}/publish`；旧成员关系的 `valid_to` 与新版本 `valid_from` 对齐，新关系在该时点开始可见。
5. 相同规范化载荷使用 `source_hash` 幂等复用，不重复创建版本。

发布是治理动作，不等于提前生效。未来版本可以提前发布，但因子服务仍按 `valid_from/valid_to` 和 `as_of` 选择成员关系，因此不会产生切换空窗或未来数据泄漏。

## 校验门

- 分类代码在版本内唯一，父节点必须存在且父层级小于子层级。
- 标的必须存在于 active 证券目录，行业代码必须存在于本次完整快照。
- 同一标的和行业不能重复；权重必须位于 `(0, 1]`，合计不得超过 1。
- 每个标的最多一个主分类。
- `effective_from` 必须带时区；同一 standard/version 不可重复创建不同内容。

## JSON 合同示例

```json
{
  "standard": "licensed-industry-standard",
  "version": "2026-09",
  "name": "许可行业分类 2026-09",
  "source": "licensed-reference-feed",
  "effective_from": "2026-09-01T00:00:00+08:00",
  "classifications": [
    {
      "code": "cn-financials",
      "name": "金融",
      "level": 1,
      "parent_code": null,
      "aliases": []
    },
    {
      "code": "cn-banks",
      "name": "银行",
      "level": 2,
      "parent_code": "cn-financials",
      "aliases": ["银行业"]
    }
  ],
  "memberships": [
    {
      "instrument_id": "cn:etf:512800",
      "industry_code": "cn-banks",
      "weight": 1.0,
      "is_primary": true
    }
  ],
  "source_metadata": {
    "license": "internal-use",
    "supplier_snapshot_id": "snapshot-2026-09"
  }
}
```

管理页面位于 `/admin/market-master-data`。上传只支持 JSON 完整快照；生产 Connector 应调用同一 API，从而复用全部校验、幂等、审计和发布规则。
