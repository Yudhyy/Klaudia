"""The application starts with one agent and only its retained services."""

import asyncpg
import jwt
import pytest
from uuid import uuid4

from identity_core.contracts import Login, Registration
from app.services.core.container import KlaudiaContainer
from app.services.workflow.approvals import CheckedApprovals
from config.settings import Settings
from tests.integration.postgres import POSTGRES_TEST_URL


async def test_single_agent_startup_connects_retained_services(migrated_postgres):
    """Start real stores and ledger transport without calling a model provider."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=POSTGRES_TEST_URL,
        MODEL_PROVIDER="deepseek",
        LLM_MODEL="deepseek-flash",
        DEEPSEEK_API_KEY="startup-test-placeholder",
        LLM_API_KEY="startup-test-placeholder",
        GOOGLE_GENAI_USE_VERTEXAI=False,
        GUARDRAILS_ENABLED=False,
        LANGFUSE_ENABLED=False,
        MOCK_KIE=True,
        REDIS_URL="redis://localhost:6379/9",
        MINIO_BUCKET="klaudia-sandbox-blobs",
        JWT_SECRET="startup-identity-secret-with-at-least-32-bytes",
        JWT_EXPIRES_DAYS=3,
    )
    container = await KlaudiaContainer.create(settings)
    try:
        assert container.main_chat is not None
        assert container.tasks is not None
        assert container.memory_documents is not None
        assert container.identity_store is not None
        assert container.identity_service is not None
        assert await container.identity_store.get_user_by_username("dev") == (
            await container.db_client.get_user_by_username("dev")
        )
        username = "startup_" + uuid4().hex
        registered = await container.identity_service.register(
            Registration(username, f"{username}@example.com", "startup-test-password")
        )
        authenticated = await container.identity_service.login(
            Login(username, "startup-test-password")
        )
        claims = jwt.decode(
            authenticated.access_token, settings.jwt_secret, algorithms=["HS256"]
        )
        assert authenticated.user_id == registered.user_id
        assert claims["sub"] == str(registered.user_id)
        assert claims["exp"] - claims["iat"] == 3 * 24 * 60 * 60
        assert await container.db_client.fetchval(
            'SELECT last_login IS NOT NULL FROM "user" WHERE user_id = $1',
            (registered.user_id,),
        )
        assert isinstance(container.approvals, CheckedApprovals)
        assert container.mcp_ledger.tools
        assert not hasattr(container, "supervisor")
        assert not hasattr(container, "mcp_archive")
        assert not hasattr(container, "memory")
    finally:
        await container.shutdown()
    with pytest.raises(asyncpg.InterfaceError, match="pool is closed"):
        await container.identity_store.get_user_by_username("dev")
