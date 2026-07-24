"""引用解析器。

CitationResolver 把 Claim ID 解析为 Evidence 定位和授权展示内容（DD-60 §9）。
按来源许可与调用方角色决定返回全文片段、有限摘要或仅返回原文入口。
"""

from typing import Any, Literal

from app.domain import Claim, FactCard
from app.platform.repository import Repository

# 展示范围：full（全文片段）、excerpt（有限摘要）、entry（仅入口，不返回正文）
DisplayScope = Literal["full", "excerpt", "entry"]

# 来源等级 -> 默认展示范围（MVP 基线；可被 Source.license 覆盖）
TIER_DISPLAY_SCOPE: dict[str, DisplayScope] = {
    "S": "full",
    "A": "excerpt",
    "B": "excerpt",
    "C": "entry",
}

# Source.license -> 强制展示范围；inherit 表示沿用 trust_tier 默认
LICENSE_DISPLAY_SCOPE: dict[str, DisplayScope | None] = {
    "inherit": None,
    "full": "full",
    "excerpt": "excerpt",
    "entry_only": "entry",
}

EXCERPT_MAX_LENGTH = 200

# API 角色 → CitationResolver 展示角色（publisher 视为对外渠道）
API_ROLE_TO_CITATION_ROLE: dict[str, str] = {
    "publisher": "external",
    "researcher": "researcher",
    "reviewer": "researcher",
    "admin": "researcher",
}


class CitationResolver:
    """把报告中的 Claim 引用解析为可展示的 Evidence 定位。"""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @staticmethod
    def citation_role_for_api(role: str) -> str:
        return API_ROLE_TO_CITATION_ROLE.get(role, "external")

    def resolve(
        self,
        claim: Claim,
        *,
        role: str = "researcher",
        document_source_tier: str | None = None,
        license: str | None = None,
    ) -> dict[str, Any] | None:
        evidence_id = claim.evidence_ids[0] if claim.evidence_ids else None
        if not evidence_id:
            return None
        evidence = self.repository.get_evidence(evidence_id)
        if evidence is None:
            return None

        source_tier = document_source_tier
        source_license = license
        if source_tier is None or source_license is None:
            document = self.repository.get_document(evidence.document_id)
            if document is not None:
                if source_tier is None:
                    source_tier = document.source_tier
                if source_license is None:
                    source = self.repository.get_source(document.source_id)
                    if source is not None:
                        source_license = source.license

        scope = self._scope(role, source_tier, source_license)
        return {
            "claim_id": claim.id,
            "evidence_id": evidence.id,
            "locator": evidence.locator,
            "display_scope": scope,
            "excerpt": self._masked_excerpt(evidence.excerpt, scope),
        }

    def resolve_report(self, report: FactCard, *, role: str = "researcher") -> list[dict[str, Any]]:
        """按报告版本快照的 Claim ID 返回可展示引用，不修改报告内容。"""
        citations = []
        for claim_id in report.claim_ids:
            claim = self.repository.get_claim(claim_id)
            if claim is None:
                continue
            citation = self.resolve(claim, role=role)
            if citation is not None:
                citations.append(citation)
        return citations

    def authorized_document_content(
        self,
        content: str | None,
        *,
        role: str,
        source_tier: str | None,
        license: str | None = None,
    ) -> tuple[DisplayScope, str | None]:
        """按角色、来源等级与 Source.license 决定 API 是否返回文档正文。"""

        scope = self._scope(role, source_tier, license)
        if content is None or scope == "entry":
            return scope, None
        return scope, content

    def _scope(
        self,
        role: str,
        source_tier: str | None,
        license: str | None = None,
    ) -> DisplayScope:
        # 公开/外部调用方只能看入口
        if role == "external":
            return "entry"
        override = LICENSE_DISPLAY_SCOPE.get(license or "inherit")
        if override is not None:
            return override
        if source_tier is None:
            return "excerpt"
        return TIER_DISPLAY_SCOPE.get(source_tier, "excerpt")

    @staticmethod
    def _masked_excerpt(excerpt: str, scope: DisplayScope) -> str | None:
        if scope == "full":
            return excerpt
        if scope == "excerpt":
            return excerpt[:EXCERPT_MAX_LENGTH]
        return None  # entry：不返回正文
