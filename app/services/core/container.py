import logging
import os
import sys
from pathlib import Path
from typing import Optional

from config.settings import Settings
from app.services.core.llm_client import LLMClient
from app.services.core.observability import LangfuseService
from app.services.extraction.infra.db_client import AppDBClient
from app.services.memory.store import MemoryDocumentStore
from app.services.extraction.infra.dedup_cache import DedupCache
from app.services.extraction.infra.kie_client import KIEClient
from app.services.extraction.infra.object_store import MinIOClient
from app.services.extraction.ingest import IngestService
from app.services.extraction.agents.base import ExtractionAgent
from app.services.core.approvals import ApprovalService
from app.services.core.memory import MemoryService
from app.services.core.spreadsheets import SpreadsheetService
from app.services.guardrails import GuardrailsAgent, GuardrailsConfig
from klaudia.core.supervisor.agent import SupervisorAgent
from klaudia.interfaces.tool_registry import MCPToolRegistry
from ledger.store import LedgerStore
from ledger.catalogue import CatalogueStore
from app.services.catalogue.service import CatalogueService
from app.services.catalogue.authoring import AuthoringService
from app.services.core.main_chat import MainChatService
from app.services.workflow.store import TaskStore
from app.services.workflow.approvals import CheckedApprovals
from app.services.core.operations import OperationService
from klaudia.core.supervisor.llm import build_chat_llm

logger = logging.getLogger(__name__)

# Project root resolves to the repo root regardless of where uvicorn is launched.
# container.py lives at app/services/core/container.py — three parents up.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _ensure_gcp_credentials(settings: Settings) -> None:
    """Resolve GOOGLE_APPLICATION_CREDENTIALS to an absolute path and export it.

    The Google SDKs (google-genai, google-auth used by langchain-google-vertexai)
    read this env var directly. A relative path breaks for MCP subprocesses that
    chdir into mcp-archive/ or mcp-gsheets/. Resolving once at startup keeps the
    file discoverable regardless of CWD and lets subprocesses inherit it.
    """
    raw = settings.google_application_credentials
    if not raw:
        return
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (_PROJECT_ROOT / raw).resolve()
    if not candidate.is_file():
        logger.warning(
            "GOOGLE_APPLICATION_CREDENTIALS=%s does not exist (resolved=%s)",
            raw,
            candidate,
        )
        return
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(candidate)
    logger.info("GOOGLE_APPLICATION_CREDENTIALS resolved to %s", candidate)


def _build_mcp_registries(
    settings: Settings,
) -> tuple[MCPToolRegistry, MCPToolRegistry]:
    """Construct archive and spreadsheet registries for the configured transport.

    stdio: spawn the server as a subprocess of FastAPI. No port, no SSE keep-alive
           required — the connection is a pipe owned by this process.
    http:  connect to stateless remote services with automatic protocol negotiation.
    sse:   legacy rollback mode; derive /sse endpoints from configured HTTP URLs.
    """
    transport = (settings.mcp_transport or "stdio").lower()

    if transport == "stdio":
        python_bin = sys.executable
        server_env = {**os.environ, "DATABASE_URL": settings.database_url}

        archive_registry = MCPToolRegistry.from_stdio(
            "mcp-archive",
            command=python_bin,
            args=["main.py", "--transport", "stdio"],
            cwd=str(_PROJECT_ROOT / "mcp-archive"),
            env=server_env,
        )
        if settings.sheets_backend == "ledger":
            sheets_reg = MCPToolRegistry.from_stdio(
                "mcp-ledger",
                command=python_bin,
                args=["main.py", "--transport", "stdio"],
                cwd=str(_PROJECT_ROOT / "mcp-ledger"),
                env=server_env,
            )
        else:
            sheets_reg = MCPToolRegistry.from_stdio(
                "mcp-gsheets",
                command=python_bin,
                args=["main.py", "--transport", "stdio"],
                cwd=str(_PROJECT_ROOT / "mcp-gsheets"),
                env=server_env,
            )
        return archive_registry, sheets_reg

    if transport in {"http", "sse"}:
        archive_url = settings.mcp_archive_url
        sheets_url = (
            settings.mcp_ledger_url
            if settings.sheets_backend == "ledger"
            else settings.mcp_gsheets_url
        )
        if transport == "sse":
            archive_url = _legacy_sse_url(archive_url)
            sheets_url = _legacy_sse_url(sheets_url)
        sheets_name = (
            "mcp-ledger" if settings.sheets_backend == "ledger" else "mcp-gsheets"
        )
        return (
            MCPToolRegistry(
                "mcp-archive",
                archive_url,
                auth_token=settings.mcp_auth_token or None,
            ),
            MCPToolRegistry(
                sheets_name,
                sheets_url,
                auth_token=settings.mcp_auth_token or None,
            ),
        )

    raise ValueError(
        f"Unknown MCP_TRANSPORT={settings.mcp_transport!r}; "
        "expected 'stdio', 'http', or 'sse'"
    )


