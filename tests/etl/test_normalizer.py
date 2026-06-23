"""
Tests for app.etl.normalizer.

All tests are pure-Python — no database connection required.
"""

import pytest

from app.core.exceptions import NormalizationError
from app.etl.deduplicator import compute_hash
from app.etl.normalizer import _normalize_city, _normalize_state, _parse_salary, normalize
from app.models.db import ContractType, JobSource, Seniority
from app.scrapers.base import RawJob

# ── Helpers ───────────────────────────────────────────────────────────────────


def _raw(**overrides: object) -> RawJob:
    defaults: dict[str, object] = {
        "source": "gupy",
        "external_id": "123",
        "title": "Desenvolvedor Python",
        "company": "ACME",
        "url": "https://example.com/job/1",
    }
    defaults.update(overrides)
    return RawJob(**defaults)  # type: ignore[arg-type]


# ── Contract type ─────────────────────────────────────────────────────────────


def test_contract_type_clt() -> None:
    assert normalize(_raw(title="Dev Python CLT")).contract_type == ContractType.clt


def test_contract_type_clt_from_raw_field() -> None:
    assert normalize(_raw(contract_type_raw="CLT")).contract_type == ContractType.clt


def test_contract_type_pj() -> None:
    assert normalize(_raw(title="Dev Python PJ")).contract_type == ContractType.pj


def test_contract_type_pj_pessoa_juridica() -> None:
    assert normalize(_raw(title="Dev pessoa jurídica")).contract_type == ContractType.pj


def test_contract_type_freelance() -> None:
    assert normalize(_raw(title="Dev Freelance")).contract_type == ContractType.freelance


def test_contract_type_freela_short() -> None:
    assert normalize(_raw(title="Dev Freela")).contract_type == ContractType.freelance


def test_contract_type_internship_estagio() -> None:
    assert normalize(_raw(title="Estagiário Dev")).contract_type == ContractType.internship


def test_contract_type_internship_english() -> None:
    assert normalize(_raw(title="Dev Intern")).contract_type == ContractType.internship


def test_contract_type_unknown() -> None:
    assert normalize(_raw(title="Dev Python")).contract_type == ContractType.unknown


# ── Seniority — detected from TITLE only ─────────────────────────────────────


def test_seniority_from_title_junior() -> None:
    assert normalize(_raw(title="Dev Python Junior")).seniority == Seniority.junior


def test_seniority_from_title_junior_jr() -> None:
    assert normalize(_raw(title="Dev Python Jr.")).seniority == Seniority.junior


def test_seniority_from_title_mid_pleno() -> None:
    assert normalize(_raw(title="Engenheiro Pleno")).seniority == Seniority.mid


def test_seniority_from_title_senior() -> None:
    assert normalize(_raw(title="Dev Python Sênior")).seniority == Seniority.senior


def test_seniority_from_title_senior_sr() -> None:
    assert normalize(_raw(title="Dev Python Sr.")).seniority == Seniority.senior


def test_seniority_from_title_lead() -> None:
    assert normalize(_raw(title="Tech Lead Python")).seniority == Seniority.lead


def test_seniority_from_title_staff() -> None:
    assert normalize(_raw(title="Staff Engineer")).seniority == Seniority.lead


def test_seniority_not_from_description() -> None:
    """'senior' only in description must NOT be detected (title-only rule)."""
    job = normalize(_raw(title="Engenheiro Python", description="buscamos perfil senior"))
    assert job.seniority == Seniority.unknown


def test_seniority_unknown_when_absent() -> None:
    assert normalize(_raw(title="Engenheiro de Software")).seniority == Seniority.unknown


# ── Salary parser ─────────────────────────────────────────────────────────────


def test_salary_parser_range() -> None:
    lo, hi = _parse_salary("R$ 8.000 - R$ 12.000")
    assert lo == 8_000
    assert hi == 12_000


def test_salary_parser_range_com_a() -> None:
    lo, hi = _parse_salary("R$ 5.000 a R$ 8.000")
    assert lo == 5_000
    assert hi == 8_000


def test_salary_parser_k_notation() -> None:
    lo, hi = _parse_salary("5k-8k")
    assert lo == 5_000
    assert hi == 8_000


def test_salary_parser_k_decimal() -> None:
    lo, hi = _parse_salary("5,5k–8k")
    assert lo == 5_500
    assert hi == 8_000


def test_salary_parser_ate() -> None:
    lo, hi = _parse_salary("até R$ 10.000")
    assert lo is None
    assert hi == 10_000


def test_salary_parser_single_value() -> None:
    lo, hi = _parse_salary("R$ 6.000")
    assert lo == 6_000
    assert hi is None


def test_salary_none() -> None:
    lo, hi = _parse_salary(None)
    assert lo is None
    assert hi is None


def test_salary_no_match() -> None:
    lo, hi = _parse_salary("a combinar")
    assert lo is None
    assert hi is None


# ── Location normalisation ────────────────────────────────────────────────────


def test_normalize_city_title_case() -> None:
    assert _normalize_city("são paulo") == "São Paulo"


def test_normalize_city_de_particle() -> None:
    assert _normalize_city("RIO DE JANEIRO") == "Rio de Janeiro"


def test_normalize_city_none() -> None:
    assert _normalize_city(None) is None


def test_normalize_state_uppercase() -> None:
    assert _normalize_state("sp") == "SP"
    assert _normalize_state("rj") == "RJ"


def test_normalize_state_none() -> None:
    assert _normalize_state(None) is None


def test_location_split_from_combined_city() -> None:
    job = normalize(_raw(city="São Paulo, SP"))
    assert job.city == "São Paulo"
    assert job.state == "SP"


def test_location_explicit_state_takes_precedence() -> None:
    job = normalize(_raw(city="São Paulo, SP", state="RJ"))
    assert job.state == "RJ"


# ── Source validation ─────────────────────────────────────────────────────────


def test_valid_source_maps_to_enum() -> None:
    assert normalize(_raw(source="gupy")).source == JobSource.gupy
    assert normalize(_raw(source="remoteok")).source == JobSource.remoteok


def test_unknown_source_raises() -> None:
    with pytest.raises(Exception, match="Unknown source"):
        normalize(_raw(source="xpto_board"))


def test_missing_title_raises() -> None:
    with pytest.raises(NormalizationError):
        normalize(_raw(title="", url=""))


# ── content_hash wiring ───────────────────────────────────────────────────────


def test_content_hash_is_16_chars() -> None:
    job = normalize(_raw())
    assert len(job.content_hash) == 16


def test_content_hash_matches_compute_hash_after_city_normalisation() -> None:
    raw = _raw(city="São Paulo, SP")
    job = normalize(raw)
    # city is normalised to "São Paulo" before hashing
    assert job.content_hash == compute_hash("Desenvolvedor Python", "ACME", "São Paulo")
