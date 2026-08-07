import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain import (
    Claim,
    ClaimEvidenceRelation,
    ConflictRecord,
    Document,
    Event,
    EvidenceSpan,
    ReviewTask,
)
from app.events.schemas import ClaimTemplate, get_schema
from app.evidence.claims import (
    ClaimFingerprint,
    ClaimNormalizer,
)
from app.evidence.conflicts import ConflictDetector
from app.evidence.policy import EvidencePolicyService, EvidenceRecord
from app.ingestion.blocks import PARSER_VERSION, DocumentBlockReader
from app.platform.ids import new_id
from app.platform.repository import Repository
from app.review.service import AutoReviewService

EXCERPT_MAX_LENGTH = 500


class EvidenceService:
    """把事件披露文档转换为可定位证据、规范化事实与来源政策决定。

    流程（DD-40 §3）：读取块 -> 注册证据 -> ClaimNormalizer 规范化 ->
    ClaimFingerprint 生成 -> EvidencePolicyService 决定状态与置信度 -> 持久化
    Claim、ClaimEvidence 关系（同事务）。
    """

    def __init__(
        self,
        repository: Repository,
        block_reader: Optional[DocumentBlockReader] = None,
        normalizer: Optional[ClaimNormalizer] = None,
        policy: Optional[EvidencePolicyService] = None,
    ) -> None:
        self.repository = repository
        self.block_reader = block_reader or DocumentBlockReader()
        self.normalizer = normalizer or ClaimNormalizer()
        self.fingerprinter = ClaimFingerprint()
        self.policy = policy or EvidencePolicyService()
        self.conflict_detector = ConflictDetector()

    def register_event_disclosure(
        self, document: Document, event: Event
    ) -> tuple[EvidenceSpan, Claim]:
        """旧调用约定：返回业务 Claim 的首项，必要时回退到事件存在 Claim。"""
        evidence, claims = self.register_event_claims(document, event)
        if not claims or evidence is None:
            raise RuntimeError("NO_SOURCE_TEXT")
        return evidence, claims[0]

    def register_event_claims(
        self, document: Document, event: Event
    ) -> tuple[Optional[EvidenceSpan], list[Claim]]:
        """按事件 Schema 创建所有有原文依据的业务 Claim。

        每个模板只在其主 key_field 已抽取且正文存在时落库；缺字段保持在
        Event.missing_required 中供人工审核，绝不以标题或空来源臆造事实。
        """
        if not document.content.strip():
            return None, []

        schema = get_schema(event.event_type)
        claims: list[Claim] = []
        first_evidence: Optional[EvidenceSpan] = None
        if schema:
            for template in schema.claim_templates:
                if event.key_fields.get(template.key_field) in (None, "", {}, []):
                    continue
                source_text = self._source_text_for(template, event.key_fields)
                evidence = self._register_evidence(document, source_text)
                first_evidence = first_evidence or evidence
                normalized = self._business_normalized(document, event, template)
                claims.append(self._persist_claim(document, event, evidence, normalized))

        # 保留兼容路径：无法形成业务 Claim 的已披露事件仍可记录文档级事实；
        # 但只要正文为空就不会创建任何 Claim。
        if not claims:
            evidence = first_evidence or self._register_evidence(document)
            claims.append(self._persist_legacy_claim(document, event, evidence))
            first_evidence = evidence
        self._apply_conflicts(event, claims)
        return first_evidence or self._register_evidence(document), claims

    def _apply_conflicts(self, event: Event, new_claims: list[Claim]) -> None:
        """检测新旧 Claim 之间的关键冲突，更新状态并持久化冲突记录。"""
        if not new_claims:
            return
        new_ids = {c.id for c in new_claims}
        all_claims = self.repository.get_claims_for_event(event.id)
        normalized_cache: dict[str, Any] = {}

        def _normalized(claim: Claim) -> Any:
            if claim.id not in normalized_cache:
                normalized_cache[claim.id] = self.normalizer.normalize(
                    subject_text=claim.subject_text,
                    subject_entity_id=claim.subject_entity_id,
                    predicate=claim.predicate,
                    object_value=claim.object_value,
                    qualifiers=claim.qualifiers,
                    as_of=claim.as_of.isoformat(),
                )
            return normalized_cache[claim.id]

        conflicted_ids: set[str] = set()
        for left in all_claims:
            for right in all_claims:
                if left.id >= right.id:
                    continue
                if left.id not in new_ids and right.id not in new_ids:
                    continue
                conflict = self.conflict_detector.detect(
                    _normalized(left), _normalized(right), left.id, right.id
                )
                if conflict is None:
                    continue
                if conflict.severity != "critical":
                    continue
                conflicted_ids.add(left.id)
                conflicted_ids.add(right.id)
                conflict = ConflictRecord(
                    id=new_id("cfl"),
                    event_id=event.id,
                    conflict_type=conflict.conflict_type,
                    severity=conflict.severity,
                    status="open",
                    summary=conflict.summary,
                    claim_ids=conflict.claim_ids,
                )
                self.repository.save_conflict(conflict)
                task = ReviewTask(
                    id=new_id("rvt"),
                    object_type="claim_conflict",
                    object_id=conflict.id,
                    reason_code=f"CONFLICT_{conflict.conflict_type.upper()}",
                    allowed_decisions=["approve", "reject"],
                    status="pending",
                )
                self.repository.save_review_task(task)
                AutoReviewService(self.repository).attempt_conflict_review(task, conflict)

        for claim in all_claims:
            if claim.id in conflicted_ids and claim.status != "conflicted":
                self.repository.update_claim(
                    claim.__class__(
                        **{**claim.__dict__, "status": "conflicted", "confidence": 0.30}
                    )
                )

    def _register_evidence(
        self, document: Document, source_text: Optional[str] = None
    ) -> EvidenceSpan:
        revision = self.repository.get_latest_revision(document.id)
        if not revision:
            raise RuntimeError("DOCUMENT_REVISION_MISSING")

        block = self._block_for_source(document, source_text)
        if block is not None:
            excerpt = block.text[:EXCERPT_MAX_LENGTH]
            char_start = block.char_start
            char_end = block.char_start + len(excerpt)
            extraction_method = "parser"
            locator = {
                "type": "html",
                "block_id": block.block_id,
                "char_start": char_start,
                "char_end": char_end,
                "dom_path": block.dom_path,
            }
        else:
            excerpt = document.title[:EXCERPT_MAX_LENGTH]
            char_start = 0
            char_end = len(excerpt)
            extraction_method = "fallback"
            locator = {
                "type": "title",
                "block_id": "title-001",
                "char_start": char_start,
                "char_end": char_end,
                "dom_path": "title",
            }

        if document.content[char_start:char_end] != excerpt:
            raise RuntimeError("EVIDENCE_LOCATOR_INCONSISTENT")

        evidence = EvidenceSpan(
            id=new_id("evd"),
            document_id=document.id,
            revision_id=revision.id,
            locator=locator,
            excerpt=excerpt,
            excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            locator_type=locator["type"],
            extraction_method=extraction_method,
            extraction_version=PARSER_VERSION,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_evidence(evidence)
        return evidence

    def _first_block(self, document: Document):
        blocks = self.block_reader.parse(document.content)
        return blocks[0] if blocks else None

    def _block_for_source(self, document: Document, source_text: Optional[str]):
        blocks = self.block_reader.parse(document.content)
        if source_text:
            for block in blocks:
                if source_text in block.text:
                    return block
        return blocks[0] if blocks else None

    def _subject(self, document: Document, event: Event) -> tuple[str, Optional[str]]:
        primary_link = event.entity_links[0] if event.entity_links else None
        return (
            primary_link.market_code if primary_link else document.title,
            primary_link.entity_id if primary_link else None,
        )

    def _business_normalized(self, document: Document, event: Event, template: ClaimTemplate):
        fields = event.key_fields
        subject_text, subject_entity_id = self._subject(document, event)
        value = self._object_value(template, fields)
        return self.normalizer.normalize(
            subject_text=subject_text,
            subject_entity_id=subject_entity_id,
            predicate=template.predicate,
            object_value=value,
            qualifiers=self._qualifiers(event.event_type, fields, template.key_field),
            as_of=document.published_at.isoformat(),
        )

    def _object_value(self, template: ClaimTemplate, fields: dict[str, Any]) -> dict[str, Any]:
        raw = fields[template.key_field]
        if template.object_type == "range":
            return {
                "type": "range",
                "min": raw["min"],
                "max": raw["max"],
                "unit": raw.get("unit"),
                "currency": fields.get("currency"),
            }
        if template.object_type == "decimal":
            unit = "percent" if template.key_field == "ownership_ratio" else fields.get("unit")
            return {
                "type": "decimal",
                "value": raw,
                "unit": unit,
                "currency": fields.get("currency"),
            }
        return {"type": template.object_type, "value": raw}

    def _qualifiers(
        self, event_type: str, fields: dict[str, Any], key_field: str
    ) -> dict[str, Any]:
        common = {"event_type": event_type, "key_field": key_field}
        keys_by_event = {
            "earnings_guidance": ("period", "profit_metric"),
            "major_contract": ("counterparties", "duration"),
            "merger_acquisition": ("transaction_type", "stage", "valuation", "unit"),
            "shareholder_reduction": ("shareholder", "stage", "ownership_ratio", "shares"),
            "regulatory_penalty": ("authority", "subject", "reason"),
        }
        common.update(
            {
                key: fields[key]
                for key in keys_by_event.get(event_type, ())
                if key in fields and key != key_field
            }
        )
        if event_type == "earnings_guidance" and "change_rate" in fields:
            common["comparison"] = "year_over_year"
        return common

    def _source_text_for(self, template: ClaimTemplate, fields: dict[str, Any]) -> Optional[str]:
        raw = fields.get(template.key_field)
        if isinstance(raw, dict):
            # 用原文中可稳定出现的数值定位；完整区间不要求格式完全一致。
            return str(raw.get("min") or raw.get("max") or "") or None
        return str(raw) if raw is not None else None

    def _persist_legacy_claim(
        self, document: Document, event: Event, evidence: EvidenceSpan
    ) -> Claim:
        subject_text, subject_entity_id = self._subject(document, event)

        normalized = self.normalizer.normalize(
            subject_text=subject_text,
            subject_entity_id=subject_entity_id,
            predicate="document_discloses_event",
            object_value={"type": "string", "value": event.event_type},
            qualifiers={},
            as_of=document.published_at.isoformat(),
        )
        return self._persist_claim(document, event, evidence, normalized)

    def _persist_claim(
        self, document: Document, event: Event, evidence: EvidenceSpan, normalized
    ) -> Claim:
        fingerprint = self.fingerprinter.compute(normalized)

        # 同事件已存在同指纹 Claim 时复用（DD-40 §12：重复运行通过指纹复用对象），
        # 不再创建重复 Claim，只追加本次证据关系。
        existing = self.repository.find_claim_by_fingerprint(event.id, fingerprint)
        if existing is not None:
            self.repository.save_claim_evidence(
                ClaimEvidenceRelation(
                    claim_id=existing.id,
                    evidence_id=evidence.id,
                    stance="support",
                    source_independence_key=document.external_id
                    or document.canonical_url
                    or document.id,
                )
            )
            return existing

        record = EvidenceRecord(
            evidence_id=evidence.id,
            source_tier=document.source_tier,
            stance="support",
            source_independence_key=document.external_id or document.canonical_url or document.id,
        )
        decision = self.policy.decide(normalized, [record], has_critical_conflict=False)

        claim = Claim(
            id=new_id("clm"),
            event_id=event.id,
            subject_text=normalized.subject_text,
            predicate=normalized.predicate,
            object_value=normalized.object_value,
            status=decision.status,
            confidence=decision.confidence,
            evidence_ids=[evidence.id],
            as_of=document.ingested_at,
            subject_entity_id=normalized.subject_entity_id,
            qualifiers=normalized.qualifiers,
            fingerprint=fingerprint,
            policy_version=decision.policy_version,
        )
        self.repository.save_claim(claim)
        self.repository.save_claim_evidence(
            ClaimEvidenceRelation(
                claim_id=claim.id,
                evidence_id=evidence.id,
                stance="support",
                source_independence_key=record.source_independence_key,
            )
        )
        return claim
