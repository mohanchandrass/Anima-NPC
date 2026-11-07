import asyncio
import json
import time

import pytest

from npc.contracts.context import ContextScenario
from npc.contracts.dialogue import CurrentState, ProviderReply
from npc.generation.provider import ProviderFailure
from npc.runtime.preview import PreviewLimits, PreviewRunner

VALID = '{"mode":"speech","text":"Work is tiring.","cue":null,"evidence_ids":[]}'


class ScriptedProvider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, prompt, timeout_s):
        self.calls.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ProviderReply(content=reply, model="fake", finish_reason="stop", input_tokens=10, output_tokens=5)


def generating(data):
    data["turn"]["facts"] = []
    data["turn"]["player_input"]["text"] = "How is your work going?"
    return ContextScenario.model_validate(data)


def test_one_call_success(scenario_data):
    provider = ScriptedProvider(VALID)
    result = asyncio.run(PreviewRunner(provider).run(generating(scenario_data)))
    assert result.status == "model_success"
    assert result.response.text == "Work is tiring."
    assert result.model_calls == 1
    assert result.input_tokens == 10
    assert result.timing_profile == "realtime"


def test_invalid_output_gets_one_targeted_repair(scenario_data):
    provider = ScriptedProvider("PRIVATE_INVALID_OUTPUT", VALID)
    result = asyncio.run(PreviewRunner(provider).run(generating(scenario_data)))
    assert result.status == "repair_success"
    assert result.model_calls == 2
    assert result.input_tokens == 20
    assert "invalid_json" in result.violations
    assert "PRIVATE_INVALID_OUTPUT" not in "".join(item.content for item in provider.calls[1].messages)


def test_two_invalid_outputs_then_validated_fallback(scenario_data):
    provider = ScriptedProvider("bad", "still bad")
    result = asyncio.run(PreviewRunner(provider).run(generating(scenario_data)))
    assert result.status == "error_fallback"
    assert result.model_calls == 2
    assert result.response.text == result.plan.fallback_text


@pytest.mark.parametrize("code", ["provider_rate_limited", "provider_auth", "provider_transport", "provider_output_limit",
                                  "provider_bad_request", "provider_protocol", "provider_unavailable", "provider_timeout"])
def test_provider_failures_fall_back_without_retry(scenario_data, code):
    provider = ScriptedProvider(ProviderFailure(code))
    result = asyncio.run(PreviewRunner(provider).run(generating(scenario_data)))
    assert result.status == "error_fallback"
    assert result.model_calls == 1
    assert result.error_code == code
    assert result.response is not None
    assert not result.usage_complete


def test_authored_busy_path_has_no_model_calls(scenario):
    provider = ScriptedProvider()
    result = asyncio.run(PreviewRunner(provider).run(scenario))
    assert result.status == "authored_success"
    assert result.response.text == result.plan.authored_text
    assert result.model_calls == 0


def test_combat_has_no_model_calls_or_speech(scenario_data):
    scenario_data["turn"]["facts"][0]["path"] = "npc.in_combat"
    result = asyncio.run(PreviewRunner(ScriptedProvider()).run(ContextScenario.model_validate(scenario_data)))
    assert result.status == "policy_silence"
    assert result.response.mode == "end"
    assert result.response.text == ""
    assert result.model_calls == 0


def test_budget_failure_has_no_provider_work(scenario_data):
    scenario_data["turn"]["player_input"]["text"] = "x"*4000
    scenario = generating(scenario_data)
    data = scenario.model_dump()
    data["turn"]["player_input"]["text"] = "x"*4000
    result = asyncio.run(PreviewRunner(ScriptedProvider()).run(ContextScenario.model_validate(data)))
    assert result.error_code == "prompt_budget"
    assert result.model_calls == 0


def test_stale_before_generation_does_not_speak(scenario_data):
    scenario = generating(scenario_data)
    state = lambda: CurrentState(dialogue_revision=999, turn_epoch=1)
    result = asyncio.run(PreviewRunner(ScriptedProvider()).run(scenario, current_state=state))
    assert result.status == "cancelled"
    assert result.response is None
    assert result.model_calls == 0


