"""Exercise the installed identity use cases without application dependencies."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


def test_installed_identity_registration_and_login(tmp_path):
    """Install only identity-core and run both use cases outside the checkout."""
    python = install_agent_packages(tmp_path, ("packages/identity-core",))
    assert (
        probe(
            python,
            '''
import asyncio
from importlib.metadata import distributions
from importlib.util import find_spec
from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock

import identity_core
from identity_core.contracts import Login, Registration
from identity_core.service import IdentityService

assert {package.metadata['Name'] for package in distributions()} == {'identity-core'}
assert Path(identity_core.__file__).resolve().is_relative_to(Path(sys.prefix))
for name in ('app', 'config', 'fastapi', 'asyncpg', 'bcrypt', 'jwt', 'pydantic',
             'sqlalchemy', 'alembic', 'fastmcp', 'mcp', 'ledger_core', 'agent_core'):
    assert find_spec(name) is None, name

async def account_flow():
    """Use only supplied password, token and account capabilities."""
    store = Mock()
    store.get_user_by_username = AsyncMock(return_value=None)
    store.create_user = AsyncMock(return_value=42)
    store.update_last_login = AsyncMock()
    passwords = Mock()
    passwords.hash.return_value = 'saved-hash'
    passwords.verify.return_value = True
    tokens = Mock()
    tokens.issue.return_value = 'access-token'
    identity = IdentityService(store, passwords, tokens)
    created = await identity.register(Registration('alice', 'alice@example.com', 'password'))
    assert (created.user_id, created.username, created.access_token) == (42, 'alice', 'access-token')
    store.get_user_by_username.return_value = {
        'user_id': 42, 'username': 'alice', 'email': 'alice@example.com', 'password_hash': 'saved-hash',
    }
    logged_in = await identity.login(Login('alice', 'password'))
    assert logged_in == created
    store.update_last_login.assert_awaited_once_with(42)

asyncio.run(account_flow())
print('isolated identity use cases verified')
''',
        )
        == "isolated identity use cases verified"
    )
