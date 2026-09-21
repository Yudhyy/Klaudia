"""Procedural loading accepts package keys without granting filesystem access."""

import pytest

from klaudia.core.skills.registry import SkillRegistry


@pytest.mark.parametrize(
    "name", ["../../README.md", "/etc/passwd", "resource-discovery.md", "unknown"]
)
def test_registry_rejects_arbitrary_paths_and_unknown_keys(name):
    """Only the exact registry keys can load content from the package."""
    with pytest.raises(ValueError, match="Unknown skill"):
        SkillRegistry().load(name)


def test_packaged_procedures_match_registry_versions():
    """Every advertised skill ships a nonempty procedure under its declared version."""
    registry = SkillRegistry()
    for description in registry.descriptions:
        loaded = registry.load(description.name)
        assert loaded["version"] == description.version
        assert loaded["content"].startswith("Procedure:")
        assert "Procedure:" not in description.description


def test_append_procedure_versions_literal_value_preservation():
    """The packaged append contract includes complete-value and type checks."""
    procedure = SkillRegistry().load("table-append")
    assert procedure["version"] == "2"
    assert "Do not shorten, paraphrase, translate or normalise" in procedure["content"]
    assert "Compare every proposed field" in procedure["content"]
    assert (
        "not proof that the proposal matches the user's request" in procedure["content"]
    )


def test_formula_procedure_requires_named_workbook_evidence():
    """An active UI hint cannot stand in for a user's named destination."""
    procedure = SkillRegistry().load("decimal-formulas")
    assert procedure["version"] == "3"
    assert "workbook_name" in procedure["content"]
    assert "search_resources" in procedure["content"]
    assert "Never assume the active workbook" in procedure["content"]
    assert "formula removal is not supported" in procedure["content"]
    assert "dependent cells whose cached results changed" in procedure["content"]
