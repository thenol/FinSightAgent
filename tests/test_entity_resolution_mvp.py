from datetime import datetime, timezone

from app.events.entities import EntityResolver
from app.events.reference_data import (
    DeterministicReferenceDataProvider,
    ReferenceSecurity,
    TemporalIdentifier,
)
from app.platform.repository import InMemoryRepository

UTC = timezone.utc
PAST = datetime(2019, 6, 1, tzinfo=UTC)
CURRENT = datetime(2026, 6, 1, tzinfo=UTC)


def _record(
    *,
    entity_id: str = "ent_pingan",
    security_id: str = "sec_pingan",
    market_code: str = "000001.SZ",
    canonical_name: str = "平安银行股份有限公司",
    full_name: str = "平安银行股份有限公司",
    short_names: tuple[str, ...] = ("平安银行",),
) -> ReferenceSecurity:
    return ReferenceSecurity(
        entity_id=entity_id,
        security_id=security_id,
        market_code=market_code,
        canonical_name=canonical_name,
        full_name=full_name,
        short_names=short_names,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        historical_codes=(
            TemporalIdentifier(
                "000002.SZ",
                valid_from=datetime(2010, 1, 1, tzinfo=UTC),
                valid_to=datetime(2020, 1, 1, tzinfo=UTC),
            ),
        ),
        historical_names=(
            TemporalIdentifier(
                "深圳发展银行股份有限公司",
                valid_from=datetime(2010, 1, 1, tzinfo=UTC),
                valid_to=datetime(2020, 1, 1, tzinfo=UTC),
            ),
        ),
    )


def _resolver(*records: ReferenceSecurity) -> tuple[EntityResolver, InMemoryRepository]:
    repository = InMemoryRepository()
    provider = DeterministicReferenceDataProvider(records, version="cn-security-master-v1")
    return EntityResolver(repository, provider), repository


def test_versioned_provider_resolves_code_to_stable_ids() -> None:
    resolver, repository = _resolver(_record())

    result = resolver.resolve("平安银行（000001.SZ）公告", "doc_1", as_of=CURRENT)[0]

    assert result.entity_id == "ent_pingan"
    assert result.security_id == "sec_pingan"
    assert result.resolution_method == "code_exact"
    assert result.confidence == 1.0
    assert repository.get_security_by_market_code("000001.SZ").id == "sec_pingan"


def test_full_and_short_names_have_deterministic_methods_and_confidence() -> None:
    resolver, _ = _resolver(_record())

    full = resolver.resolve("平安银行股份有限公司发布公告", "doc_full", as_of=CURRENT)
    short = resolver.resolve("平安银行发布公告", "doc_short", as_of=CURRENT)

    assert (full[0].resolution_method, full[0].confidence) == ("name_full", 0.90)
    assert (short[0].resolution_method, short[0].confidence) == ("name_short", 0.75)
    assert full[0].entity_id == short[0].entity_id == "ent_pingan"


def test_historical_code_and_name_resolve_only_inside_validity_interval() -> None:
    resolver, _ = _resolver(_record())

    code = resolver.resolve("000002.SZ 发布公告", "doc_code", as_of=PAST)
    name = resolver.resolve("深圳发展银行股份有限公司发布公告", "doc_name", as_of=PAST)

    assert code[0].entity_id == name[0].entity_id == "ent_pingan"
    assert code[0].security_id == name[0].security_id == "sec_pingan"
    assert code[0].resolution_method == "historical_code"
    assert name[0].resolution_method == "historical_name"


def test_as_of_does_not_leak_future_reference_mapping() -> None:
    resolver, repository = _resolver(_record())
    before_mapping = datetime(2009, 12, 31, tzinfo=UTC)

    code = resolver.resolve("000002.SZ 发布公告", "doc_code", as_of=before_mapping)
    name = resolver.resolve("深圳发展银行股份有限公司发布公告", "doc_name", as_of=before_mapping)

    assert code[0].entity_id is None
    assert code[0].resolution_method == "unresolved"
    assert name == []
    assert repository.entities == {}
    assert repository.securities == {}


def test_same_name_with_multiple_candidates_is_ambiguous_without_guessing() -> None:
    first = _record(short_names=("华兴科技",))
    second = _record(
        entity_id="ent_huaxing_2",
        security_id="sec_huaxing_2",
        market_code="600001.SH",
        canonical_name="华兴科技集团股份有限公司",
        full_name="华兴科技集团股份有限公司",
        short_names=("华兴科技",),
    )
    resolver, repository = _resolver(first, second)

    result = resolver.resolve("华兴科技发布公告", "doc_ambiguous", as_of=CURRENT)

    assert len(result) == 1
    assert result[0].ambiguous
    assert result[0].entity_id is None
    assert result[0].security_id is None
    assert result[0].resolution_method == "ambiguous"
    assert repository.entities == {}
    assert resolver.to_links(result) == []
