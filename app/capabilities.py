"""Versioned event capability packs for the generic research runtime.

Capability packs keep event-specific extraction and research knowledge out of the
core Agent runtime.  A pack is declarative: executable calculations and tool
adapters remain registered in code, while the manifest and workflow template
can be evaluated, versioned, shadowed and promoted independently.
"""

# Declarative manifests keep related schema references together for review.
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PackStatus = Literal["candidate", "draft", "validated", "shadow", "canary", "active", "deprecated", "retired"]


class CapabilityPackManifest(BaseModel):
    """Stable, serializable contract for one event capability pack."""

    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: PackStatus = "draft"
    display_name: str = Field(min_length=1)
    parent_type: str = Field(min_length=1)
    event_types: list[str] = Field(min_length=1)
    subtypes: list[str] = Field(default_factory=list)
    input_schema_ref: str = Field(min_length=1)
    event_schema_ref: str = Field(min_length=1)
    analysis_schema_ref: str = Field(min_length=1)
    workflow_template_ref: str = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    quality_gate_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityPack:
    """Runtime representation of a manifest plus non-executable pack metadata."""

    manifest: CapabilityPackManifest
    positive_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    derived_fields: tuple[str, ...] = ()
    research_questions: tuple[str, ...] = ()
    mechanism_templates: tuple[dict[str, Any], ...] = ()
    target_types: tuple[str, ...] = ()
    examples: tuple[dict[str, Any], ...] = ()

    @property
    def pack_id(self) -> str:
        return self.manifest.pack_id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def status(self) -> PackStatus:
        return self.manifest.status


@dataclass(frozen=True)
class CapabilityPlanTask:
    name: str
    capability: str
    required: bool = True
    dependencies: tuple[str, ...] = ()
    output_field: str | None = None
    optional_when: str | None = None


@dataclass(frozen=True)
class CapabilityResearchPlan:
    pack_id: str
    pack_version: str
    tasks: tuple[CapabilityPlanTask, ...]
    missing_fields: tuple[str, ...] = ()
    phase: str = "preliminary"


class CapabilityPackError(ValueError):
    """Raised for invalid lifecycle transitions or ambiguous pack selection."""


