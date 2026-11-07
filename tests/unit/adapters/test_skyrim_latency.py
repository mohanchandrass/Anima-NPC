"""I24 WP-D: no screenshot wait without CV, TURN_TIMING for every turn, shared provider client, runner profile."""

import asyncio
import logging
import threading
import time

import httpx
import pytest

from npc.adapters.skyrim.bridge import TIMING_STAGES, Bridge, ConnectionError
from npc.adapters.skyrim.protocol import ActorData, Form, GameEvent, GameSnapshot, Settings, SpeechInput
from npc.generation.provider import ChatCompletionsProvider, OfflineProvider, ProviderConfig
from npc.runtime.preview import PreviewRunner


def form(identifier="00013478", name="Test NPC"):
    return Form(form_id=identifier, local_id=identifier, name=name, plugin="Skyrim.esm")


def event(kind="start", sequence=1, **changes):
    actor = ActorData(reference=form(), base=form("0001347B"), in_combat=False, dead=False,
                      weapon_drawn=False, sees_player=False)
    player = actor.model_copy(update={"reference": form("00000014", "Player")})
    snapshot = GameSnapshot(npc=actor, player=player, hour=12.0, game_days=2.0, interior=True,
                            location=form("00016772", "Whiterun"))
    return GameEvent(kind=kind, sequence=sequence, state_revision=1, event_id=f"event-{sequence}",
                     session_id="session-a", game_epoch="load-a", game_pid=123,
                     captured_at_ms=int(time.time() * 1000),
                     snapshot=snapshot if kind in {"start", "snapshot"} else None).model_copy(update=changes)


def settings(tmp_path, **changes):
    return Settings(game_directory=str(tmp_path / "game"), output_directory=str(tmp_path / "output"),
                    audio_root=str(tmp_path / "audio"), pipeline_mode="preview",
                    **{"capture_screenshots": False, **changes})


def say():
    return SpeechInput(session_id="session-a", input_id="input-a", text="How is your work going?")


