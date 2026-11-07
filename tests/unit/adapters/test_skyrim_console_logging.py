"""Console visibility: model input/output, verbose gating, start-up banner, frame statistics and colours."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from npc.adapters.skyrim import console, diagnostics
from npc.adapters.skyrim.assets import png_from_bgra
from npc.adapters.skyrim.banner import banner_lines, cv_state
from npc.adapters.skyrim.bridge import Bridge
from npc.adapters.skyrim.console import colour_line
from npc.adapters.skyrim.diagnostics import LOGGER, configure, set_verbose
from npc.adapters.skyrim.frame_stats import frame_stats
from npc.adapters.skyrim.model_log import LoggingProvider, provider_label
from npc.adapters.skyrim.protocol import Settings
from npc.contracts.dialogue import ChatMessage, PromptPackage, ProviderReply
from npc.generation.provider import OfflineProvider, ProviderFailure

SYSTEM_SECRET = "SYSTEM-PROMPT-SECRET-TEXT"
DATA_SECRET = "PLAYER-DATA-SECRET-TEXT"


@pytest.fixture
def logs(tmp_path):
    """Returns start(): configure logging inside the test body, after capsys has taken over stdout."""
    original = list(LOGGER.handlers), LOGGER.level, LOGGER.propagate
    path = tmp_path / "anima.log"

    def start():
        configure(str(path))
        return path
    yield start
    set_verbose(False)
    for handler in tuple(LOGGER.handlers):
        if handler not in original[0]:
            LOGGER.removeHandler(handler)
            handler.close()
    LOGGER.setLevel(original[1])
    LOGGER.propagate = original[2]


def prompt():
    return PromptPackage(messages=(ChatMessage(role="system", content=SYSTEM_SECRET),
                                   ChatMessage(role="user", content=DATA_SECRET)), budget_bytes=2048, history_count=2)


class Scripted:
    def __init__(self, reply=None, failure=None):
        self.reply, self.failure = reply, failure

    async def generate(self, package, timeout_s):
        if self.failure:
            raise self.failure
        return self.reply


REPLY = ProviderReply(content='{"mode":"speech","text":"Hello."}', model="m1", finish_reason="stop", input_tokens=120, output_tokens=9)


def lines(capsys):
    return capsys.readouterr().out.splitlines()


def test_logging_provider_logs_input_and_output_without_changing_the_reply(logs, capsys):
    path = logs()
    wrapper = LoggingProvider(Scripted(REPLY))
    wrapper.note(npc="Lydia", persona_source="derived", turn_type="new", move="answer", scene_items=4)
    assert asyncio.run(wrapper.generate(prompt(), 2.0)) is REPLY
    out = lines(capsys)
    assert len(out) == 2
    assert "MODEL_INPUT" in out[0]
    for fragment in ('npc="Lydia"', 'persona_source="derived"', 'turn_type="new"', 'move="answer"', 'messages="2"',
                     'evidence="0"', 'history="2"', 'scene_items="4"', f'prompt_bytes="{len(SYSTEM_SECRET) + len(DATA_SECRET)}"'):
        assert fragment in out[0]
    for fragment in ("MODEL_OUTPUT", 'status="ok"', 'finish_reason="stop"', 'input_tokens="120"', 'output_tokens="9"', "latency_ms="):
        assert fragment in out[1]
    assert '\\"text\\":\\"Hello.\\"' in out[1]  # the raw JSON reply, escaped onto one line


def test_logging_provider_logs_failures_and_reraises(logs, capsys):
    path = logs()
    wrapper = LoggingProvider(Scripted(failure=ProviderFailure("provider_timeout")))
    with pytest.raises(ProviderFailure):
        asyncio.run(wrapper.generate(prompt(), 1.0))
    out = lines(capsys)
    assert 'status="failed"' in out[1] and 'error="provider_timeout"' in out[1]
    assert colour_line("10:00:00 [ANIMA] " + out[1].split("[ANIMA] ", 1)[1], "ANIMA").startswith(console.RED)


def test_logging_provider_delegates_close_and_label():
    inner = OfflineProvider()
    wrapper = LoggingProvider(inner)
    assert provider_label(wrapper) == ("OfflineProvider", "offline-demo")
    assert wrapper.inner is inner


def test_prompt_text_is_logged_only_with_verbose_and_in_console_and_file_alike(logs, capsys):
    path = logs()
    wrapper = LoggingProvider(Scripted(REPLY))
    asyncio.run(wrapper.generate(prompt(), 2.0))
    console_text, file_text = capsys.readouterr().out, path.read_text()
    assert SYSTEM_SECRET not in console_text + file_text and DATA_SECRET not in console_text + file_text
    assert console_text == file_text and "MODEL_PROMPT" not in file_text
    set_verbose(True)
    asyncio.run(wrapper.generate(prompt(), 2.0))
    console_text, file_text = capsys.readouterr().out, path.read_text()
    assert SYSTEM_SECRET in console_text and DATA_SECRET in console_text
    assert SYSTEM_SECRET in file_text and DATA_SECRET in file_text
    assert all("MODEL_PROMPT" not in line or "role=" in line for line in console_text.splitlines())


def test_verbose_prompt_is_truncated(logs, capsys):
    path = logs()
    set_verbose(True)
    big = PromptPackage(messages=(ChatMessage(role="system", content="s" * 20000), ChatMessage(role="user", content="u")), budget_bytes=2048)
    asyncio.run(LoggingProvider(Scripted(REPLY)).generate(big, 2.0))
    prompt_line = next(line for line in lines(capsys) if "MODEL_PROMPT" in line)
    assert 6000 <= len(prompt_line) < 6300


def test_validation_repair_and_fallback_lines(logs, capsys):
    path = logs()
    knowledge = SimpleNamespace(status="ok")
    base = dict(model_calls=2, evidence_used=("e1",), evidence_omitted=0, knowledge=knowledge)
    Bridge._log_preview(SimpleNamespace(status="repair_success", violations=("too_long",), **base))
    Bridge._log_preview(SimpleNamespace(status="error_fallback", violations=("too_long", "bad_json"), error_code="invalid_candidate", **base))
    Bridge._log_preview(SimpleNamespace(status="authored_success", violations=(), model_calls=0, knowledge=None))
    text = capsys.readouterr().out
    assert 'REPAIR violations="too_long"' in text
    assert 'VALIDATION result="accepted" violations="too_long"' in text
    assert 'VALIDATION result="rejected" violations="too_long,bad_json"' in text
    assert 'FALLBACK reason="invalid_candidate"' in text
    assert 'RETRIEVAL status="ok" evidence="e1"' in text
    assert text.count("RETRIEVAL") == 2  # none for the authored turn that made no model call


def settings(**extra):
    return Settings.model_validate({"game_directory": "x", **extra})


def mock(handler):
    return httpx.MockTransport(handler)


def test_banner_cv_states_disabled_unreachable_ready():
    assert cv_state(None) == "disabled: cv_url not set"

    def refuse(request):
        raise httpx.ConnectError("refused")
    assert cv_state("http://127.0.0.1:8000", transport=mock(refuse)) == "unreachable: http://127.0.0.1:8000"
    ready = mock(lambda request: httpx.Response(200, json={"ready": True, "vlm": {"available": True, "detail": "qwen2.5vl:3b"}}))
    assert cv_state("http://127.0.0.1:8000", transport=ready) == "ready: qwen2.5vl:3b"
    down = mock(lambda request: httpx.Response(200, json={"ready": False, "vlm": {"available": False, "detail": "ollama unreachable"}}))
    assert cv_state("http://127.0.0.1:8000", transport=down).startswith("service up, model not ready")


def test_banner_lists_every_component():
    rows = dict(banner_lines(settings(rag_config="configs/rag/skyrim.yaml", capture_screenshots=False, memory_scope="session"),
                             provider=("chat_completions", "gemini-x"), stt="Whisper base.en CPU INT8",
                             tts="Piper, folder=P, models=12, audio_output=system", cv="disabled: cv_url not set"))
    assert list(rows) == ["LLM", "STT", "TTS", "RAG", "MEMORY", "CV", "SCREENSHOTS"]
    assert "model=gemini-x" in rows["LLM"] and "offline.yaml" in rows["LLM"]
    assert "models=12" in rows["TTS"] and rows["RAG"] == "configured: skyrim.yaml"
    assert rows["MEMORY"].startswith("scope=session") and rows["CV"] == "disabled: cv_url not set"
    assert rows["SCREENSHOTS"].startswith("capture off")
    plain = dict(banner_lines(settings(), cv="x"))
    assert plain["RAG"] == "not configured" and plain["SCREENSHOTS"].startswith("capture on")


def bgra(width, height, value):
    return png_from_bgra(width, height, bytes([value, value, value, 255]) * (width * height))


def test_black_frame_is_detected_and_bright_frame_is_not():
    black = frame_stats(bgra(64, 48, 0))
    assert (black["width"], black["height"]) == (64, 48)
    assert black["black_fraction"] == 1.0 and black["mean_brightness"] == 0.0
    bright = frame_stats(bgra(64, 48, 200))
    assert bright["black_fraction"] == 0.0 and bright["mean_brightness"] == 200.0
    assert frame_stats(b"not a png") is None


def test_screenshot_logging_warns_on_mostly_black_and_stays_quiet_on_normal_frames(logs, capsys, tmp_path):
    path = logs()
    bridge = SimpleNamespace()
    dark, light = tmp_path / "dark.png", tmp_path / "light.png"
    dark.write_bytes(bgra(32, 32, 0))
    light.write_bytes(bgra(32, 32, 120))
    asyncio.run(Bridge._log_screenshot(bridge, str(dark)))
    asyncio.run(Bridge._log_screenshot(bridge, str(light)))
    out = lines(capsys)
    assert 'SCREENSHOT saved=' in out[0] and 'size="32x32"' in out[0] and 'black_fraction="1.0"' in out[0]
    assert 'SCREENSHOT warning="mostly_black"' in out[1] and "windowed or borderless" in out[1]
    assert len(out) == 3 and "warning" not in out[2] and 'mean_brightness="120.0"' in out[2]


def test_new_event_kinds_have_the_requested_colours():
    def start(kind, extra=""):
        return colour_line(f'10:00:00 [ANIMA] {kind} {extra} a="1"', "ANIMA")
    assert start("PLAYER_INPUT").startswith(console.INPUT_CYAN) and "96m" in console.INPUT_CYAN
    assert start("MODEL_INPUT").startswith(console.BLUE)
    assert start("MODEL_OUTPUT", 'status="ok"').startswith(console.GREEN)
    assert start("MODEL_OUTPUT", 'status="failed"').startswith(console.RED)
    for kind in ('CV_REQUEST url="http://127.0.0.1:8000"', "CV_RESULT", "CV_CONTEXT", "SCREENSHOT"):
        assert start(kind).startswith(console.MAGENTA)
    assert start("SCREENSHOT", 'warning="mostly_black"').startswith(console.YELLOW)
    for kind in ("CV_UNAVAILABLE", "REPAIR", "FALLBACK"):
        assert start(kind).startswith(console.YELLOW)
    assert start("SCREENSHOT_FAILED").startswith(console.RED)
    # Words in dialogue never turn a line red or yellow.
    assert colour_line('10:00:00 [ANIMA] PLAYER_INPUT text="ERROR WARNING FAILED"', "ANIMA").startswith(console.INPUT_CYAN)
    assert colour_line('10:00:00 [ANIMA] MODEL_OUTPUT status="ok" raw="Traceback ERROR"', "ANIMA").startswith(console.GREEN)


def test_no_color_output_is_plain():
    assert colour_line("10:00:00 [ANIMA] MODEL_OUTPUT status=\"ok\"", "ANIMA", color=False) == "10:00:00 [ANIMA] MODEL_OUTPUT status=\"ok\""
    assert colour_line("[x] [error] boom", "PLUGIN", color=False) == "[PLUGIN] [x] [error] boom"


def test_setup_without_colour_keeps_the_plain_formatter(logs, monkeypatch):
    path = logs()
    monkeypatch.setattr(console, "plugin_log_path", lambda: None)
    console.setup(LOGGER, color=False)
    assert not any(isinstance(handler.formatter, console.ColourFormatter) for handler in LOGGER.handlers)
    console.setup(LOGGER, color=True)
    assert any(isinstance(handler.formatter, console.ColourFormatter) for handler in LOGGER.handlers)


def test_bridge_logs_game_context_screenshot_cv_and_player_input(logs, capsys, tmp_path):
    from datetime import datetime, timezone
    from npc.generation.provider import OfflineProvider
    from npc.runtime.preview import PreviewLimits, PreviewRunner
    from npc.vision import CVFailure, VisualContext
    from tests.unit.adapters.test_skyrim_connector import event, settings as bridge_settings, speech as make_speech
    path = logs()

    class Vision:
        def __init__(self):
            self.fail = False

        async def analyze(self, *args):
            if self.fail:
                raise CVFailure("cv_timeout")
            return VisualContext(id="f", captured_at=datetime.now(timezone.utc), environment="A dim room",
                                 characters=("a guard",), objects=("table", "lamp"), uncertainties=("dark",), vlm_model="qwen2.5vl:3b")

    async def run():
        vision = Vision()
        config = bridge_settings(tmp_path, pipeline_mode="preview", attach_visual_reference=True, cv_url="http://127.0.0.1:8000").model_copy(update={"capture_screenshots": True})
        bridge = Bridge(config, root=tmp_path, cv_client=vision, capture=lambda _: bgra(8, 8, 0),
                        runner=PreviewRunner(LoggingProvider(OfflineProvider()), PreviewLimits(max_input_bytes=12000)))
        try:
            await bridge.game_event(event())
            await bridge.session.context_task
            await bridge.player_input(make_speech(source="stt", confidence=0.87).model_copy(update={"text": "Who are you, stranger, and what do you do here?"}), stt_ms=412)
            await bridge.session.turn_task
            vision.fail = True
            await bridge.publish_context(bridge.session, "context_updated")
        finally:
            await bridge.close()
    asyncio.run(run())
    text = capsys.readouterr().out
    assert text == path.read_text()
    for fragment in ("GAME_CONTEXT", 'npc="Test NPC"', 'persona_source="default_unknown"', "SCREENSHOT saved=", 'warning="mostly_black"',
                     'CV_REQUEST url="http://127.0.0.1:8000"', 'CV_RESULT environment="A dim room" characters="1" objects="2" uncertainties="1" model="qwen2.5vl:3b"',
                     'CV_CONTEXT used="attached_to_prompt"', 'PLAYER_INPUT', 'source="speech" confidence="0.87" transcript_ms="412"',
                     'MODEL_INPUT', 'persona_source="default_unknown"', 'MODEL_OUTPUT', 'CV_UNAVAILABLE reason="cv_timeout"', "VALIDATION"):
        assert fragment in text, fragment
