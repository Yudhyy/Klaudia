"""Keep supported workflow imports bound to the installed canonical definitions."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("ports", "TaskBusyError"),
        ("ports", "TaskConflictError"),
        ("ports", "TaskSession"),
        ("ports", "TaskStore"),
        ("ports", "TaskReader"),
        ("ports", "OperationDecisions"),
        ("service", "TaskCompletion"),
        ("service", "TaskRun"),
        ("service", "WorkflowService"),
    ],
)
def test_supported_workflow_imports_share_one_definition(module, name):
    """Legacy exceptions, interfaces and services retain their object identity."""
    canonical = getattr(import_module(f"workflow_core.{module}"), name)
    legacy = getattr(import_module(f"app.services.workflow.{module}"), name)
    assert legacy is canonical


def test_main_chat_uses_the_canonical_workflow_service():
    """The production chat path runs the installed lifecycle implementation."""
    from app.services.core import main_chat
    from workflow_core.service import WorkflowService

    assert main_chat.WorkflowService is WorkflowService