def test_state_change_during_generation_suppresses_output_and_fallback(scenario_data):
    scenario = generating(scenario_data)
    state = [CurrentState(dialogue_revision=7, turn_epoch=1)]
    class ChangingProvider:
        async def generate(self, prompt, timeout_s):
            state[0] = CurrentState(dialogue_revision=8, turn_epoch=2)
            return ProviderReply(content=VALID, model="fake", finish_reason="stop")
    result = asyncio.run(PreviewRunner(ChangingProvider()).run(scenario, current_state=lambda: state[0]))
    assert result.status == "cancelled"
    assert result.response is None


def test_late_result_rejected_and_repair_not_started(scenario_data):
    clock = [0.0]
    class LateProvider:
        async def generate(self, prompt, timeout_s):
            clock[0] = 2.6
            return ProviderReply(content=VALID, model="fake", finish_reason="stop")
    result = asyncio.run(PreviewRunner(LateProvider(), clock=lambda: clock[0]).run(generating(scenario_data)))
    assert result.error_code == "late_completion"
    assert result.model_calls == 1
    assert result.status == "error_fallback"


def test_whole_response_deadline_does_not_emit_late_fallback(scenario_data):
    clock = [0.0]
    class LateProvider:
        async def generate(self, prompt, timeout_s):
            clock[0] = 3.1
            return ProviderReply(content="bad", model="fake", finish_reason="stop")
    result = asyncio.run(PreviewRunner(LateProvider(), clock=lambda: clock[0]).run(generating(scenario_data)))
    assert result.error_code == "response_deadline"
    assert result.response is None


def test_cancellation_stops_provider_before_slot_is_released(scenario_data):
    async def run():
        started, stopped, cancellation = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class SlowProvider:
            async def generate(self, prompt, timeout_s):
                started.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    stopped.set()
        runner = PreviewRunner(SlowProvider())
        task = asyncio.create_task(runner.run(generating(scenario_data), cancellation=cancellation))
        await started.wait()
        cancellation.set()
        result = await task
        assert stopped.is_set()
        assert result.status == "cancelled"
        assert result.response is None
    asyncio.run(run())


def test_single_runner_arbitrates_same_npc_and_global_provider_slot(scenario_data):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        class HoldingProvider:
            async def generate(self, prompt, timeout_s):
                started.set()
                await release.wait()
                return ProviderReply(content=VALID, model="fake", finish_reason="stop")
        runner = PreviewRunner(HoldingProvider())
        scenario = generating(scenario_data)
        first = asyncio.create_task(runner.run(scenario))
        await started.wait()
        second = await runner.run(scenario)
        assert second.error_code == "npc_busy"
        assert second.model_calls == 0
        data = scenario.model_dump()
        data["turn"]["npc_id"] = data["snapshot"]["npc_id"] = "another-npc"
        third = await runner.run(ContextScenario.model_validate(data))
        assert third.error_code == "provider_busy"
        release.set()
        assert (await first).status == "model_success"
    asyncio.run(run())


def test_development_timing_profile_is_explicit(scenario_data):
    limits = PreviewLimits(response_timeout_ms=10000, model_cutoff_ms=9450)
    result = asyncio.run(PreviewRunner(ScriptedProvider(VALID), limits).run(generating(scenario_data)))
    assert result.timing_profile == "development"
    assert result.response_deadline_ms == 10000


