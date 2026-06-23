import re

from app.core.exceptions import NormalizationError
from app.etl.deduplicator import compute_hash
from app.models.db import ContractType, JobSource, Seniority
from app.models.schemas import JobCreate
from app.scrapers.base import RawJob

# ── Contract type ─────────────────────────────────────────────────────────────

_CONTRACT_MAP: dict[str, ContractType] = {
    r"\bclt\b": ContractType.clt,
    r"\bpj\b|\bpessoa\s+jur[íi]dica\b": ContractType.pj,
    r"\bfreela(?:nce|ncer)?\b": ContractType.freelance,
    r"\bintern(?:ship)?\b|\bestagi[áa]rio\b|\best[aá]gio\b": ContractType.internship,
}

# ── Seniority (matched against job TITLE only) ────────────────────────────────

_SENIORITY_MAP: dict[str, Seniority] = {
    r"\bjunior\b|\bjr\.?\b": Seniority.junior,
    r"\bpleno\b|\bmid(?:[- ]level)?\b": Seniority.mid,
    r"\bs[eê]nior\b|\bsr\.?\b": Seniority.senior,
    r"\blead\b|\bl[ií]der\b|\bstaff\b": Seniority.lead,
}

# ── Salary patterns ───────────────────────────────────────────────────────────

# "5k-8k" or "5k–8k" or "5k a 8k"
_SALARY_K_RANGE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*k\s*[-–a]\s*(\d+(?:[.,]\d+)?)\s*k",
    re.IGNORECASE,
)

# "até R$ 10.000" or "até 10.000" — upper bound only
_SALARY_ATE = re.compile(
    r"at[eé]\s+(?:R\$\s*)?([\d.,]+)",
    re.IGNORECASE,
)

# "R$ 5.000 - R$ 8.000" or "R$ 5.000 a R$ 8.000" or "R$ 5.000"
_SALARY_BRL = re.compile(
    r"R\$\s*([\d.,]+)\s*(?:[-–a]\s*(?:R\$\s*)?([\d.,]+))?",
    re.IGNORECASE,
)

# ── Location normalisation ────────────────────────────────────────────────────

_CITY_PARTICLES = frozenset({"de", "do", "da", "dos", "das", "e", "na", "no", "em", "para"})


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detect[T](text: str, mapping: dict[str, T]) -> T | None:
    lower = text.lower()
    for pattern, value in mapping.items():
        if re.search(pattern, lower):
            return value
    return None


def _k_to_int(s: str) -> int:
    """'5,5' or '5.5' → 5500;  '8' → 8000."""
    return int(float(s.replace(",", ".")) * 1000)


def _brl_to_int(s: str) -> int:
    """'5.000' or '5,000' or '5000' → 5000.  Drops cents."""
    # Remove thousands separators: "5.000" → "5000", "5,000" → "5000"
    s = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", s)
    s = re.sub(r"[.,]\d+$", "", s)  # drop decimal part
    return int(re.sub(r"\D", "", s))


def _parse_salary(raw: str | None) -> tuple[int | None, int | None]:
    if not raw:
        return None, None

    m = _SALARY_K_RANGE.search(raw)
    if m:
        return _k_to_int(m.group(1)), _k_to_int(m.group(2))

    m = _SALARY_ATE.search(raw)
    if m:
        return None, _brl_to_int(m.group(1))

    m = _SALARY_BRL.search(raw)
    if m:
        lo = _brl_to_int(m.group(1))
        hi = _brl_to_int(m.group(2)) if m.group(2) else None
        return lo, hi

    return None, None


def _normalize_city(name: str | None) -> str | None:
    if not name:
        return None
    words = name.strip().split()
    result: list[str] = []
    for i, word in enumerate(words):
        low = word.lower()
        if len(word) == 2 and word.isalpha() and not low.startswith("de"):
            # Keep two-letter tokens (state abbreviations, "RJ", "SP") uppercase
            result.append(word.upper())
        elif i > 0 and low in _CITY_PARTICLES:
            result.append(low)
        else:
            result.append(word.capitalize())
    return " ".join(result) if result else None


def _normalize_state(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip()
    return name.upper() if len(name) == 2 and name.isalpha() else name.title()


def _resolve_location(city: str | None, state: str | None) -> tuple[str | None, str | None]:
    """Split 'São Paulo, SP' into city/state when state is absent from the source."""
    if state is not None or city is None or "," not in city:
        return _normalize_city(city), _normalize_state(state)
    parts = [p.strip() for p in city.split(",", 1)]
    return _normalize_city(parts[0] or None), _normalize_state(parts[1] or None)


# ── Public API ────────────────────────────────────────────────────────────────


def normalize(raw: RawJob) -> JobCreate:
    if not raw.title or not raw.url:
        raise NormalizationError(f"Missing required fields in raw job from {raw.source!r}")

    try:
        source = JobSource(raw.source)
    except ValueError as exc:
        raise NormalizationError(f"Unknown source: {raw.source!r}") from exc

    # Seniority detected from TITLE only; contract from title + raw contract field
    seniority = _detect(raw.title, _SENIORITY_MAP) or Seniority.unknown
    contract_src = f"{raw.title} {raw.contract_type_raw or ''}"
    contract_type = _detect(contract_src, _CONTRACT_MAP) or ContractType.unknown

    salary_min, salary_max = _parse_salary(raw.salary_raw)
    city, state = _resolve_location(raw.city, raw.state)

    return JobCreate(
        external_id=raw.external_id,
        source=source,
        title=raw.title.strip(),
        company=raw.company.strip(),
        city=city,
        state=state,
        remote=raw.remote,
        contract_type=contract_type,
        seniority=seniority,
        salary_min=salary_min,
        salary_max=salary_max,
        raw_description=raw.description,
        url=str(raw.url),
        content_hash=compute_hash(raw.title, raw.company, city),
    )
