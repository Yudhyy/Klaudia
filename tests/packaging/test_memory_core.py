"""Exercise installed memory contracts and reads without application code."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


def test_installed_memory_reads_preserve_owner_and_exact_policy(tmp_path):
    """Read an owned policy through the wheel with only Pydantic installed."""
    python = install_agent_packages(tmp_path, ("packages/memory-core",))
    output = probe(
        python,
        """
import asyncio
from importlib.util import find_spec
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import memory_core
from memory_core.contracts import DocumentEdit, DocumentPath, MemoryDocument
from memory_core.ports import DocumentReader
from memory_core.tools import MemoryReader

assert Path(memory_core.__file__).resolve().is_relative_to(Path(sys.prefix))
for name in ('app', 'config', 'agent_core', 'workflow_core', 'klaudia',
             'ledger_core', 'ledger_postgres', 'langchain_core', 'fastapi',
             'asyncpg', 'sqlalchemy', 'alembic', 'fastmcp', 'redis', 'taskiq'):
    assert find_spec(name) is None, name

policy = {
    'entity': 'Example Ltd',
    'jurisdiction': 'Example',
    'effective_from': '2026-01-01',
    'effective_until': '2026-12-31',
    'unit': 'USD',
    'numeric_text': 'decimal',
    'null_amounts': 'reject',
    'null_keys': 'reject',
    'tolerance': '0.00000000000000000000000000000001',
}
edit = DocumentEdit(expected_revision=0, content='Approved policy', policy=policy)
path = DocumentPath.ACCOUNTING_POLICY
store = AsyncMock(spec=DocumentReader)
store.read.return_value = MemoryDocument(
    path=path, revision=1, status='active', content=edit.content,
    policy=edit.policy, actor_id=42,
)
evidence = asyncio.run(MemoryReader(store, 42).read(path.value))
store.read.assert_awaited_once_with(42, path)
assert evidence == {
    'authority': 'untrusted_context',
    'accounting_validation': 'not_run',
    'document': {
        'path': path.value, 'revision': 1, 'status': 'active',
        'content': 'Approved policy', 'policy': {**policy, 'tolerance': '1E-32'},
        'actor_id': 42, 'updated_at': None, 'source_note': '',
    },
}
print('isolated memory policy read verified')
""",
    )
    assert output == "isolated memory policy read verified"
