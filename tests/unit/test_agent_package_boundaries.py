"""Protect canonical agent packages and legacy import identity."""

from importlib import import_module
from pathlib import Path
import tomllib

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("directory", "dependencies"),
    [
        ("packages/agent-core", {"agent-skills", "langchain-core", "pydantic"}),
        ("packages/agent-skills", {"pydantic"}),
        ("klaudia", {"agent-core", "agent-skills"}),
    ],
)
def test_agent_packages_declare_only_consumed_dependencies(directory, dependencies):
    """Keep provider and application dependencies outside the agent packages."""
    manifest = tomllib.loads((REPO_ROOT / directory / "pyproject.toml").read_text())
    declared = {
        requirement.split(">=")[0]
        for requirement in manifest["project"]["dependencies"]
    }
    assert declared == dependencies


@pytest.mark.parametrize(
    "module",
    [
        "agent",
        "authoring",
        "checkpoints",
        "context",
        "documents",
        "evidence",
        "memory",
        "prompt",
        "reconciliation",
        "tools",
        "writes",
        *[
            f"ports.{name}"
            for name in (
                "append",
                "authoring",
                "calculations",
                "catalogue",
                "documents",
                "errors",
                "financial",
                "memory",
                "operations",
                "reconciliation",
                "typed",
            )
        ],
    ],
)
def test_legacy_runtime_exports_share_canonical_definitions(module):
    """Keep legacy callers on the same classes, errors and helper functions."""
    legacy = import_module(f"klaudia.core.agent.{module}")
    canonical = import_module(f"agent_core.agent.{module}")
    exports = {
        name: value for name, value in vars(legacy).items() if not name.startswith("_")
    }
    assert exports
    assert all(value is getattr(canonical, name) for name, value in exports.items())


def test_legacy_skill_registry_uses_canonical_definitions():
    """Load one skill registry through both supported import paths."""
    legacy = import_module("klaudia.core.skills.registry")
    canonical = import_module("agent_skills.registry")
    assert legacy.SkillRegistry is canonical.SkillRegistry


def test_provider_factory_belongs_to_application():
    """Load provider construction through its application adapter."""
    factory = import_module("app.adapters.llm.model_factory")
    assert factory.build_chat_llm.__module__ == "app.adapters.llm.model_factory"
