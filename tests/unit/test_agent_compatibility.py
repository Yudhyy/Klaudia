"""Freeze model tools, prompt bytes, skills and saved runtime identities."""

from dataclasses import asdict
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from klaudia.core.agent.agent import MainAgent, RunLimits
from klaudia.core.agent.context import TaskContext
from klaudia.core.skills.registry import SkillRegistry
from tests.agent_compatibility import (
    FIXTURES,
    PROFILES,
    agent_options,
    read_fixture,
    tool_contracts,
)
from tests.unit.test_agent_checkpoints import MemoryCheckpoint
from tests.unit.test_main_agent import ScriptedModel


@pytest.mark.parametrize("profile", PROFILES)
async def test_complete_runtime_contract_matches_release(profile: str) -> None:
    """Preserve tool order, schemas, descriptions, prompt and checkpoint hash."""
    release = read_fixture("runtime.json")
    saved = release["profiles"][profile]
    context = TaskContext.model_validate(release["identity_inputs"]["context"])
    model = ScriptedModel([AIMessage(content="Ready.")])
    checkpoint = MemoryCheckpoint()
    await MainAgent(
        model, AsyncMock(), **agent_options(PROFILES[profile], context)
    ).run(release["identity_inputs"]["message"], context, checkpoint=checkpoint)

    contracts = tool_contracts(model.tools)
    assert saved["capabilities"] == PROFILES[profile]
    assert list(contracts) == saved["tools"]
    assert contracts == {name: release["tools"][name] for name in saved["tools"]}
    assert (
        model.inputs[0][0].content.encode("utf-8")
        == (FIXTURES / saved["prompt"]).read_bytes()
    )
    assert RunLimits().model_dump() == release["identity_inputs"]["limits"]
    assert checkpoint.state["identity"] == saved["identity"]


def test_capability_profile_inventory_is_complete() -> None:
    """Fail if a saved optional combination is removed or never exercised."""
    assert set(read_fixture("runtime.json")["profiles"]) == set(PROFILES)


def test_skill_metadata_and_exact_content_bytes_match_release() -> None:
    """Keep the ordered skill registry and installed procedure bytes unchanged."""
    registry = SkillRegistry()
    saved = read_fixture("skills.json")
    assert [asdict(skill) for skill in registry.descriptions] == saved
    for skill in saved:
        name = skill["name"]
        content = (FIXTURES / "skills" / f"{name}.md").read_bytes()
        assert (
            files("klaudia.core.skills").joinpath(f"{name}.md").read_bytes() == content
        )
        assert registry.load(name) == {
            "name": name,
            "version": skill["version"],
            "content": content.decode("utf-8"),
        }
