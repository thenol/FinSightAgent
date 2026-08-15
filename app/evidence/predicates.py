"""受控谓词词表。

谓词使用版本化的受控词表（DD-40 §5）。未知谓词可提交候选，但进入
``unverified``（reason ``PREDICATE_UNSUPPORTED``），不得临时拼写新谓词绕过校验。

词表按一等事件类型定义。新增谓词需要升级 ``PREDICATE_VERSION`` 并补 Claim 模板。
"""

from dataclasses import dataclass
from typing import Optional

PREDICATE_VERSION = "controlled-v3"

# 受控谓词 -> {object_type, summary}。object_type 约束 ClaimNormalizer 接受的值类型。
CONTROLLED_PREDICATES: dict[str, dict[str, str]] = {
    "document_discloses_event": {
        "object_type": "string",
        "summary": "文档披露了某类事件存在",
    },
    "geopolitical_action": {
        "object_type": "string",
        "summary": "主体采取地缘政治行动（开战/制裁/军演等）",
    },
    "expects_net_profit": {
        "object_type": "range",
        "summary": "公司预计净利润落入某区间",
    },
    "expects_net_profit_change": {
        "object_type": "range",
        "summary": "公司预计净利润同比变化幅度区间",
    },
    "signs_major_contract": {
        "object_type": "decimal",
        "summary": "公司签订重大合同金额",
    },
    "announces_merger_acquisition": {
        "object_type": "entity",
        "summary": "公司公告并购重组标的",
    },
    "reduces_holding": {
        "object_type": "decimal",
        "summary": "股东减持股份数量或比例",
    },
    "penalized_by_regulator": {
        "object_type": "string",
        "summary": "公司或主体被监管处罚",
    },
    "reduces_holding_ratio": {
        "object_type": "decimal",
        "summary": "股东减持持股比例",
    },
    "adjusts_policy_rate": {
        "object_type": "string",
        "summary": "政策主体调整利率（加息/降息/维持）",
    },
}


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    object_type: str
    summary: str
    version: str


def get_predicate(name: str) -> Optional[PredicateSpec]:
    spec = CONTROLLED_PREDICATES.get(name)
    if not spec:
        return None
    return PredicateSpec(
        name=name,
        object_type=spec["object_type"],
        summary=spec["summary"],
        version=PREDICATE_VERSION,
    )


def is_controlled(name: str) -> bool:
    return name in CONTROLLED_PREDICATES