def test_slow_provider_cleanup_does_not_delay_caller_or_release_admission(scenario_data):
    async def run():
        cleaning, release = asyncio.Event(), asyncio.Event()
        class SlowCleanupProvider:
            async def generate(self, prompt, timeout_s):
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cleaning.set()
                    await release.wait()
                    return ProviderReply(content=VALID, model="fake", finish_reason="stop")
        limits = PreviewLimits(response_timeout_ms=500, model_cutoff_ms=50)
        runner = PreviewRunner(SlowCleanupProvider(), limits)
        scenario = generating(scenario_data)
        task = asyncio.create_task(runner.run(scenario))
        try:
            await asyncio.wait_for(cleaning.wait(), timeout=1)
            # Cleanup is blocked indefinitely, so caller completion must be independent of it.
            result = await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
            assert result.error_code == "provider_timeout"
            assert result.response is not None
            assert result.elapsed_ms < 500
            still_busy = await runner.run(scenario)
            assert still_busy.status == "busy"
            assert still_busy.model_calls == 0
        finally:
            release.set()
            await task
            await asyncio.sleep(0)
    asyncio.run(run())


# Transient failures (provider_unavailable, provider_timeout) get exactly one retry: the second of the two
# allowed calls, after a delay, inside the model cutoff. provider_timeout used to fall back immediately.
def retrying(**changes):
    return PreviewLimits(**{**dict(response_timeout_ms=3000, model_cutoff_ms=2000, retry_delay_ms=20, retry_transient=True), **changes})


@pytest.mark.parametrize("code", ["provider_unavailable", "provider_timeout"])
def test_transient_failure_is_retried_once_with_second_call(scenario_data, code):
    provider = ScriptedProvider(ProviderFailure(code), VALID)
    result = asyncio.run(PreviewRunner(provider, retrying(retry_delay_ms=100)).run(generating(scenario_data)))
    assert result.status == "model_success" and result.response.text == "Work is tiring."
    assert result.model_calls == 2 and len(provider.calls) == 2
    # The retry delay is counted as model wait (margin allows for Windows' ~16 ms timer granularity).
    assert result.stage_ms["model"] >= 90


@pytest.mark.parametrize("code", ["provider_unavailable", "provider_timeout"])
def test_second_transient_failure_falls_back_without_third_call(scenario_data, code):
    provider = ScriptedProvider(ProviderFailure(code), ProviderFailure(code), VALID)
    result = asyncio.run(PreviewRunner(provider, retrying()).run(generating(scenario_data)))
    assert result.status == "error_fallback" and result.error_code == code
    assert result.model_calls == 2 and len(provider.calls) == 2
    assert result.response.text == result.plan.fallback_text


def test_retried_call_with_invalid_reply_gets_no_repair(scenario_data):
    provider = ScriptedProvider(ProviderFailure("provider_unavailable"), "not json", VALID)
    result = asyncio.run(PreviewRunner(provider, retrying()).run(generating(scenario_data)))
    assert result.status == "error_fallback" and result.model_calls == 2 and len(provider.calls) == 2
    assert result.response.text == result.plan.fallback_text
    assert "invalid_json" in result.violations


def test_repair_call_failure_is_not_retried(scenario_data):
    provider = ScriptedProvider("not json", ProviderFailure("provider_unavailable"), VALID)
    result = asyncio.run(PreviewRunner(provider, retrying()).run(generating(scenario_data)))
    assert result.status == "error_fallback" and result.model_calls == 2 and len(provider.calls) == 2


@pytest.mark.parametrize("code", ["provider_auth", "provider_rate_limited"])
def test_auth_and_rate_limit_never_retry_even_with_budget(scenario_data, code):
    provider = ScriptedProvider(ProviderFailure(code), VALID)
    result = asyncio.run(PreviewRunner(provider, retrying(provider_attempt_ms=500)).run(generating(scenario_data)))
    assert result.error_code == code and result.model_calls == 1 and len(provider.calls) == 1


def test_retry_delay_that_does_not_fit_the_cutoff_skips_the_retry(scenario_data):
    provider = ScriptedProvider(ProviderFailure("provider_unavailable"), VALID)
    limits = retrying(model_cutoff_ms=100, retry_delay_ms=500)
    result = asyncio.run(PreviewRunner(provider, limits).run(generating(scenario_data)))
    assert result.status == "error_fallback" and result.error_code == "provider_unavailable"
    assert result.model_calls == 1 and len(provider.calls) == 1