@pytest.fixture
def lines(caplog, monkeypatch):
    from npc.adapters.skyrim.diagnostics import LOGGER
    monkeypatch.setattr(LOGGER, "propagate", False)
    monkeypatch.setattr(LOGGER, "handlers", [caplog.handler])
    caplog.set_level(logging.INFO, logger=LOGGER.name)
    return lambda prefix: [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


class RecordingRunner:
    """Captures the scenario and answers like the offline provider; optionally fails."""

    def __init__(self, fail=None):
        self.scenarios, self.inner, self.fail = [], PreviewRunner(OfflineProvider()), fail

    async def run(self, scenario, **kwargs):
        self.scenarios.append(scenario)
        if self.fail:
            await asyncio.sleep(.01)
            raise self.fail
        return await self.inner.run(scenario, **kwargs)


def test_turn_does_not_wait_for_or_need_a_screenshot_when_cv_is_off(tmp_path):
    release = threading.Event()
    started = threading.Event()

    def blocked_capture(pid):
        started.set()
        release.wait(10)
        return b""

    async def run():
        runner = RecordingRunner()
        bridge = Bridge(settings(tmp_path, capture_screenshots=True), root=tmp_path, runner=runner, capture=blocked_capture)
        assert bridge.cv_client is None
        try:
            await bridge.game_event(event())
            await asyncio.to_thread(started.wait, 5)  # the session-open capture is in flight and stuck
            assert bridge.session.context_task.done()
            assert not bridge.session.visual_task.done()
            await bridge.player_input(say())
            await asyncio.wait_for(bridge.session.turn_task, 3)  # would hang here if the turn waited for it
            assert bridge.session.delivery["text"]
            payload = runner.scenarios[0].adapter_context.payload
            assert payload["screenshot"] is None and payload["image_description"] is None
        finally:
            release.set()
            await bridge.close()
    asyncio.run(run())


def test_turn_publishes_context_but_does_not_wait_for_cv_when_connected(tmp_path):
    release = threading.Event()

    class Vision:
        async def analyze(self, image, media_type):
            raise AssertionError("not reached")

    def slow_capture(pid):
        release.wait(10)
        raise OSError("no frame")

    async def run():
        bridge = Bridge(settings(tmp_path, capture_screenshots=True), root=tmp_path, runner=RecordingRunner(),
                        capture=slow_capture, cv_client=Vision())
        try:
            await bridge.game_event(event())
            await bridge.player_input(say())
            await asyncio.sleep(.2)
            assert bridge.session.context_task.done()
            assert bridge.session.turn_task.done()  # optional CV never blocks the turn
            assert not bridge.session.visual_task.done()
            release.set()
            await asyncio.wait_for(bridge.session.turn_task, 3)
            assert bridge.session.delivery["text"]
        finally:
            release.set()
            await bridge.close()
    asyncio.run(run())


def fields(line):
    return dict(part.split("=", 1) for part in line.split(" ") if "=" in part)


def test_turn_timing_logs_every_stage_for_a_delivered_turn(tmp_path, lines):
    async def run():
        bridge = Bridge(settings(tmp_path), root=tmp_path, runner=PreviewRunner(OfflineProvider()))
        try:
            await bridge.game_event(event())
            await bridge.session.context_task
            await bridge.player_input(say())
            await bridge.session.turn_task
            assert lines("TURN_TIMING") == []  # still waiting for the game to start speech
            command = bridge.command("session-a")
            await bridge.game_event(event("delivery", 2, delivery_id=command["delivery_id"], status="started"))
            await bridge.game_event(event("delivery", 3, delivery_id=command["delivery_id"], status="completed"))
        finally:
            await bridge.close()
    asyncio.run(run())
    timing = lines("TURN_TIMING")
    assert len(timing) == 1  # one line per turn, not repeated by the later completed receipt
    values = fields(timing[0])
    assert values["outcome"] == '"started"' and values["request"] == '"input-a"'
    for stage in TIMING_STAGES:
        assert stage in values, stage
    # Stages this turn went through hold numbers; text input has no STT, no TTS server, so those read none/near zero.
    for stage in ("context_wait_ms", "prompt_ms", "model_ms", "validation_ms", "pipeline_ms", "staging_ms", "native_start_ms"):
        assert values[stage] != '"none"', stage
    assert values["stt_ms"] == '"none"'
    assert float(values["total_ms"].strip('"')) >= 0


def test_turn_timing_is_logged_for_a_failed_turn(tmp_path, lines):
    async def run():
        bridge = Bridge(settings(tmp_path), root=tmp_path, runner=RecordingRunner(fail=ConnectionError("model exploded")))
        try:
            await bridge.game_event(event())
            await bridge.session.context_task
            await bridge.player_input(say())
            await bridge.session.turn_task
        finally:
            await bridge.close()
    asyncio.run(run())
    assert len(lines("TURN_FAILED")) == 1
    timing = lines("TURN_TIMING")
    assert len(timing) == 1
    values = fields(timing[0])
    assert values["outcome"] == '"failed"'
    assert all(stage in values for stage in TIMING_STAGES)
    assert values["pipeline_ms"] != '"none"'  # the stage that failed is still measured
    assert values["tts_ms"] == '"none"' and values["staging_ms"] == '"none"' and values["native_start_ms"] == '"none"'


def test_turn_timing_is_logged_when_an_unexpected_error_kills_the_turn(tmp_path, lines):
    async def run():
        bridge = Bridge(settings(tmp_path), root=tmp_path, runner=RecordingRunner(fail=RuntimeError("bug")))
        try:
            await bridge.game_event(event())
            await bridge.session.context_task
            await bridge.player_input(say())
            await asyncio.gather(bridge.session.turn_task, return_exceptions=True)
        finally:
            await bridge.close()
    asyncio.run(run())
    assert fields(lines("TURN_TIMING")[0])["outcome"] == '"failed"'


def test_interrupted_turn_logs_cancelled_timing_once(tmp_path, lines):
    async def run():
        bridge = Bridge(settings(tmp_path), root=tmp_path, runner=PreviewRunner(OfflineProvider()))
        try:
            await bridge.game_event(event())
            await bridge.session.context_task
            await bridge.player_input(say())
            await bridge.session.turn_task  # staged, never started
            await bridge.game_event(event("end", 2))
        finally:
            await bridge.close()
    asyncio.run(run())
    timing = lines("TURN_TIMING")
    assert len(timing) == 1 and fields(timing[0])["outcome"] == '"cancelled"'
    assert fields(timing[0])["native_start_ms"] == '"none"'


def test_shared_provider_client_is_closed_when_the_bridge_shuts_down(tmp_path):
    reply = {"model": "m", "choices": [{"message": {"content": '{"mode":"speech","text":"Work keeps me occupied.","cue":null,"evidence_ids":[]}'},
                                        "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
    requests = []
    provider = ChatCompletionsProvider(
        ProviderConfig(base_url="https://example.test/v1", model="m", api_key="PRIVATE_TEST_KEY"),
        transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, json=reply)))

    async def run():
        bridge = Bridge(settings(tmp_path), root=tmp_path, runner=PreviewRunner(provider))
        await bridge.game_event(event())
        await bridge.session.context_task
        await bridge.player_input(say())
        await bridge.session.turn_task
        assert len(requests) == 1
        client = provider._client
        assert client is not None and not client.is_closed
        await bridge.close()
        assert client.is_closed and provider._client is None
    asyncio.run(run())


def test_close_still_closes_bridge_client_if_the_provider_close_fails(tmp_path):
    class BrokenRunner:
        async def aclose(self):
            raise RuntimeError("close failed")

    async def run():
        bridge = Bridge(settings(tmp_path), root=tmp_path, runner=BrokenRunner())
        with pytest.raises(RuntimeError):
            await bridge.close()
        assert bridge.client.is_closed
    asyncio.run(run())


def test_connector_runner_profile_cuts_off_a_slow_call_and_stays_inside_the_budget(tmp_path):
    from npc.adapters.skyrim.__main__ import build_runner
    profile = tmp_path / "configs"
    profile.mkdir()
    (profile / "offline.yaml").write_text("provider: offline\n", encoding="utf-8")
    default = build_runner(Settings(game_directory="g", provider_profile="configs/offline.yaml"), tmp_path)
    limits = default.limits
    assert limits.provider_attempt_ms == 4000
    assert limits.model_cutoff_ms == 8250  # two 4 s calls plus the 250 ms retry delay, not the old 29 s
    assert limits.response_timeout_ms == 30000
    tight = build_runner(Settings(game_directory="g", provider_profile="configs/offline.yaml", timeout_s=5, model_cutoff_s=3), tmp_path)
    assert tight.limits.model_cutoff_ms == 4000  # still leaves 1 s of the 5 s response budget
    configured = build_runner(Settings(game_directory="g", provider_profile="configs/offline.yaml", model_cutoff_s=1), tmp_path)
    assert (configured.limits.provider_attempt_ms, configured.limits.model_cutoff_ms) == (1000, 2250)
