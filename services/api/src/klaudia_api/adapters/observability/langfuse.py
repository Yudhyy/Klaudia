"""Langfuse observability service.

Fail-open: if credentials are missing or the client fails to initialize,
`LangfuseService.enabled` is False and all public methods become no-ops so
that the rest of the pipeline continues unobserved instead of breaking.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


class LangfuseService:
    """Thin wrapper around the Langfuse SDK.

    Provides:
      • a singleton-like handle to the Langfuse client
      • a LangChain/LangGraph callback handler
      • span/generation context helpers with fail-open semantics
      • a `flush()` hook used on shutdown and after tests
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._callback_handler: Any = None
        self._enabled = False

        if not settings.langfuse_enabled:
            logger.info("Langfuse disabled via LANGFUSE_ENABLED=false")
            return

        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            logger.info(
                "Langfuse credentials missing — tracing disabled. "
                "Set LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY to enable."
            )
            return

        try:
            from langfuse import Langfuse
            from langfuse.langchain import CallbackHandler

            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_base_url,
                environment=settings.stage,
                release=settings.version,
            )
            self._callback_handler = CallbackHandler()
            self._enabled = True
            logger.info(
                f"Langfuse initialized — env={settings.stage} host={settings.langfuse_base_url}"
            )
        except Exception as exc:
            logger.warning(f"Langfuse init failed, tracing disabled: {exc}")
            self._client = None
            self._callback_handler = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def client(self) -> Any:
        return self._client

    @property
    def callback_handler(self) -> Any:
        """LangChain callback handler — None when disabled."""
        return self._callback_handler

    def langchain_config(
        self,
        *,
        session_id: Optional[int] = None,
        user_id: Optional[int] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        run_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build a RunnableConfig dict with Langfuse callbacks + trace metadata.

        Returns an empty dict when tracing is disabled so callers can always
        merge it into graph.ainvoke/astream config.
        """
        if not self._enabled:
            return {}

        merged_meta: dict[str, Any] = {}
        if session_id is not None:
            merged_meta["langfuse_session_id"] = str(session_id)
        if user_id is not None:
            merged_meta["langfuse_user_id"] = str(user_id)
        if tags:
            merged_meta["langfuse_tags"] = list(tags)
        if metadata:
            merged_meta.update(metadata)

        config: dict[str, Any] = {"callbacks": [self._callback_handler]}
        if merged_meta:
            config["metadata"] = merged_meta
        if run_name:
            config["run_name"] = run_name
        return config

    @contextmanager
    def span(
        self,
        name: str,
        *,
        as_type: str = "span",
        input: Any = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Iterator[Any]:
        """Context manager for a Langfuse span/generation/agent/tool.

        Yields the underlying observation (or None if disabled) so the caller
        can `.update(output=..., metadata=...)` at the end.

        Fail-open: if Langfuse *setup* fails, yields None and continues.
        Exceptions raised *inside* the with-block propagate normally — the
        except must only guard setup, not the yield, to avoid the
        'generator didn't stop after throw()' RuntimeError.
        """
        if not self._enabled:
            yield None
            return

        try:
            observation_ctx = self._client.start_as_current_observation(
                name=name,
                as_type=as_type,
                input=input,
                metadata=metadata,
            )
        except Exception as exc:
            # Langfuse context creation failed → fail-open, yield None
            logger.debug(f"Langfuse span '{name}' setup failed, continuing: {exc}")
            yield None
            return

        with observation_ctx as obs:
            yield obs

    @contextmanager
    def trace_attributes(
        self,
        *,
        session_id: Optional[int] = None,
        user_id: Optional[int] = None,
        tags: Optional[list[str]] = None,
    ) -> Iterator[None]:
        """Propagate user_id/session_id/tags to all nested observations."""
        if not self._enabled:
            yield
            return
        try:
            from langfuse import propagate_attributes

            ctx = propagate_attributes(
                user_id=str(user_id) if user_id is not None else None,
                session_id=str(session_id) if session_id is not None else None,
                tags=tags,
            )
        except Exception as exc:
            # Setup failed → fail-open, same double-yield guard as span()
            logger.debug(f"Langfuse propagate_attributes setup failed: {exc}")
            yield
            return

        with ctx:
            yield

    def flush(self) -> None:
        if self._enabled and self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:
                logger.debug(f"Langfuse flush failed: {exc}")

    def shutdown(self) -> None:
        if self._enabled and self._client is not None:
            try:
                self._client.shutdown()
            except Exception as exc:
                logger.debug(f"Langfuse shutdown failed: {exc}")