def _legacy_sse_url(url: str) -> str:
    """Convert a configured MCP HTTP endpoint to its legacy SSE endpoint.

    Args:
        url: Configured remote MCP URL.

    Returns:
        The URL unchanged when it already ends in /sse, otherwise with the
        trailing /mcp path replaced by /sse.
    """
    normalized = url.rstrip("/")
    if normalized.endswith("/sse"):
        return normalized
    if normalized.endswith("/mcp"):
        return f"{normalized[:-4]}/sse"
    return f"{normalized}/sse"


class KlaudiaContainer:
    """Service container - manages lifecycle of all services."""

    def __init__(self) -> None:
        self.settings: Optional[Settings] = None
        self.llm_client: Optional[LLMClient] = None
        self.kie_client: Optional[KIEClient] = None
        self.db_client: Optional[AppDBClient] = None
        self.memory_documents: Optional[MemoryDocumentStore] = None
        self.dedup_cache: Optional[DedupCache] = None
        self.object_store: Optional[MinIOClient] = None
        self.ingest_service: Optional[IngestService] = None
        self.mcp_archive: Optional[MCPToolRegistry] = None
        self.mcp_gsheets: Optional[MCPToolRegistry] = None
        self.guardrails: Optional[GuardrailsAgent] = None
        self.supervisor: Optional[SupervisorAgent] = None
        self.ledger_store: Optional[LedgerStore] = None
        self.spreadsheets: Optional[SpreadsheetService] = None
        self.catalogue: Optional[CatalogueService] = None
        self.authoring: Optional[AuthoringService] = None
        self.main_chat: Optional[MainChatService] = None
        self.tasks: Optional[TaskStore] = None
        self.memory: Optional[MemoryService] = None
        self.approvals: Optional[ApprovalService] = None
        self.extraction_agent: Optional[ExtractionAgent] = None
        self.langfuse: Optional[LangfuseService] = None

    @classmethod
    async def create(cls, settings: Settings) -> "KlaudiaContainer":
        container = cls()
        container.settings = settings
        _ensure_gcp_credentials(settings)
        container.langfuse = LangfuseService(settings)
        container.llm_client = LLMClient(settings, langfuse=container.langfuse)
        container.kie_client = KIEClient(settings, langfuse=container.langfuse)
        container.db_client = AppDBClient(settings)
        await container.db_client.connect()
        container.memory_documents = MemoryDocumentStore(container.db_client.pool)
        await container.memory_documents.initialize()
        container.dedup_cache = DedupCache(settings)
        try:
            await container.dedup_cache.connect()
        except Exception as e:
            logger.warning(
                "Redis unavailable (%s). Dedup cache disabled; database fallback active.",
                e,
            )
            container.dedup_cache = None
        container.object_store = MinIOClient(settings)
        try:
            await container.object_store.ensure_bucket()
        except Exception as e:
            logger.error("MinIO unavailable (%s). Image/PDF uploads will fail.", e)
        if settings.sheets_backend == "ledger":
            container.ledger_store = LedgerStore(settings.database_url)
            await container.ledger_store.connect()
            container.spreadsheets = SpreadsheetService(container.ledger_store)
            container.catalogue = CatalogueService(
                CatalogueStore(container.ledger_store.pool)
            )

        if settings.memory_mode != "off":
            container.memory = MemoryService.from_settings(settings)

        container.mcp_archive, container.mcp_gsheets = _build_mcp_registries(settings)
        logger.info("MCP transport: %s", settings.mcp_transport)
        await container.mcp_archive.connect()
        await container.mcp_gsheets.connect()

        container.approvals = ApprovalService(
            container.db_client, container.mcp_gsheets
        )
        await container.approvals.ensure_schema()

        if container.dedup_cache is None:
            container.dedup_cache = _NullDedupCache()  # type: ignore[assignment]

        container.ingest_service = IngestService(
            settings=settings,
            db=container.db_client,
            cache=container.dedup_cache,  # type: ignore[arg-type]
            store=container.object_store,
            ocr=container.kie_client,
            langfuse=container.langfuse,
        )

        container.extraction_agent = ExtractionAgent(
            ingest_service=container.ingest_service,
            langfuse=container.langfuse,
        )

        guardrails_config = GuardrailsConfig(
            enabled=settings.guardrails_enabled,
            groq_api_key=settings.groq_api_key,
            groq_model=settings.llm_guardrails_prompt_inj,
            guardrails_model=settings.llm_guardrails_model,
            guardrails_provider=settings.guardrails_provider,
            deepseek_base_url=settings.deepseek_base_url,
            deepseek_api_key=settings.deepseek_api_key,
            disable_thinking=settings.llm_disable_thinking,
        )
        container.guardrails = GuardrailsAgent(
            container.llm_client, guardrails_config, langfuse=container.langfuse
        )

        if container.ledger_store is not None:
            container.authoring = AuthoringService(
                container.ledger_store.pool,
                require_approval=settings.main_chat_require_approval,
            )
            container.approvals.checked = CheckedApprovals(container.ledger_store.pool)
        openai_base_url, openai_api_key = settings.active_openai_endpoint()
        if settings.chat_runtime == "main":
            container.tasks = TaskStore(
                settings.database_url,
                require_approval=settings.main_chat_require_approval,
            )
            await container.tasks.connect()
            container.approvals.checked = CheckedApprovals(container.ledger_store.pool)
            model = build_chat_llm(
                model=settings.llm_model,
                provider=settings.model_provider,
                temperature=settings.llm_temperature,
                use_vertexai=settings.google_genai_use_vertexai,
                llm_api_key=settings.llm_api_key,
                google_cloud_project=settings.google_cloud_project,
                google_cloud_location=settings.google_cloud_location,
                openai_base_url=openai_base_url,
                openai_api_key=openai_api_key,
                thinking_level=settings.llm_thinking_level_worker,
                disable_thinking=settings.llm_disable_thinking,
            )
            container.main_chat = MainChatService(
                model,
                container.catalogue,
                container.db_client,
                operations=OperationService(container.ledger_store),
                tasks=container.tasks,
                authoring=container.authoring,
                langfuse=container.langfuse,
            )
        container.supervisor = SupervisorAgent(
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
            mcp_archive=container.mcp_archive,
            mcp_gsheets=container.mcp_gsheets,
            langfuse=container.langfuse,
            provider=settings.model_provider,
            use_vertexai=settings.google_genai_use_vertexai,
            google_cloud_project=settings.google_cloud_project,
            google_cloud_location=settings.google_cloud_location,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            disable_thinking=settings.llm_disable_thinking,
            temperature=settings.llm_temperature,
            thinking_level_routing=settings.llm_thinking_level_routing,
            thinking_level_worker=settings.llm_thinking_level_worker,
        )

        logger.info("KlaudiaContainer initialized")
        return container

    async def shutdown(self) -> None:
        logger.info("Starting graceful shutdown...")
        if self.guardrails:
            await self.guardrails.shutdown()
        if self.llm_client:
            await self.llm_client.shutdown()
        if self.kie_client:
            await self.kie_client.shutdown()
        if self.mcp_archive:
            await self.mcp_archive.disconnect()
        if self.mcp_gsheets:
            await self.mcp_gsheets.disconnect()
        if self.memory:
            self.memory.close()
        if self.tasks:
            await self.tasks.close()
        if self.ledger_store:
            await self.ledger_store.close()
        if isinstance(self.dedup_cache, DedupCache):
            await self.dedup_cache.close()
        if self.db_client:
            await self.db_client.close()
        if self.langfuse:
            self.langfuse.shutdown()
        logger.info("Shutdown complete")


class _NullDedupCache:
    """Null-object replacement for DedupCache when Redis is down.

    Every read misses, every write is a no-op. IngestService keeps working
    against PostgreSQL alone, with higher extraction latency.
    """

    async def get_blob(self, *_a, **_kw):  # noqa: D401
        return None

    async def set_blob(self, *_a, **_kw):
        return None

    async def get_extraction(self, *_a, **_kw):
        return None

    async def set_extraction(self, *_a, **_kw):
        return None

    async def delete_extraction(self, *_a, **_kw):
        return None

    async def queue_depth(self, *_a, **_kw):
        return 0

    async def close(self):
        return None
