"""The application starts with one agent and only its retained services."""

from app.services.core.container import KlaudiaContainer
from app.services.workflow.approvals import CheckedApprovals
from config.settings import Settings
from tests.integration.postgres import POSTGRES_TEST_URL


async def test_single_agent_startup_connects_retained_services():
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
    )
    container = await KlaudiaContainer.create(settings)
    try:
        assert container.main_chat is not None
        assert container.tasks is not None
        assert container.memory_documents is not None
        assert isinstance(container.approvals, CheckedApprovals)
        assert container.mcp_ledger.tools
        assert not hasattr(container, "supervisor")
        assert not hasattr(container, "mcp_archive")
        assert not hasattr(container, "memory")
    finally:
        await container.shutdown()
