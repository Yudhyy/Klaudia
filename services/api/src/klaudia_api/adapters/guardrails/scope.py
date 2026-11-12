import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from klaudia_api.adapters.guardrails.config import GuardrailsConfig
from klaudia_api.adapters.guardrails.llm import GuardrailChatLLM
from klaudia_api.adapters.guardrails.prompts import (
    FINANCIAL_ADVICE_CHECK_PROMPT,
    SARA_CHECK_PROMPT,
)

logger = logging.getLogger(__name__)


@dataclass
class ScopeViolation:
    violated: bool
    policy: Optional[str] = None  # "SARA" | "FINANCIAL_ADVICE" | None


async def _check_policy(
    text: str,
    prompt_template: str,
    policy_name: str,
    llm_client: GuardrailChatLLM,
    config: GuardrailsConfig,
) -> bool:
    """Run a single policy prompt against the LLM. Returns True if violated."""
    prompt = prompt_template.format(message=text)
    try:
        result = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config.guardrails_model,
            temperature=0.0,
            max_tokens=10,
            span_name=f"guardrail.scope.{policy_name.lower()}",
        )
        is_blocked = result.strip().upper().startswith("YES")
        if is_blocked:
            logger.warning(f"{policy_name} violation detected: {text[:80]}...")
        return is_blocked
    except Exception as e:
        logger.error(f"{policy_name} check failed: {e}")
        # Fail open — don't block if the guard is down
        return False


async def check_scope(
    text: str,
    llm_client: GuardrailChatLLM,
    config: GuardrailsConfig,
) -> ScopeViolation:
    """Run SARA and Financial Advice policy checks in parallel.

    Returns a ScopeViolation indicating which policy (if any) was triggered.
    SARA takes precedence if both fire simultaneously.
    """
    sara_task = _check_policy(text, SARA_CHECK_PROMPT, "SARA", llm_client, config)
    fa_task = _check_policy(
        text, FINANCIAL_ADVICE_CHECK_PROMPT, "FINANCIAL_ADVICE", llm_client, config
    )

    is_sara, is_fa = await asyncio.gather(sara_task, fa_task)

    if is_sara:
        return ScopeViolation(violated=True, policy="SARA")
    if is_fa:
        return ScopeViolation(violated=True, policy="FINANCIAL_ADVICE")
    return ScopeViolation(violated=False)