class CapabilityPackRegistry:
    """In-process registry with explicit version and lifecycle semantics.

    Persistence can be layered behind this interface.  The registry deliberately
    keeps all versions so old workflows can be replayed with their original pack.
    """

    _ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        "candidate": {"draft", "retired"},
        "draft": {"validated", "retired"},
        "validated": {"shadow", "retired"},
        "shadow": {"canary", "validated", "retired"},
        "canary": {"active", "shadow", "retired"},
        "active": {"deprecated", "retired"},
        "deprecated": {"retired"},
        "retired": set(),
    }

    def __init__(self, packs: list[CapabilityPack] | None = None) -> None:
        self._packs: dict[tuple[str, str], CapabilityPack] = {}
        self._active: dict[str, str] = {}
        for pack in packs or []:
            self.register(pack)

    def register(self, pack: CapabilityPack) -> CapabilityPack:
        key = (pack.pack_id, pack.version)
        if key in self._packs:
            raise CapabilityPackError(f"capability pack already registered: {pack.pack_id}@{pack.version}")
        self._packs[key] = pack
        if pack.status == "active":
            self._set_active(pack)
        return pack

    def get(self, pack_id: str, version: str | None = None) -> CapabilityPack | None:
        if version is None:
            version = self._active.get(pack_id)
        return self._packs.get((pack_id, version)) if version else None

    def list(self, *, status: PackStatus | None = None) -> list[CapabilityPack]:
        values = list(self._packs.values())
        if status:
            values = [pack for pack in values if pack.status == status]
        return sorted(values, key=lambda pack: (pack.pack_id, pack.version))

    def resolve_for_event(self, event_type: str, *, subtype: str | None = None) -> CapabilityPack | None:
        candidates = [
            pack for pack in self._packs.values()
            if pack.status == "active" and event_type in pack.manifest.event_types
        ]
        if subtype:
            candidates = [pack for pack in candidates if subtype in pack.manifest.subtypes] or candidates
        if not candidates:
            return None
        return max(candidates, key=lambda pack: tuple(int(part) for part in pack.version.split(".")))

    def transition(self, pack_id: str, version: str, status: PackStatus) -> CapabilityPack:
        current = self.get(pack_id, version)
        if current is None:
            raise CapabilityPackError(f"capability pack not found: {pack_id}@{version}")
        if status not in self._ALLOWED_TRANSITIONS[current.status]:
            raise CapabilityPackError(f"invalid capability pack transition: {current.status}->{status}")
        updated = CapabilityPack(
            manifest=current.manifest.model_copy(update={"status": status}),
            positive_terms=current.positive_terms,
            negative_terms=current.negative_terms,
            required_fields=current.required_fields,
            optional_fields=current.optional_fields,
            derived_fields=current.derived_fields,
            research_questions=current.research_questions,
            mechanism_templates=current.mechanism_templates,
            target_types=current.target_types,
            examples=current.examples,
        )
        self._packs[(pack_id, version)] = updated
        if status == "active":
            self._set_active(updated)
        return updated

    def _set_active(self, pack: CapabilityPack) -> None:
        previous_version = self._active.get(pack.pack_id)
        if previous_version and previous_version != pack.version:
            previous = self._packs[(pack.pack_id, previous_version)]
            self._packs[(pack.pack_id, previous_version)] = CapabilityPack(
                manifest=previous.manifest.model_copy(update={"status": "deprecated"}),
                positive_terms=previous.positive_terms,
                negative_terms=previous.negative_terms,
                required_fields=previous.required_fields,
                optional_fields=previous.optional_fields,
                derived_fields=previous.derived_fields,
                research_questions=previous.research_questions,
                mechanism_templates=previous.mechanism_templates,
                target_types=previous.target_types,
                examples=previous.examples,
            )
        self._active[pack.pack_id] = pack.version


def compile_capability_plan(
    pack: CapabilityPack,
    *,
    extracted_fields: dict[str, Any] | None = None,
    verified: bool = False,
    market_data_available: bool = True,
) -> CapabilityResearchPlan:
    """Compile a typed research DAG from pack metadata and current data state."""
    extracted = extracted_fields or {}
    missing = tuple(field for field in pack.required_fields if not extracted.get(field))
    tasks: list[CapabilityPlanTask] = [
        CapabilityPlanTask("extract_structured_facts", "structured_extract", output_field="event_fields"),
        CapabilityPlanTask("verify_primary_evidence", "fact_verify", dependencies=("extract_structured_facts",), output_field="fact_check"),
        CapabilityPlanTask("resolve_entities", "entity_resolve", dependencies=("extract_structured_facts",), output_field="entities"),
    ]
    if missing:
        tasks.append(CapabilityPlanTask("fill_missing_fields", "targeted_retrieve", dependencies=("verify_primary_evidence",), required=False, output_field="missing_fields"))
    if "company_analyze" in pack.manifest.required_capabilities:
        tasks.append(CapabilityPlanTask("analyze_company", "company_analyze", dependencies=("assess_event",), output_field="company_analysis"))
    if "industry_analyze" in pack.manifest.required_capabilities:
        tasks.append(CapabilityPlanTask("analyze_industry", "industry_analyze", dependencies=("assess_event",), output_field="industry_analysis"))
    if market_data_available and "market_analyze" in pack.manifest.required_capabilities:
        tasks.append(CapabilityPlanTask("analyze_market_reaction", "market_analyze", dependencies=("assess_event",), output_field="market_analysis"))
    tasks.extend([
        CapabilityPlanTask("assess_event", "preliminary_assessor", dependencies=("verify_primary_evidence", "resolve_entities"), output_field="preliminary_assessment"),
        CapabilityPlanTask("build_impact_graph", "impact_analyze", dependencies=("assess_event",), output_field="impact_analysis"),
        CapabilityPlanTask("challenge_conclusion", "skeptic_review", dependencies=("build_impact_graph",), output_field="skeptic_review"),
        CapabilityPlanTask("synthesize_research", "synthesize", dependencies=("build_impact_graph", "challenge_conclusion"), output_field="research_conclusion"),
    ])
    return CapabilityResearchPlan(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        tasks=tuple(tasks),
        missing_fields=missing,
        phase="verified" if verified and not missing else "preliminary",
    )


