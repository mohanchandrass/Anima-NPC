"""I24 WP-A2 stage 1: Piper voice selection, fallbacks, timeouts and cleanup. Offline; uses a fake piper process."""

import asyncio
import io
import json
import logging
import struct
import sys
import textwrap
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from npc.adapters.skyrim.protocol import Settings
from npc.adapters.skyrim.speech import PiperSpeech, SpeechFailure, SynthesisInput, create_speech_app
from npc.adapters.skyrim.voice_types import VoiceTypeResolver, parse_voice_types

FAKE_PIPER = textwrap.dedent('''
    import os, sys, time, wave
    mode = os.environ.get("FAKE_PIPER_MODE", "ok")
    log = os.environ["FAKE_PIPER_LOG"]
    def note(text):
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(text + "\\n")
    note("start")
    model = ""
    for line in sys.stdin:
        line = line.rstrip("\\r\\n")
        if line.startswith("load_model "):
            model = os.path.basename(line[len("load_model "):])[:-5]
            note("load " + model)
            print("Model loaded", flush=True)
        elif line.startswith("synthesize "):
            note("synth " + model + " | " + line[len("synthesize "):])
            if mode == "hang" or (mode == "hang_voice" and model == "maleguard"):
                time.sleep(60)
            if mode == "crash_voice" and model == "maleguard":
                sys.exit(3)
            with wave.open("out.wav", "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"" if mode == "emptywav" else b"\\x01\\x00" * 1600)
            print("Enter command: .\\\\out.wav", flush=True)
            print("Synthesis complete", flush=True)
        elif line == "exit":
            break
''')


