"""
Tests for app.etl.enricher.JobEnricher.extract_technologies.

extract_technologies is pure regex — no spaCy model is loaded, no DB needed.
"""

import pytest

from app.etl.enricher import JobEnricher


@pytest.fixture(scope="module")
def enricher() -> JobEnricher:
    return JobEnricher()


# ── Single technology ─────────────────────────────────────────────────────────


def test_extract_python_from_description(enricher: JobEnricher) -> None:
    techs = enricher.extract_technologies(
        "Buscamos desenvolvedor Python com experiência em Django."
    )
    assert "Python" in techs
    assert "Django" in techs


def test_extract_javascript(enricher: JobEnricher) -> None:
    techs = enricher.extract_technologies("Frontend em JavaScript e React.")
    assert "JavaScript" in techs
    assert "React" in techs


def test_extract_typescript(enricher: JobEnricher) -> None:
    assert "TypeScript" in enricher.extract_technologies("Projeto em TypeScript e Node.js.")


def test_extract_docker_kubernetes(enricher: JobEnricher) -> None:
    techs = enricher.extract_technologies("Usamos Docker e Kubernetes para orquestração.")
    assert "Docker" in techs
    assert "Kubernetes" in techs


def test_extract_aws(enricher: JobEnricher) -> None:
    assert "AWS" in enricher.extract_technologies("Infraestrutura AWS com Terraform.")


def test_extract_postgresql(enricher: JobEnricher) -> None:
    techs = enricher.extract_technologies("Banco de dados PostgreSQL e Redis.")
    assert "PostgreSQL" in techs
    assert "Redis" in techs


# ── Multiple technologies ─────────────────────────────────────────────────────


def test_extract_multiple_technologies(enricher: JobEnricher) -> None:
    description = (
        "Desenvolvedor Full Stack com Python (FastAPI/Django), "
        "React, TypeScript, PostgreSQL, Redis, Docker e AWS."
    )
    techs = enricher.extract_technologies(description)
    expected = {
        "Python",
        "FastAPI",
        "Django",
        "React",
        "TypeScript",
        "PostgreSQL",
        "Redis",
        "Docker",
        "AWS",
    }
    assert expected.issubset(set(techs))


def test_extract_returns_list(enricher: JobEnricher) -> None:
    result = enricher.extract_technologies("Python e Docker.")
    assert isinstance(result, list)


def test_extract_deduplicates(enricher: JobEnricher) -> None:
    """Same technology mentioned multiple times should appear once."""
    techs = enricher.extract_technologies("Python Python Python")
    assert techs.count("Python") == 1


def test_extract_sorted_by_frequency(enricher: JobEnricher) -> None:
    """Technologies with more mentions come first."""
    # Python mentioned 3x, Docker once
    description = "Python Python Python e Docker."
    techs = enricher.extract_technologies(description)
    assert techs[0] == "Python"


# ── Empty / no match ─────────────────────────────────────────────────────────


def test_no_technologies_returns_empty(enricher: JobEnricher) -> None:
    assert enricher.extract_technologies("Boa comunicação e trabalho em equipe.") == []


def test_empty_string_returns_empty(enricher: JobEnricher) -> None:
    assert enricher.extract_technologies("") == []


def test_none_like_empty(enricher: JobEnricher) -> None:
    # Passing an empty description (as the enricher receives when raw_description is None)
    assert enricher.extract_technologies("") == []


# ── Case-insensitivity ────────────────────────────────────────────────────────


def test_extract_case_insensitive(enricher: JobEnricher) -> None:
    techs_lower = enricher.extract_technologies("python e docker")
    techs_upper = enricher.extract_technologies("PYTHON e DOCKER")
    assert set(techs_lower) == set(techs_upper)


def test_canonical_name_preserved(enricher: JobEnricher) -> None:
    """Canonical names (e.g. 'Node.js') are returned regardless of input case."""
    techs = enricher.extract_technologies("node.js project")
    assert "Node.js" in techs
