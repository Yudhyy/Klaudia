"""Exercise standard Alembic revision authoring for both owned histories."""

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import shutil

from alembic import command
from alembic.config import Config
import pytest

from app.adapters.persistence.migrations import HISTORIES

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("history_name", ["application", "ledger"])
def test_standard_revision_generation_uses_timestamp_and_header(history_name, tmp_path):
    """Generate a linked revision through the checked-in configuration/template."""
    history = HISTORIES[history_name]
    directory = tmp_path / history_name
    shutil.copytree(history.directory, directory)
    configuration = Config(str(ROOT / "alembic.ini"), ini_section=history_name)
    configuration.set_main_option("script_location", str(directory))
    generated = command.revision(
        configuration, message="add example", rev_id="example_0002"
    )
    header = ast.get_docstring(ast.parse(Path(generated.path).read_text()))
    fields = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
    created = datetime.fromisoformat(fields["Create Date"])
    assert created.utcoffset() == timezone.utc.utcoffset(created)
    assert (
        Path(generated.path).name
        == created.strftime("%Y-%m-%d-%H%M") + "_add_example.py"
    )
    assert generated.revision == fields["Revision ID"] == "example_0002"
    assert generated.down_revision == fields["Revises"] == history.revision
    assert generated.module.upgrade.__annotations__["return"] is None


@pytest.mark.parametrize("history_name", ["application", "ledger"])
def test_runtime_history_ignores_authoring_configuration(
    history_name, tmp_path, monkeypatch
):
    """Read installed revisions without loading a local or bundled config file."""
    history = HISTORIES[history_name]
    directory = tmp_path / history_name
    shutil.copytree(history.directory, directory)
    (directory / "alembic.ini").write_text("[invalid configuration")
    (tmp_path / "alembic.ini").write_text("[invalid configuration")
    monkeypatch.chdir(tmp_path)
    installed_history = replace(history, directory=directory)
    assert installed_history.scripts().get_heads() == [history.revision]


@pytest.mark.parametrize("history_name", ["application", "ledger"])
def test_baseline_uses_the_reference_header_and_filename(history_name):
    """Keep stable baseline IDs while using the preferred contributor conventions."""
    history = HISTORIES[history_name]
    baseline = history.scripts().get_revision(history.revision)
    header = ast.get_docstring(ast.parse(Path(baseline.path).read_text()))
    assert f"Revision ID: {history.revision}" in header
    assert "Revises:" in header
    created = datetime.fromisoformat(header.split("Create Date: ", 1)[1].strip())
    assert Path(baseline.path).name.startswith(created.strftime("%Y-%m-%d-%H%M") + "_")
    assert baseline.down_revision is None
