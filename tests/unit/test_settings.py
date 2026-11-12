"""Settings validation tests."""

import pytest
from pydantic import ValidationError

from klaudia_api.composition.settings import DEFAULT_DATABASE_URL, Settings


def test_deepseek_vision_is_default_kie_model():
    """Use DeepSeek vision when no KIE model is configured."""
    settings = Settings(_env_file=None)

    assert settings.kie_model == "deepseek-flash"


def test_toon_is_default_extraction_context_format() -> None:
    """Use TOON for new extraction context unless configured otherwise."""
    settings = Settings(_env_file=None)

    assert settings.extraction_context_format == "toon"


def test_extraction_context_format_rejects_unknown_value() -> None:
    """Reject formats without a registered context encoder."""
    with pytest.raises(ValidationError, match="EXTRACTION_CONTEXT_FORMAT"):
        Settings(_env_file=None, EXTRACTION_CONTEXT_FORMAT="yaml")


def test_settings_repr_hides_credentials():
    """Keep credentials out of logs and test tracebacks."""
    secret = "credential-that-must-not-appear"
    settings = Settings(
        _env_file=None,
        JWT_SECRET=secret,
        VLLM_LLM_API_KEY=secret,
        DEEPSEEK_API_KEY=secret,
        LLM_API_KEY=secret,
        VLLM_KIE_API_KEY=secret,
        GROQ_API_KEY=secret,
        DATABASE_URL=secret,
        REDIS_URL=secret,
        MINIO_ACCESS_KEY=secret,
        MINIO_SECRET_KEY=secret,
        TASKIQ_BROKER_URL=secret,
        TASKIQ_RESULT_BACKEND_URL=secret,
        MCP_AUTH_TOKEN=secret,
        LANGFUSE_SECRET_KEY=secret,
    )

    assert secret not in repr(settings)


def test_production_rejects_development_database_url():
    """Reject the development URL even when the test runner sets a database."""
    settings = Settings(
        _env_file=None,
        STAGE="production",
        DATABASE_URL=DEFAULT_DATABASE_URL,
        JWT_SECRET="production-test-secret-at-least-32-bytes",
    )

    with pytest.raises(ValueError, match="DATABASE_URL"):
        settings.validate_production_config()
