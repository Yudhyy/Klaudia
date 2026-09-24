"""Rollout accounting must retain unknown usage and incomplete schedules."""

import httpx
import pytest

from tests.e2e.rollout_measurement import UsageMeter, qualify, rollout_configuration
from config.settings import Settings
from tests.e2e.rollout_cases import measured_post


async def test_meter_counts_main_and_guardrail_usage_without_copying_prompts():
    """Count every provider HTTP completion using conservative recorded prices."""

    def respond(request):
        """Return synthetic provider counters without external calls."""
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                "choices": [],
            },
        )

    with UsageMeter() as meter:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for _ in range(3):
                await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    json={
                        "model": "deepseek-flash",
                        "messages": [{"content": "Do not retain this prompt"}],
                    },
                )
    usage = meter.summary()
    assert usage["complete"] is True
    assert usage["input_tokens"] == 3000
    assert usage["output_tokens"] == 300
    assert usage["cost_upper_bound_usd"] == "0.00126"
    assert "messages" not in str(meter.calls)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 1},
    ],
)
async def test_missing_or_invalid_usage_never_becomes_zero_cost(usage):
    """Fail cost qualification rather than inventing absent token counts."""
    with UsageMeter() as meter:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"usage": usage})
            )
        ) as client:
            await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                json={"model": "deepseek-flash"},
            )
    assert meter.summary()["complete"] is False
    assert meter.summary()["cost_upper_bound_usd"] is None


def test_unrun_and_failed_trials_remain_in_denominator():
    """A passing observed subset cannot qualify an incomplete schedule."""
    trials = {
        "one": {
            "status": "passed",
            "turns": [
                {
                    "elapsed_seconds": 1,
                    "usage": {"complete": True, "cost_upper_bound_usd": "0.01"},
                }
            ],
        },
        "two": {"status": "unrun"},
    }
    summary = qualify(trials)
    assert summary["scheduled"] == 2
    assert summary["passed"] == 1
    assert summary["accepted"] is False


@pytest.mark.parametrize("seconds,cost", [(61, "0.01"), (1, "0.100001")])
def test_operating_limits_are_hard_failures(seconds, cost):
    """Reject an otherwise correct trial outside declared latency or cost limits."""
    assert (
        qualify(
            {
                "one": {
                    "status": "passed",
                    "turns": [
                        {
                            "elapsed_seconds": seconds,
                            "usage": {"complete": True, "cost_upper_bound_usd": cost},
                        }
                    ],
                }
            }
        )["accepted"]
        is False
    )


def test_empty_usage_and_pending_prose_do_not_qualify():
    """Require usage and independent final-answer review, not just correct state."""
    assert UsageMeter().summary()["complete"] is False
    assert (
        qualify({"one": {"status": "state_passed_prose_pending", "turns": []}})[
            "accepted"
        ]
        is False
    )


def test_passed_trial_without_measurements_cannot_qualify():
    """Require turn evidence for each scheduled trial, not only the combined sample."""
    trials = {
        "empty": {"status": "passed"},
        "observed": {
            "status": "passed",
            "turns": [
                {
                    "elapsed_seconds": 1,
                    "usage": {"complete": True, "cost_upper_bound_usd": "0.01"},
                }
            ],
        },
    }
    assert qualify(trials)["accepted"] is False


@pytest.mark.parametrize(
    "seconds,cost", [(-1, "0.01"), (float("nan"), "0.01"), (1, "-1"), (1, "NaN")]
)
def test_invalid_measurements_fail_closed(seconds, cost):
    """Reject nonfinite and negative counters even if a report says it passed."""
    trials = {
        "one": {
            "status": "passed",
            "turns": [
                {
                    "elapsed_seconds": seconds,
                    "usage": {"complete": True, "cost_upper_bound_usd": cost},
                }
            ],
        }
    }
    assert qualify(trials)["accepted"] is False


async def test_failed_turn_retains_provider_usage_and_restores_transport():
    """Keep billable counters even when the surrounding request raises."""
    original = httpx.AsyncClient.send

    class FailingClient:
        """Fail only after a simulated provider returns token counts."""

        async def post(self, route, json):
            """Observe a completion before the application loses its connection."""
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
                    )
                )
            ) as provider:
                await provider.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-flash"},
                )
            raise ConnectionError("Lost application response")

    observed = {}
    with pytest.raises(ConnectionError):
        await measured_post(FailingClient(), ("/v1/chat", {}), observed)
    turn = observed["turns"][0]
    assert turn["usage"]["complete"] is True
    assert turn["usage"]["input_tokens"] == 10
    assert turn["elapsed_seconds"] >= 0
    assert turn["error_type"] == "ConnectionError"
    assert httpx.AsyncClient.send is original


def test_fast_replays_cannot_dilute_slow_interaction_latency():
    """Apply the latency bound independently to actual interactions."""
    usage = {"complete": True, "cost_upper_bound_usd": "0.01"}
    turns = [{"kind": "interaction", "elapsed_seconds": 61, "usage": usage}]
    turns.extend(
        {"kind": "replay", "elapsed_seconds": 1, "usage": usage} for _ in range(30)
    )
    summary = qualify({"one": {"status": "passed", "turns": turns}})
    assert summary["p95_seconds"] == 61
    assert summary["replay_p95_seconds"] == 1
    assert summary["accepted"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_live_configuration_records_real_settings_and_requires_guards(enabled):
    """An environment override cannot silently remove measured guardrail calls."""
    settings = Settings(
        _env_file=None,
        GUARDRAILS_ENABLED=enabled,
        GUARDRAILS_PROVIDER="deepseek",
        LLM_GUARDRAILS_MODEL="deepseek-flash",
        LLM_GUARDRAILS_PROMPT_INJ="meta-llama/Llama-Prompt-Guard-2-86M",
    )
    if enabled:
        recorded = rollout_configuration(settings)
        assert recorded["guardrails_enabled"] is True
        assert recorded["guardrails_model"] == "deepseek-flash"
    else:
        with pytest.raises(ValueError, match="enabled"):
            rollout_configuration(settings)