class HangingProvider:
    def __init__(self, *, then=None):
        self.limits, self.then = [], then

    async def generate(self, prompt, timeout_s):
        self.limits.append(timeout_s)
        if len(self.limits) == 1 or self.then is None:
            await asyncio.sleep(30)
        return ProviderReply(content=self.then, model="fake", finish_reason="stop", input_tokens=1, output_tokens=1)


def test_slow_call_is_abandoned_at_attempt_limit_then_retried_within_cutoff(scenario_data):
    provider = HangingProvider(then=VALID)
    limits = retrying(provider_attempt_ms=150, model_cutoff_ms=1000, retry_delay_ms=30)
    started = time.monotonic()
    result = asyncio.run(PreviewRunner(provider, limits).run(generating(scenario_data)))
    assert result.status == "model_success" and result.model_calls == 2
    assert provider.limits[0] == pytest.approx(0.15, abs=0.01)
    assert time.monotonic() - started < 1.0


def test_two_slow_calls_stay_inside_the_cutoff_and_use_fallback(scenario_data):
    provider = HangingProvider()
    limits = retrying(provider_attempt_ms=200, model_cutoff_ms=350, retry_delay_ms=20)
    started = time.monotonic()
    result = asyncio.run(PreviewRunner(provider, limits).run(generating(scenario_data)))
    elapsed = time.monotonic() - started
    assert result.status == "error_fallback" and result.error_code == "provider_timeout"
    assert result.model_calls == 2 and len(provider.limits) == 2
    assert provider.limits[1] < 0.16  # clipped to what the 0.35 s cutoff has left, not the full 0.2 s attempt limit
    assert elapsed < 0.55
    assert result.response.text == result.plan.fallback_text


def test_stage_timings_cover_prompt_model_and_validation(scenario_data):
    result = asyncio.run(PreviewRunner(ScriptedProvider(VALID)).run(generating(scenario_data)))
    assert {"prompt", "model", "validation"} <= set(result.stage_ms)
    assert all(value >= 0 for value in result.stage_ms.values())
def test_stage_timings_separate_generation_and_repair(scenario_data):
    now = [0.0]
    class Provider(ScriptedProvider):
        async def generate(self, prompt, timeout_s):
            now[0] += .05 if not self.calls else .03
            return await super().generate(prompt, timeout_s)
    provider = Provider("bad json", VALID)
    result = asyncio.run(PreviewRunner(provider, clock=lambda: now[0]).run(generating(scenario_data)))
    assert result.stage_ms["generation"] == pytest.approx(50)
    assert result.stage_ms["repair"] == pytest.approx(30)
    assert result.stage_ms["retrieval"] == 0
    assert result.elapsed_ms == pytest.approx(80)
    assert result.status == "repair_success" and result.model_calls == 2


def test_provider_timeout_retains_stage_time(scenario_data):
    now = [0.0]
    class Provider:
        async def generate(self, prompt, timeout_s):
            now[0] += .2
            raise ProviderFailure("provider_timeout")
    result = asyncio.run(PreviewRunner(Provider(), clock=lambda: now[0]).run(generating(scenario_data)))
    assert result.stage_ms["generation"] == pytest.approx(200)
    assert result.status == "error_fallback" and result.model_calls == 1


def test_prompt_budget_failure_retains_construction_time(scenario_data, monkeypatch):
    from npc.generation.prompt import PromptTooLarge
    now = [0.0]
    def oversized(*args, **kwargs):
        now[0] += .05
        raise PromptTooLarge("required context too large")
    monkeypatch.setattr("npc.runtime.preview.build_prompt", oversized)
    provider = ScriptedProvider(VALID)
    result = asyncio.run(PreviewRunner(provider, clock=lambda: now[0]).run(generating(scenario_data)))
    assert result.error_code == "prompt_budget" and result.model_calls == 0
    assert result.stage_ms["prompt"] == pytest.approx(50)
    assert result.stage_ms["generation"] == 0
