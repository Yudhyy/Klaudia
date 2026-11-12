import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from klaudia_api.adapters.llm.client import LLMClient
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.guardrails.base import check_prompt_injection
from klaudia_api.adapters.guardrails.config import GuardrailsConfig
from klaudia_api.adapters.guardrails.llm import GuardrailsLLMRouter
from klaudia_api.adapters.guardrails.output import check_output
from klaudia_api.adapters.guardrails.prompts import REJECTION_MESSAGES
from klaudia_api.adapters.guardrails.scope import check_scope

logger = logging.getLogger(__name__)


@contextmanager
def _nullspan():
    yield None


@dataclass
class GuardrailResult:
    passed: bool
    rejection_message: str | None = None


class GuardrailsAgent:
    """Runs input validation checks in parallel: prompt injection + scope."""

    def __init__(
        self,
        llm_client: LLMClient,
        config: GuardrailsConfig,
        langfuse: Optional[LangfuseService] = None,
    ) -> None:
        self._llm_client = llm_client
        self._config = config
        self._langfuse = langfuse
        # Routes the scope/output LLM checks to Gemini or DeepSeek per
        # config.guardrails_provider. Prompt-injection stays on Groq (base.py).
        self._guard_llm = GuardrailsLLMRouter(llm_client, config, langfuse)

    async def validate_input(self, text: str) -> GuardrailResult:
        """Run prompt injection and scope checks in parallel."""
        if not self._config.enabled:
            return GuardrailResult(passed=True)

        span_cm = (
            self._langfuse.span(
                "guardrail.validate_input",
                as_type="guardrail",
                input=text,
            )
            if self._langfuse is not None
            else _nullspan()
        )

        with span_cm as obs:
            injection_task = check_prompt_injection(text, self._config, self._langfuse)
            scope_task = check_scope(text, self._guard_llm, self._config)

            is_injection, scope_result = await asyncio.gather(
                injection_task, scope_task
            )

            if is_injection:
                msg = REJECTION_MESSAGES["prompt_injection"]
                logger.warning("Input rejected: prompt injection")
                if obs is not None:
                    try:
                        obs.update(
                            output={"passed": False, "reason": "prompt_injection"}
                        )
                    except Exception:
                        pass
                return GuardrailResult(passed=False, rejection_message=msg)

            if scope_result.violated:
                policy = scope_result.policy or "blacklisted_topic"
                msg = REJECTION_MESSAGES.get(
                    policy, REJECTION_MESSAGES["blacklisted_topic"]
                )
                logger.warning(f"Input rejected: {policy}")
                if obs is not None:
                    try:
                        obs.update(output={"passed": False, "reason": policy})
                    except Exception:
                        pass
                return GuardrailResult(passed=False, rejection_message=msg)

            if obs is not None:
                try:
                    obs.update(output={"passed": True})
                except Exception:
                    pass
            return GuardrailResult(passed=True)

    async def shutdown(self) -> None:
        """Release the guardrail LLM router's owned resources (DeepSeek client)."""
        await self._guard_llm.shutdown()

    async def validate_output(self, response_text: str) -> GuardrailResult:
        """Validate assistant output against blacklisted topics."""
        if not self._config.enabled:
            return GuardrailResult(passed=True)
        span_cm = (
            self._langfuse.span(
                "guardrail.validate_output",
                as_type="guardrail",
                input=response_text,
            )
            if self._langfuse is not None
            else _nullspan()
        )

        with span_cm as obs:
            is_blocked = await check_output(
                response_text, self._guard_llm, self._config
            )
            if is_blocked:
                msg = REJECTION_MESSAGES["blacklisted_topic"]
                if obs is not None:
                    try:
                        obs.update(
                            output={"passed": False, "reason": "blacklisted_topic"}
                        )
                    except Exception:
                        pass
                return GuardrailResult(passed=False, rejection_message=msg)
            if obs is not None:
                try:
                    obs.update(output={"passed": True})
                except Exception:
                    pass
            return GuardrailResult(passed=True)
