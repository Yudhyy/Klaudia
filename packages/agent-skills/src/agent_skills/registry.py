"""Allowlisted packaged procedures with stable summaries and content versions."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

MAX_SKILL_BYTES = 8192


@dataclass(frozen=True)
class SkillDescription:
    """Stable registry metadata; procedure bodies stay outside the initial prompt."""

    name: str
    version: str
    description: str


_SKILLS = (
    SkillDescription(
        "policy-reconciliation",
        "3",
        "Reconcile owned tables under saved structured accounting policy, with checked entity, dates, unit columns and revision-bound evidence.",
    ),
    SkillDescription(
        "decimal-formulas",
        "4",
        "Declare typed inputs and native exact-decimal formulas, inspect same-workbook dependencies, choose explicit financial rounding and verify recalculation receipts.",
    ),
    SkillDescription(
        "financial-execution",
        "2",
        "Read bounded records, sort, look up, join, age balances and calculate variance with explicit numeric policies and labelled evidence.",
    ),
    SkillDescription(
        "table-authoring",
        "1",
        "Create or register table regions, maintain names and column identities, refresh metadata and request approval to unregister tables when authoring tools are enabled.",
    ),
    SkillDescription(
        "table-append",
        "2",
        "Prepare and execute complete literal records when optional append tools are enabled; retry stored references and report committed receipts.",
    ),
    SkillDescription(
        "table-calculation",
        "2",
        "Calculate exact sums and counts from inspected tables with explicit units and source revisions.",
    ),
    SkillDescription(
        "resource-discovery",
        "2",
        "Find tables from business intent and resolve ambiguity using evidence.",
    ),
    SkillDescription(
        "schema-inspection",
        "1",
        "Inspect registered schemas, paginate columns and interpret freshness.",
    ),
)


class LoadSkill(BaseModel):
    """Select a packaged skill by registry name, never by an arbitrary path."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=80)


class SkillRegistry:
    """Expose brief descriptions first and load only explicitly requested procedures."""

    @property
    def descriptions(self) -> tuple[SkillDescription, ...]:
        """Return the stable list of supported procedural capabilities.

        Returns:
            Immutable names, versions and short descriptions.
        """
        return _SKILLS

    def load(self, name: str) -> dict[str, str]:
        """Read an allowlisted procedure from the installed package.

        Args:
            name: Exact registry key from the available descriptions.

        Returns:
            Versioned procedural text.

        Raises:
            ValueError: The name is unknown or the packaged content exceeds its budget.
            OSError: The packaged procedure is missing or unreadable.
        """
        skill = next((skill for skill in _SKILLS if skill.name == name), None)
        if skill is None:
            raise ValueError("Unknown skill; use a name from the skill registry")
        content = (
            Path(__file__).with_name(f"{skill.name}.md").read_text(encoding="utf-8")
        )
        if len(content.encode("utf-8")) > MAX_SKILL_BYTES:
            raise ValueError("Skill exceeds the procedural context budget")
        return {"name": skill.name, "version": skill.version, "content": content}