def wav(seconds=.1, marker=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<h", marker) * int(seconds * 16000))
    return out.getvalue()


def vtyp_plugin(path: Path, voices: dict[int, str], *, extra_group=True):
    """A minimal plugin: a TES4 record, an unrelated group, and a VTYP group with editor IDs."""
    def record(kind, form, data):
        return struct.pack("<4sIIIII", kind, len(data), 0, form, 0, 0) + data
    body = b"".join(record(b"VTYP", form, b"EDID" + struct.pack("<H", len(name) + 1) + name.encode() + b"\0")
                    for form, name in voices.items())
    group = lambda label, content: struct.pack("<4sI4sIII", b"GRUP", 24 + len(content), label, 0, 0, 0) + content
    data = record(b"TES4", 0, b"HEDR" + struct.pack("<H", 4) + b"\0\0\0\0")
    if extra_group:
        data += group(b"NPC_", record(b"NPC_", 0x13478, b"EDID\x02\x00A\0"))
    data += group(b"VTYP", body)
    path.write_bytes(data)


MODELS = ["maleguard", "malenord", "femalenord", "femaleeventoned", "maleeventoned", "cruniquealduin"]


class Rig:
    def __init__(self, tmp_path, monkeypatch, models=MODELS, mode="ok", voices=None, **kwargs):
        self.tmp = tmp_path
        self.folder = tmp_path / "piper"
        low = self.folder / "models/skyrim/low"
        low.mkdir(parents=True)
        for name in models:
            (low / f"{name}.onnx").write_bytes(b"x")
            (low / f"{name}.onnx.json").write_text("{}")
        self.fake = tmp_path / "fake_piper.py"
        self.fake.write_text(FAKE_PIPER)
        self.log = tmp_path / "fake.log"
        monkeypatch.setenv("FAKE_PIPER_LOG", str(self.log))
        monkeypatch.setenv("FAKE_PIPER_MODE", mode)
        self.data = tmp_path / "game/Data"
        self.data.mkdir(parents=True)
        vtyp_plugin(self.data / "Skyrim.esm", voices if voices is not None else
                    {0x13AE6: "MaleGuard", 0x13AE7: "MaleEvenToned", 0x13AEA: "FemaleNord", 0x99999: "FemaleNoModelVoice"})
        self.processes, self.sapi = [], []

        async def launch(*args, **kw):
            process = await asyncio.create_subprocess_exec(*args, **kw)
            self.processes.append(process)
            return process

        async def sapi_launch(*args, **kw):
            request = json.loads(Path(args[-1]).read_text())
            Path(request["output"]).write_bytes(wav(marker=7))
            self.sapi.append(request["text"])

            class Process:
                returncode = 0
                async def communicate(self): return b'{"status":"ok"}', None
            return Process()

        self.engine = PiperSpeech(tmp_path / "speech", tmp_path, piper_directory=self.folder,
                                  voice_types=VoiceTypeResolver(self.data), command=[sys.executable, str(self.fake)],
                                  piper_launch=launch, launch=sapi_launch, **kwargs)

    def request(self, voice="skyrim", local="00013AE6", sex=0, text="Well met, traveler.", plugin="Skyrim.esm", request_id="r1"):
        voice = {"plugin": plugin, "local_id": local, "form_id": local, "name": ""} if voice else None
        return SynthesisInput(request_id=request_id, text=text, voice=voice, sex=sex)

    def lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def synths(self):
        return [line for line in self.lines() if line.startswith("synth ")]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


@pytest.fixture
def events(caplog, monkeypatch):
    from npc.adapters.skyrim.diagnostics import LOGGER
    monkeypatch.setattr(LOGGER, "propagate", False)
    monkeypatch.setattr(LOGGER, "handlers", [caplog.handler])
    caplog.set_level(logging.INFO, logger=LOGGER.name)
    return lambda prefix: [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def run(engine, request):
    async def go():
        try:
            return await engine.synthesize(request)
        finally:
            await engine.close()
    return asyncio.run(go())


def test_voice_type_record_selects_its_piper_model_and_logs_engine_and_voice(rig, events):
    raw = run(rig.engine, rig.request(local="00013AE6"))
    assert raw[:4] == b"RIFF" and rig.sapi == []
    assert rig.synths() == ["synth maleguard | Well met, traveler."]
    ready = events("TTS_READY")
    assert 'engine="Piper"' in ready[0] and 'voice="maleguard"' in ready[0] and 'selected_by="voice_type"' in ready[0]


@pytest.mark.parametrize("local,sex,expected", [
    ("00099999", 1, "femalenord"),    # voice type known, but its model is not installed: same-sex default
    ("00099999", 0, "malenord"),
    ("00055555", 1, "femalenord"),    # voice type not in the plugin
    ("00013AE6", 1, "maleguard"),     # the voice type wins over the sex when its model exists
])
def test_missing_voice_model_falls_back_to_a_model_of_the_same_sex(tmp_path, monkeypatch, events, local, sex, expected):
    rig = Rig(tmp_path, monkeypatch)
    run(rig.engine, rig.request(local=local, sex=sex))
    assert rig.synths()[0].split(" | ")[0] == f"synth {expected}"
    assert rig.sapi == []


def test_sex_default_without_preferred_model_uses_first_sorted_match(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, models=["femalecommander", "femalenord", "maleguard", "malebandit"])
    run(rig.engine, rig.request(local="00055555", sex=0))
    assert rig.synths()[0].startswith("synth malebandit")
    (tmp_path / "b").mkdir()
    other = Rig(tmp_path / "b", monkeypatch, models=["femalecommander", "maleguard"])
    run(other.engine, other.request(local="00055555", sex=1))
    assert other.synths()[0].startswith("synth femalecommander")


@pytest.mark.parametrize("sex", [None, -1, 2])
def test_unknown_voice_and_unknown_sex_use_windows_speech(rig, events, sex):
    raw = run(rig.engine, rig.request(local="00055555", sex=sex))
    assert raw == wav(marker=7) and rig.sapi == ["Well met, traveler."] and rig.lines() == []
    ready = events("TTS_READY")
    assert 'engine="Windows SAPI"' in ready[0] and 'voice="system default"' in ready[0]


def test_no_voice_information_at_all_uses_a_sex_model_then_windows(rig):
    run(rig.engine, rig.request(voice=None, sex=1))
    assert rig.synths()[0].startswith("synth femalenord")


def test_missing_piper_folder_or_models_uses_windows_speech(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, models=[])
    assert run(rig.engine, rig.request()) == wav(marker=7)
    assert rig.lines() == []
    assert rig.engine.tts_engine == "Windows SAPI"


def test_model_without_its_json_sidecar_is_not_offered(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, models=["femalenord"])
    (rig.folder / "models/skyrim/low/maleguard.onnx").write_bytes(b"x")  # no .json
    assert rig.engine.available_models() == {"femalenord"}


def test_failing_voice_model_falls_to_the_same_sex_model_then_windows(tmp_path, monkeypatch, events):
    rig = Rig(tmp_path, monkeypatch, mode="crash_voice")
    raw = run(rig.engine, rig.request(local="00013AE6", sex=0))
    assert [line.split(" | ")[0] for line in rig.synths()] == ["synth maleguard", "synth malenord"]
    assert raw[:4] == b"RIFF" and rig.sapi == []
    assert 'voice="malenord"' in events("TTS_READY")[0] and 'selected_by="same_sex"' in events("TTS_READY")[0]
    assert 'reason="speech_engine_failed"' in events("TTS_FALLBACK")[0]
    # Same failure with no same-sex alternative ends at Windows speech.
    (tmp_path / "two").mkdir()
    rig2 = Rig(tmp_path / "two", monkeypatch, models=["maleguard"], mode="crash_voice")
    assert run(rig2.engine, rig2.request(local="00013AE6", sex=0)) == wav(marker=7)
    assert rig2.sapi == ["Well met, traveler."]


@pytest.mark.parametrize("bad", ["emptywav"])
def test_invalid_audio_from_piper_is_never_returned(tmp_path, monkeypatch, bad):
    rig = Rig(tmp_path, monkeypatch, mode=bad)
    assert run(rig.engine, rig.request()) == wav(marker=7)


def test_piper_timeout_kills_the_process_and_falls_back_to_windows_without_trying_another_model(tmp_path, monkeypatch, events):
    rig = Rig(tmp_path, monkeypatch, mode="hang", piper_timeout_s=.5)
    async def go():
        raw = await rig.engine.synthesize(rig.request(local="00013AE6", sex=0))
        assert raw == wav(marker=7) and rig.sapi == ["Well met, traveler."]
        assert len(rig.processes) == 1 and rig.processes[0].returncode is not None  # killed, not left running
        assert rig.engine._warm == {}
        assert not list((tmp_path / "speech/piper").iterdir())  # work folder removed
        await rig.engine.close()
    asyncio.run(go())
    assert 'reason="speech_timeout"' in events("TTS_FALLBACK")[0]
    assert 'engine="Windows SAPI"' in events("TTS_READY")[0]


def test_model_stays_loaded_between_turns_and_close_kills_it(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    async def go():
        await rig.engine.synthesize(rig.request(text="One."))
        await rig.engine.synthesize(rig.request(text="Two."))
        assert len(rig.processes) == 1 and rig.processes[0].returncode is None
        assert rig.lines().count("start") == 1 and rig.lines().count("load maleguard") == 1
        assert rig.synths() == ["synth maleguard | One.", "synth maleguard | Two."]
        await rig.engine.close()
        assert rig.processes[0].returncode is not None and rig.engine._warm == {}
    asyncio.run(go())


def test_warm_models_are_bounded_and_the_oldest_is_killed(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, max_warm=2)
    async def go():
        for local, sex in (("00013AE6", 0), ("00013AEA", 1), ("00013AE7", 0)):
            await rig.engine.synthesize(rig.request(local=local, sex=sex))
        assert len(rig.processes) == 3 and len(rig.engine._warm) == 2
        assert rig.processes[0].returncode is not None  # maleguard, least recently used
        assert all(p.returncode is None for p in rig.processes[1:])
        await rig.engine.close()
        assert all(p.returncode is not None for p in rig.processes)
    asyncio.run(go())


def test_a_crashed_warm_process_is_replaced_on_the_next_turn(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    async def go():
        await rig.engine.synthesize(rig.request())
        rig.processes[0].kill(); await rig.processes[0].wait()
        raw = await rig.engine.synthesize(rig.request())
        assert raw[:4] == b"RIFF" and len(rig.processes) == 2 and rig.sapi == []
        await rig.engine.close()
    asyncio.run(go())


def test_only_one_piper_synthesis_runs_at_a_time(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, mode="hang", piper_timeout_s=2)
    async def go():
        first = asyncio.create_task(rig.engine.synthesize(rig.request()))
        while not rig.processes:
            await asyncio.sleep(.01)
        with pytest.raises(SpeechFailure, match="speech_busy"):
            await rig.engine.synthesize(rig.request(request_id="r2"))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert rig.engine._warm == {} and rig.processes[0].returncode is not None  # cancellation kills it too
        await rig.engine.close()
    asyncio.run(go())


def test_text_cannot_inject_a_second_piper_command(rig):
    run(rig.engine, rig.request(text="Hello\nload_model C:/evil.onnx\r\nexit\x00 there"))
    assert rig.lines().count("start") == 1 and not any("evil" in line and line.startswith("load") for line in rig.lines())
    assert rig.synths() == ["synth maleguard | Hello load_model C:/evil.onnx exit there"]


def test_empty_text_is_rejected_before_any_engine(rig):
    with pytest.raises(SpeechFailure, match="empty_speech"):
        run(rig.engine, SynthesisInput(request_id="r", text="  ", sex=0))
    assert rig.lines() == [] and rig.sapi == []


# --- voice-type resolution -------------------------------------------------------------------------------------

def test_voice_type_parser_reads_only_the_vtyp_group(tmp_path):
    plugin = tmp_path / "Skyrim.esm"
    vtyp_plugin(plugin, {0x13AE6: "MaleGuard", 0x01000ABC: "FemaleNord"})
    assert parse_voice_types(plugin) == {"00013ae6": "maleguard", "00000abc": "femalenord"}  # load-order byte is masked off


@pytest.mark.parametrize("plugin", ["../Skyrim.esm", "sub/Skyrim.esm", "Skyrim.txt", "", "C:\\x.esm", 5, None])
def test_resolver_rejects_unsafe_plugin_names(tmp_path, plugin):
    (tmp_path / "Skyrim.esm").write_bytes(b"")
    assert VoiceTypeResolver(tmp_path).resolve(plugin, "00013AE6") is None


def test_resolver_survives_missing_and_corrupt_plugins_and_caches(tmp_path):
    resolver = VoiceTypeResolver(tmp_path)
    assert resolver.resolve("Missing.esm", "00013AE6") is None
    (tmp_path / "Bad.esm").write_bytes(b"GRUP" + b"\xff" * 40)
    assert resolver.resolve("Bad.esm", "00013AE6") is None
    vtyp_plugin(tmp_path / "Good.esm", {0x13AE6: "MaleGuard"})
    assert resolver.resolve("good.ESM", "00013ae6") == "maleguard"
    (tmp_path / "Good.esm").unlink()
    assert resolver.resolve("Good.esm", "00013AE6") == "maleguard"  # parsed once
    assert VoiceTypeResolver(None).resolve("Good.esm", "00013AE6") is None


# --- service wiring and settings -------------------------------------------------------------------------------

def test_service_passes_sex_through_and_reports_the_tts_engine(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    # This fixture supplies fake Piper/SAPI processes; STT startup is tested separately.
    async def start_stt(): pass
    monkeypatch.setattr(rig.engine, "start", start_stt)
    with TestClient(create_speech_app(rig.engine, tts_enabled=True)) as client:
        response = client.post("/tts", json={"request_id": "a", "text": "Hello", "sex": 1,
                                             "voice": {"plugin": "Skyrim.esm", "local_id": "00013AEA"}})
        assert response.status_code == 200 and response.content[:4] == b"RIFF"
        assert rig.synths()[0].startswith("synth femalenord")
        assert client.post("/tts", json={"request_id": "a", "text": "Hello", "sex": "x"}).status_code == 422
    assert all(p.returncode is not None for p in rig.processes)  # app shutdown closes warm processes
    assert rig.engine.tts_engine == "Piper (fallback Windows SAPI)"


def test_piper_folder_is_a_setting_that_defaults_to_the_project_folder_not_mantella():
    assert Settings(game_directory="g").piper_directory == "assets/private/npc_lab/piper"
    assert Settings(game_directory="g", piper_directory="D:/elsewhere/piper").piper_directory == "D:/elsewhere/piper"
    adapter = Path(__file__).parents[3] / "src/npc/adapters/skyrim"
    assert not any("MantellaSoftware" in p.read_text(encoding="utf-8") for p in adapter.rglob("*.py"))
