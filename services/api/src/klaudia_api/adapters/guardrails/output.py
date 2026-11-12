import logging

from klaudia_api.adapters.guardrails.config import GuardrailsConfig
from klaudia_api.adapters.guardrails.llm import GuardrailChatLLM
from klaudia_api.adapters.guardrails.prompts import OUTPUT_CHECK_PROMPT

logger = logging.getLogger(__name__)


async def check_output(
    response_text: str, llm_client: GuardrailChatLLM, config: GuardrailsConfig
) -> bool:
    """Check if assistant output contains blacklisted content.

    Returns True if blacklisted content is detected.
    OUTPUT_CHECK_PROMPT is hardcoded with SARA + Financial Advice policy — no
    dynamic topic injection needed.
    """
    prompt = OUTPUT_CHECK_PROMPT.format(response=response_text)

    try:
        result = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config.guardrails_model,
            temperature=0.0,
            max_tokens=10,
            span_name="guardrail.output_check",
        )
        is_blocked = result.strip().upper().startswith("YES")
        if is_blocked:
            logger.warning("Output guardrail triggered")
        return is_blocked
    except Exception as e:
        logger.error(f"Output check failed: {e}")
        return False