EQUITY_FINANCING_PACK = CapabilityPack(
    manifest=CapabilityPackManifest(
        pack_id="capital-markets.equity-financing",
        version="1.0.0",
        status="active",
        display_name="股权融资与新股配售",
        parent_type="capital_markets",
        event_types=["equity_financing"],
        subtypes=["share_placement", "rights_issue", "public_offering", "private_placement", "convertible_financing"],
        input_schema_ref="event-document/1.0.0",
        event_schema_ref="equity-financing-event/1.0.0",
        analysis_schema_ref="impact-analysis/2.1.0",
        workflow_template_ref="equity-financing-research/1.0.0",
        required_capabilities=["fact_verify", "company_analyze", "market_analyze", "impact_analyze", "skeptic_review"],
        allowed_tools=["search_official_filings", "resolve_security", "get_market_bars", "get_market_snapshots", "find_similar_events"],
        quality_gate_ref="equity-financing-quality/1.0.0",
    ),
    positive_terms=("配售新股", "新股配售", "配股", "增发", "募资", "超额认购", "定向发行"),
    negative_terms=("股东减持", "拟减持", "集中竞价减持"),
    required_fields=("issuer", "financing_method", "announcement_stage", "use_of_proceeds"),
    optional_fields=("gross_proceeds", "net_proceeds", "currency", "new_shares", "dilution_ratio", "placement_price", "discount_rate", "oversubscription_ratio"),
    derived_fields=("dilution_ratio", "discount_rate"),
    research_questions=("融资是否造成短期摊薄？", "募集资金能否改善中长期增长？", "认购需求是否抵消供给冲击？"),
    mechanism_templates=(
        {"name": "dilution", "path": ["new_shares", "shares_outstanding", "eps", "valuation"]},
        {"name": "investment", "path": ["proceeds", "capex", "growth_expectations", "valuation"]},
        {"name": "subscription_signal", "path": ["oversubscription", "demand_signal", "sentiment", "price"]},
    ),
    target_types=("company", "security", "industry", "market", "asset_class"),
)


GENERIC_EVENT_PACK = CapabilityPack(
    manifest=CapabilityPackManifest(
        pack_id="generic.economic-event",
        version="1.0.0",
        status="active",
        display_name="通用经济事件",
        parent_type="economic_event",
        event_types=["generic_economic_event", "general_market_news"],
        input_schema_ref="event-document/1.0.0",
        event_schema_ref="generic-economic-event/1.0.0",
        analysis_schema_ref="impact-analysis/2.1.0",
        workflow_template_ref="generic-economic-research/1.0.0",
        required_capabilities=["fact_verify", "impact_analyze", "skeptic_review"],
        allowed_tools=["search_official_filings", "find_similar_events"],
        quality_gate_ref="generic-economic-quality/1.0.0",
    ),
    required_fields=("subject", "action", "announcement_stage"),
    research_questions=("事件事实是什么？", "哪些变量和资产可能受到影响？", "哪些条件会使判断失效？"),
    target_types=("market", "industry", "company", "asset_class"),
)


def default_capability_registry() -> CapabilityPackRegistry:
    return CapabilityPackRegistry([EQUITY_FINANCING_PACK, GENERIC_EVENT_PACK])
