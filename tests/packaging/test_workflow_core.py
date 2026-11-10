"""Run installed task coordination without application or infrastructure packages."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


def test_installed_workflow_uses_only_supplied_capabilities(tmp_path):
    """Verify stored identity, recovery and cancellation outside the checkout."""
    python = install_agent_packages(tmp_path, ("packages/workflow-core",))
    output = probe(
        python,
        '''
import asyncio
from contextlib import asynccontextmanager
from importlib import import_module
from importlib.metadata import distributions
from importlib.util import find_spec
from pathlib import Path
from pkgutil import walk_packages
import sys
from unittest.mock import AsyncMock, Mock

import workflow_core
from workflow_core.ports import TaskBusyError, TaskConflictError
from workflow_core.service import TaskCompletion, TaskRun, WorkflowService

assert {package.metadata['Name'] for package in distributions()} == {'workflow-core'}
assert Path(workflow_core.__file__).resolve().is_relative_to(Path(sys.prefix))
for module in walk_packages(workflow_core.__path__, 'workflow_core.'):
    import_module(module.name)
for name in ('app', 'config', 'klaudia', 'fastapi', 'asyncpg', 'pydantic',
             'sqlalchemy', 'alembic', 'fastmcp', 'mcp', 'ledger', 'ledger_core',
             'ledger_postgres', 'agent_core', 'agent_skills', 'langchain_core',
             'redis', 'taskiq'):
    assert find_spec(name) is None, name

class StoredTasks:
    """Supply the original session under an exclusive execution scope."""

    def __init__(self):
        """Keep opaque stored input and track execution scope."""
        self.session = Mock()
        self.session.load = AsyncMock(return_value={'operation_ref': 'prepared:original'})
        self.session.save = AsyncMock()
        self.session.set_status = AsyncMock()
        self.create = AsyncMock(return_value='task:original')
        self.held = False
        self.opened = []

    @asynccontextmanager
    async def open(self, user_id, task_id):
        """Release exclusive access after success, failure or cancellation."""
        self.opened.append((user_id, task_id))
        self.held = True
        try:
            yield self.session
        finally:
            self.held = False

async def task_flow():
    """Run persisted input and retain the application's original receipt."""
    tasks = StoredTasks()
    receipt = {'operation_id': 'original', 'status': 'committed'}
    observed = {'receipts': [receipt]}

    async def run():
        """Read the original continuation while exclusive access is held."""
        assert tasks.held
        assert await tasks.session.load() == {'operation_ref': 'prepared:original'}
        return TaskCompletion('awaiting_approval', observed)

    recover = AsyncMock(return_value=observed)
    prepared = TaskRun(run, recover)
    bind = Mock(return_value=prepared)
    workflow = WorkflowService(tasks, bind)
    payload = {'request_key': 'original-key', 'text': 'Retry the saved task'}
    assert await workflow.start((42, 7), payload) is observed
    tasks.create.assert_awaited_once_with((42, 7), payload)
    assert tasks.opened == [(42, 'task:original')]
    bind.assert_called_once_with(tasks.session)
    tasks.session.set_status.assert_awaited_once_with('awaiting_approval')
    recover.assert_not_awaited()
    assert not tasks.held

    tasks.session.set_status.reset_mock()
    failed_run = AsyncMock(side_effect=ConnectionError('Lost reply'))

    async def recover_failure():
        """Read original evidence after storage records the failure."""
        assert tasks.held
        tasks.session.set_status.assert_awaited_once_with('failed')
        return observed

    failed = WorkflowService(tasks, lambda _: TaskRun(failed_run, recover_failure))
    assert await failed.resume(42, 'task:original') is observed
    assert not tasks.held
    assert tasks.create.await_count == 1

    tasks.session.set_status.reset_mock()
    cancellation = asyncio.CancelledError('Caller stopped the run')
    cancelled_run = AsyncMock(side_effect=cancellation)
    cancelled = WorkflowService(tasks, lambda _: TaskRun(cancelled_run, recover))
    try:
        await cancelled.resume(42, 'task:original')
    except asyncio.CancelledError as caught:
        assert caught is cancellation
    else:
        raise AssertionError('Cancellation did not reach the caller')
    tasks.session.set_status.assert_awaited_once_with('interrupted')
    recover.assert_not_awaited()
    assert not tasks.held

    for rejected in (TaskBusyError('Task held'), TaskConflictError('Changed retry')):
        tasks.create.side_effect = rejected
        bind.reset_mock()
        try:
            await workflow.start((42, 7), payload)
        except type(rejected) as caught:
            assert caught is rejected
        else:
            raise AssertionError('Storage rejection did not reach the caller')
        bind.assert_not_called()

asyncio.run(task_flow())
print('isolated workflow capabilities verified')
''',
    )
    assert output == "isolated workflow capabilities verified"
