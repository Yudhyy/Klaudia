from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

DEFAULT_DATABASE_URL = "postgresql://klaudia:klaudia@localhost:5432/klaudia"


class Settings(BaseSettings):
    # Service
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    service_name: str = Field(default="Klaudia Chatbot", alias="SERVICE_NAME")
    version: str = Field(default="1.0.0", alias="VERSION")
    debug: bool = Field(default=True, alias="DEBUG")
    stage: str = Field(default="development", alias="STAGE")

    jwt_secret: str = Field(
        default="dev-secret-change-me-before-any-deploy",
        alias="JWT_SECRET",
        repr=False,
    )
    jwt_expires_days: int = Field(default=7, alias="JWT_EXPIRES_DAYS")

    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_auth: str = Field(default="10/minute", alias="RATE_LIMIT_AUTH")
    rate_limit_chat: str = Field(default="30/minute", alias="RATE_LIMIT_CHAT")

    model_provider: str = Field(default="google", alias="MODEL_PROVIDER")
    llm_disable_thinking: bool = Field(default=True, alias="LLM_DISABLE_THINKING")

    vllm_llm_endpoint: str = Field(default="", alias="VLLM_LLM_ENDPOINT")
    vllm_llm_api_key: str = Field(default="", alias="VLLM_LLM_API_KEY", repr=False)
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL"
    )
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY", repr=False)

    # LLM (Google Gemini via native google-genai SDK)
    llm_model: str = Field(default="gemini-3-flash-preview", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY", repr=False)
    llm_temperature: float = Field(default=0.5, alias="LLM_TEMPERATURE")
    llm_thinking_level_routing: str = Field(
        default="minimal", alias="LLM_THINKING_LEVEL_ROUTING"
    )
    llm_thinking_level_worker: str = Field(
        default="minimal", alias="LLM_THINKING_LEVEL_WORKER"
    )
    google_cloud_project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="global", alias="GOOGLE_CLOUD_LOCATION")
    google_genai_use_vertexai: bool = Field(
        default=False, alias="GOOGLE_GENAI_USE_VERTEXAI"
    )
    google_application_credentials: str = Field(
        default="", alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    kie_model: str = Field(default="deepseek-flash", alias="KIE_MODEL")
    vllm_kie_endpoint: str = Field(default="", alias="VLLM_KIE_ENDPOINT")
    vllm_kie_api_key: str = Field(default="", alias="VLLM_KIE_API_KEY", repr=False)
    mock_kie: bool = Field(default=True, alias="MOCK_KIE")
    numeric_verify_mode: str = Field(default="log", alias="NUMERIC_VERIFY_MODE")
    extraction_context_format: Literal["toon", "json"] = Field(
        default="toon", alias="EXTRACTION_CONTEXT_FORMAT"
    )

    guardrails_enabled: bool = Field(default=True, alias="GUARDRAILS_ENABLED")
    llm_guardrails_prompt_inj: str = Field(
        default="meta-llama/Llama-Prompt-Guard-2-86M",
        alias="LLM_GUARDRAILS_PROMPT_INJ",
    )
    guardrails_provider: str = Field(default="google", alias="GUARDRAILS_PROVIDER")
    llm_guardrails_model: str = Field(
        default="gemini-3.1-flash-lite-preview", alias="LLM_GUARDRAILS_MODEL"
    )
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY", repr=False)

    # Database
    database_url: str = Field(
        default=DEFAULT_DATABASE_URL,
        alias="DATABASE_URL",
        min_length=1,
        repr=False,
    )

    # Memory
    memory_mode: str = Field(default="off", alias="MEMORY_MODE")  # off|read|write
    memory_write_mode: str = Field(default="inline", alias="MEMORY_WRITE_MODE")
    memory_top_k: int = Field(default=6, alias="MEMORY_TOP_K")
    memory_collection: str = Field(default="klaudia_memory", alias="MEMORY_COLLECTION")
    memory_llm_model: str = Field(default="deepseek-flash", alias="MEMORY_LLM_MODEL")
    memory_embed_base_url: str = Field(
        default="http://localhost:8100/v1", alias="MEMORY_EMBED_BASE_URL"
    )
    memory_embed_model: str = Field(
        default="paraphrase-multilingual-MiniLM-L12-v2", alias="MEMORY_EMBED_MODEL"
    )
    memory_embed_dims: int = Field(default=384, alias="MEMORY_EMBED_DIMS")

    # Redis (hot cache + Taskiq broker + pubsub)
    redis_url: str = Field(
        default="redis://localhost:6379/0", alias="REDIS_URL", repr=False
    )
    dedup_cache_ttl_seconds: int = Field(default=86400, alias="DEDUP_CACHE_TTL_SECONDS")

    # MinIO (object storage)
    minio_endpoint: str = Field(default="http://127.0.0.1:9000", alias="MINIO_ENDPOINT")
    minio_region: str = Field(default="us-east-1", alias="MINIO_REGION")
    minio_access_key: str = Field(
        default="minioadmin", alias="MINIO_ACCESS_KEY", repr=False
    )
    minio_secret_key: str = Field(
        default="minioadmin", alias="MINIO_SECRET_KEY", repr=False
    )
    minio_bucket: str = Field(default="klaudia-blobs", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    # Taskiq
    taskiq_broker_url: str = Field(
        default="redis://localhost:6379/1",
        alias="TASKIQ_BROKER_URL",
        repr=False,
    )
    taskiq_result_backend_url: str = Field(
        default="redis://localhost:6379/2",
        alias="TASKIQ_RESULT_BACKEND_URL",
        repr=False,
    )
    taskiq_queue_name: str = Field(default="ocr:extract", alias="TASKIQ_QUEUE_NAME")
    taskiq_result_ttl_seconds: int = Field(
        default=3600, alias="TASKIQ_RESULT_TTL_SECONDS"
    )

    # Extraction limits / pressure relief
    max_image_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_IMAGE_BYTES")
    max_pdf_bytes: int = Field(default=50 * 1024 * 1024, alias="MAX_PDF_BYTES")
    max_pdf_pages: int = Field(default=50, alias="MAX_PDF_PAGES")
    max_images_per_upload: int = Field(default=5, alias="MAX_IMAGES_PER_UPLOAD")
    extraction_queue_depth_reject: int = Field(
        default=200, alias="EXTRACTION_QUEUE_DEPTH_REJECT"
    )
    ocr_schema_version: str = Field(default="v1", alias="OCR_SCHEMA_VERSION")

    extraction_mode: str = Field(default="async", alias="EXTRACTION_MODE")
    # Per-task timeout (s) after the orchestrator stops waiting for a page.
    extraction_page_timeout_seconds: int = Field(
        default=120, alias="EXTRACTION_PAGE_TIMEOUT_SECONDS"
    )

    # MCP transport: "stdio" owns local subprocesses; "http" connects to
    # stateless remote services; "sse" is a legacy rollback mode.
    mcp_transport: str = Field(default="stdio", alias="MCP_TRANSPORT")
    mcp_archive_url: str = Field(
        default="http://localhost:8001/mcp", alias="MCP_ARCHIVE_URL"
    )
    mcp_ledger_url: str = Field(
        default="http://localhost:8003/mcp", alias="MCP_LEDGER_URL"
    )
    mcp_gsheets_url: str = Field(
        default="http://localhost:8002/mcp", alias="MCP_GSHEETS_URL"
    )
    mcp_auth_token: str = Field(default="", alias="MCP_AUTH_TOKEN", repr=False)

    sheets_backend: str = Field(default="ledger", alias="SHEETS_BACKEND")
    main_chat_require_approval: bool = Field(
        default=False, alias="MAIN_CHAT_REQUIRE_APPROVAL"
    )
    chat_runtime: Literal["legacy", "main"] = Field(
        default="main", alias="CHAT_RUNTIME"
    )

    @model_validator(mode="after")
    def validate_chat_runtime(self) -> "Settings":
        """Reject main chat without its ownership-enforcing ledger backend.

        Returns:
            Validated service settings.

        Raises:
            ValueError: Main chat uses an unsupported sheets backend.
        """
        if self.chat_runtime == "main" and self.sheets_backend != "ledger":
            raise ValueError("CHAT_RUNTIME=main requires SHEETS_BACKEND=ledger")
        return self

    # Langfuse Observability
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(
        default="", alias="LANGFUSE_SECRET_KEY", repr=False
    )
    langfuse_base_url: str = Field(
        default="https://cloud.langfuse.com", alias="LANGFUSE_BASE_URL"
    )
    langfuse_enabled: bool = Field(default=True, alias="LANGFUSE_ENABLED")

    model_config = {"env_file": ".env", "extra": "ignore"}

    def active_openai_endpoint(self) -> tuple[str, str]:
        """Return (base_url, api_key) for the active OpenAI-compatible provider.

        Only meaningful when model_provider is "vllm" or "deepseek".
        """
        if self.model_provider.strip().lower() in ("deepseek",):
            return self.deepseek_base_url, self.deepseek_api_key
        return self.vllm_llm_endpoint, self.vllm_llm_api_key

    def validate_production_config(self) -> None:
        """Reject development defaults in production.

        Called once during application startup.

        Raises:
            ValueError: If production uses a development credential or DSN.
        """
        if self.stage != "production":
            return
        if self.jwt_secret == "dev-secret-change-me-before-any-deploy":
            raise ValueError(
                "JWT_SECRET must be set to a strong random value when "
                "STAGE=production (dev default refused)."
            )
        if self.database_url == DEFAULT_DATABASE_URL:
            raise ValueError(
                "DATABASE_URL must be set to the production PostgreSQL DSN when "
                "STAGE=production (development default refused)."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
