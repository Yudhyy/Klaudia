import logging
from contextlib import contextmanager
from typing import Optional

from groq import AsyncGroq

from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.guardrails.config import GuardrailsConfig

logger = logging.getLogger(__name__)


@contextmanager
def _nullspan():
    yield None


async def check_prompt_injection(
    text: str,
    config: GuardrailsConfig,
    langfuse: Optional[LangfuseService] = None,
) -> bool:
    """Check for prompt injection using Groq Llama-Prompt-Guard.

    Returns True if the input is MALICIOUS, False if BENIGN.
    """
    span_cm = (
        langfuse.span(
            "guardrail.prompt_injection",
            as_type="generation",
            input=text,
            metadata={"model": config.groq_model, "provider": "groq"},
        )
        if langfuse is not None
        else _nullspan()
    )

    with span_cm as obs:
        client = AsyncGroq(api_key=config.groq_api_key)
        try:
            completion = await client.chat.completions.create(
                model=config.groq_model,
                messages=[{"role": "user", "content": text}],
            )
            raw = completion.choices[0].message.content.strip().upper()
            # Model may return "MALICIOUS"/"BENIGN" labels or a probability score (0.0-1.0)
            if raw in ("MALICIOUS", "BENIGN"):
                is_malicious = raw == "MALICIOUS"
            else:
                try:
                    score = float(raw)
                    is_malicious = score > 0.5
                except ValueError:
                    is_malicious = "MALICIOUS" in raw
            if is_malicious:
                logger.warning(f"Prompt injection detected: {text[:80]}...")
            if obs is not None:
                try:
                    obs.update(
                        output={"raw": raw, "is_malicious": is_malicious},
                        model=config.groq_model,
                    )
                except Exception:
                    pass
            return is_malicious
        except Exception as e:
            logger.error(f"Prompt injection check failed: {e}")
            if obs is not None:
                try:
                    obs.update(level="ERROR", status_message=str(e))
                except Exception:
                    pass
            # Fail open - allow through if guard is down
            return False
        finally:
            await client.close()
