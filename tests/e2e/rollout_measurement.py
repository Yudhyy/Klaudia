"""Bounded local rollout measurements, independent of live fixture configuration."""

from decimal import Decimal
import json
import math
from unittest.mock import patch

import httpx

PRICE_CHECKED_ON = "2026-09-24"
PRICES = {
    ("api.deepseek.com", "deepseek-flash"): (Decimal("0.30"), Decimal("1.20")),
    ("api.groq.com", "meta-llama/llama-prompt-guard-2-86m"): (
        Decimal("0.04"),
        Decimal("0.04"),
    ),
}


def rollout_configuration(settings) -> dict:
    """Freeze provider settings and reject disabled qualification guards.

    Args:
        settings: Validated application settings.

    Returns:
        Public model and guardrail configuration without credentials.

    Raises:
        ValueError: Guardrails differ from the declared live configuration.
    """
    if not settings.guardrails_enabled or (
        settings.guardrails_provider,
        settings.llm_guardrails_model,
        settings.llm_guardrails_prompt_inj.lower(),
    ) != ("deepseek", "deepseek-flash", "meta-llama/llama-prompt-guard-2-86m"):
        raise ValueError(
            "Qualification requires enabled DeepSeek text guards and Groq injection screening"
        )
    return {
        "provider": settings.model_provider,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "disable_thinking": settings.llm_disable_thinking,
        "guardrails_enabled": settings.guardrails_enabled,
        "guardrails_provider": settings.guardrails_provider,
        "guardrails_model": settings.llm_guardrails_model,
        "injection_model": settings.llm_guardrails_prompt_inj,
        "numeric_verify_mode": settings.numeric_verify_mode,
    }


class UsageMeter:
    """Observe provider responses without retaining prompts, secrets or response text."""

    def __init__(self) -> None:
        """Start a fresh list of provider request outcomes."""
        self.calls: list[dict] = []
        self._patch = None

    def __enter__(self) -> "UsageMeter":
        """Observe HTTP completion calls during one sequential qualification turn.

        Returns:
            This meter, with its temporary HTTP observer installed.
        """
        original_send = httpx.AsyncClient.send

        async def measured_send(client, request, **kwargs):
            """Retain counters from every completion attempt without changing its body.

            Args:
                client: Existing HTTP client.
                request: Outgoing request owned by its SDK.
                kwargs: Original transport options.

            Returns:
                Unchanged SDK response after reading nonstreamed JSON usage.
            """
            if not request.url.path.endswith("/chat/completions"):
                return await original_send(client, request, **kwargs)
            payload = json.loads(request.content)
            observed = {
                "host": request.url.host,
                "model": payload.get("model"),
                "complete": False,
            }
            self.calls.append(observed)
            try:
                response = await original_send(client, request, **kwargs)
                observed["status_code"] = response.status_code
                if response.is_success and not payload.get("stream", False):
                    await response.aread()
                    body = response.json()
                    observed["returned_model"] = body.get("model")
                    usage = body.get("usage") or {}
                    counts = (
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                    )
                    rates = PRICES.get(
                        (request.url.host, str(payload.get("model")).lower())
                    )
                    if rates is not None and all(
                        type(count) is int and count >= 0 for count in counts
                    ):
                        observed.update(
                            complete=True,
                            input_tokens=counts[0],
                            output_tokens=counts[1],
                            cost_upper_bound_usd=str(
                                (counts[0] * rates[0] + counts[1] * rates[1])
                                / Decimal(1_000_000)
                            ),
                        )
                return response
            except BaseException as exc:
                observed["error_type"] = type(exc).__name__
                raise

        self._patch = patch.object(httpx.AsyncClient, "send", measured_send)
        self._patch.start()
        return self

    def __exit__(self, *_exception) -> None:
        """Restore the original transport even when a trial fails."""
        self._patch.stop()

    def summary(self) -> dict:
        """Aggregate known counters without interpreting missing calls as free.

        Returns:
            Usage completeness and a conservative inference-only price bound.
        """
        complete = bool(self.calls) and all(call["complete"] for call in self.calls)
        return {
            "complete": complete,
            "calls": len(self.calls),
            "input_tokens": sum(call.get("input_tokens", 0) for call in self.calls),
            "output_tokens": sum(call.get("output_tokens", 0) for call in self.calls),
            "cost_upper_bound_usd": str(
                sum(
                    (Decimal(call["cost_upper_bound_usd"]) for call in self.calls),
                    Decimal(0),
                )
            )
            if complete
            else None,
            "price_checked_on": PRICE_CHECKED_ON,
            "pricing": "peak_uncached_input_upper_bound",
        }


def qualify(trials: dict[str, dict]) -> dict:
    """Apply predeclared limits without dropping failed or unrun scheduled trials.

    Args:
        trials: Complete scheduled trial map with separately reviewed pass statuses.

    Returns:
        Pass denominator, observed nearest-rank p95 and acceptance decision.
    """
    turns = [turn for trial in trials.values() for turn in trial.get("turns", [])]
    latencies = sorted(
        turn["elapsed_seconds"]
        for turn in turns
        if turn.get("kind", "interaction") == "interaction"
    )
    replay_latencies = sorted(
        turn["elapsed_seconds"] for turn in turns if turn.get("kind") == "replay"
    )
    latency_valid = bool(latencies) and all(
        math.isfinite(seconds) and seconds >= 0 for seconds in latencies
    )
    p95 = latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
    replay_p95 = (
        replay_latencies[math.ceil(len(replay_latencies) * 0.95) - 1]
        if replay_latencies
        else None
    )
    replay_ok = not replay_latencies or (
        all(math.isfinite(seconds) and seconds >= 0 for seconds in replay_latencies)
        and replay_p95 <= 60
    )
    passed = sum(trial["status"] == "passed" for trial in trials.values())
    cost_ok = bool(turns) and all(
        turn["usage"]["complete"]
        and turn["usage"]["cost_upper_bound_usd"] is not None
        and (cost := Decimal(turn["usage"]["cost_upper_bound_usd"])).is_finite()
        and 0 <= cost <= Decimal("0.10")
        for turn in turns
    )
    return {
        "scheduled": len(trials),
        "passed": passed,
        "observed_turns": len(turns),
        "p95_seconds": p95,
        "replay_p95_seconds": replay_p95,
        "cost_within_limit": cost_ok,
        "accepted": bool(trials)
        and all(
            any(
                turn.get("kind", "interaction") == "interaction"
                for turn in trial.get("turns", [])
            )
            for trial in trials.values()
        )
        and passed == len(trials)
        and latency_valid
        and p95 is not None
        and p95 <= 60
        and replay_ok
        and cost_ok,
    }
