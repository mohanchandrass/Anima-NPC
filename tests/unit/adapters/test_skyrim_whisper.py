"""Deterministic Whisper setup, CPU decoding and connector-default checks."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from npc.adapters.skyrim import whisper
from npc.adapters.skyrim.speech import WhisperSpeech, SpeechFailure, create_speech_app
from tests.unit.adapters.test_skyrim_speech import audio


def test_model_setup_pins_download_and_never_downloads_during_transcription(tmp_path, monkeypatch):
    calls = []
    def snapshot(repo, **kwargs):
        calls.append((repo, kwargs))
        if kwargs["local_files_only"] and len(calls) == 1:
            raise OSError("not cached")
        for name in whisper.FILES:
            (tmp_path / name).touch()
        return str(tmp_path)
    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    loaded = []
    monkeypatch.setattr(whisper, "load_model", lambda path: loaded.append(path))
    assert whisper.prepare_model(tmp_path) == tmp_path
    assert loaded == [tmp_path]
    assert [call[1]["local_files_only"] for call in calls] == [True, False]
    assert all(call[0] == whisper.MODEL and call[1]["revision"] == whisper.REVISION for call in calls)
    assert whisper.model_path(tmp_path) == tmp_path
    assert calls[-1][1]["local_files_only"] is True


def test_upgrade_prepares_small_en_without_reusing_or_removing_base_cache(tmp_path, monkeypatch):
    old_cache = tmp_path / "data/stt/hub/models--Systran--faster-whisper-base.en"
    old_cache.mkdir(parents=True)
    old_model = old_cache / "model.bin"
    old_model.write_bytes(b"existing base model")
    new_cache = tmp_path / "small-snapshot"
    calls = []

    def snapshot(repo, **kwargs):
        assert repo == "Systran/faster-whisper-small.en"
        assert kwargs["revision"] == "d1d751a5f8271d482d14ca55d9e2deeebbae577f"
        assert kwargs["cache_dir"] == str(tmp_path / "data/stt/hub")
        assert set(kwargs["allow_patterns"]) == {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
        calls.append(kwargs["local_files_only"])
        if kwargs["local_files_only"]:
            raise OSError("small model not cached")
        new_cache.mkdir()
        for name in whisper.FILES:
            (new_cache / name).touch()
        return str(new_cache)

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    loaded = []
    monkeypatch.setattr(whisper, "load_model", lambda path: loaded.append(path))
    assert whisper.prepare_model(tmp_path) == new_cache
    assert calls == [True, False]
    assert loaded == [new_cache]
    assert old_model.read_bytes() == b"existing base model"


def test_gpu_model_has_fixed_bounded_settings(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("faster_whisper.WhisperModel", lambda *a, **kw: calls.append((a, kw)))
    whisper.load_model(tmp_path)
    assert calls == [((str(tmp_path),), dict(device="cuda", compute_type="int8_float16", cpu_threads=2,
        num_workers=1, local_files_only=True))]


def test_model_load_failure_repairs_once_then_reports_failure(tmp_path, monkeypatch):
    downloads = []
    monkeypatch.setattr(whisper, "model_path", lambda root, **kw: downloads.append(kw) or tmp_path)
    def broken(path): raise RuntimeError("corrupt model")
    monkeypatch.setattr(whisper, "load_model", broken)
    with pytest.raises(RuntimeError): whisper.prepare_model(tmp_path)
    assert downloads == [{}, {"download": True, "force": True}]


def test_transcription_consumes_segments_and_rejects_silence():
    calls = []
    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(text=" Hello ", no_speech_prob=.1, avg_logprob=-.2),
                SimpleNamespace(text=" there.", no_speech_prob=.1, avg_logprob=-.4),
                SimpleNamespace(text="noise", no_speech_prob=.9, avg_logprob=-.1),
            ]), None
    result = whisper.transcribe(Model(), "input.wav")
    assert result["text"] == "Hello there."
    assert 0 < result["confidence"] < 1
    assert calls[0] == dict(language="en", beam_size=1, condition_on_previous_text=False, vad_filter=True)
    class Silent:
        def transcribe(self, *a, **kw): return iter([]), None
    with pytest.raises(ValueError, match="No usable speech"):
        whisper.transcribe(Silent(), "input.wav")


@pytest.mark.parametrize("failure", [None, "timeout", "cancel", "invalid"])
def test_whisper_worker_protocol_and_cleanup(tmp_path, failure):
    processes = []
    class Process:
        returncode = None
        killed = False
        def __init__(self):
            self.stdin = self
            self.stdout = self
            self.reads = 0
        def write(self, raw):
            request = json.loads(raw)
            assert Path(request["input"]).read_bytes() == audio()
        async def drain(self): pass
        async def readline(self):
            self.reads += 1
            if self.reads == 1:
                return b'{"ready":true,"device":"cuda"}\n'
            if failure in {"timeout", "cancel"}: await asyncio.sleep(10)
            return json.dumps({"text": "Hello", "confidence": 2 if failure == "invalid" else .8, "device": "cuda"}).encode()
        def kill(self): self.killed = True
        async def wait(self): self.returncode = -1
    async def launch(*args, **kwargs):
        assert args[:3] == (sys.executable, "-m", "npc.adapters.skyrim.whisper")
        assert args[-1] == "--worker"
        process = Process(); processes.append(process); return process
    async def run():
        engine = WhisperSpeech(tmp_path / "speech", tmp_path, launch=launch, timeout_s=.02)
        await engine.start()
        task = asyncio.create_task(engine.transcribe(audio()))
        if failure == "cancel":
            await asyncio.sleep(0)
            task.cancel()
        if failure:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else (SpeechFailure, TimeoutError)): await task
        else:
            assert await task == {"text": "Hello", "confidence": .8}
            assert await engine.transcribe(audio()) == {"text": "Hello", "confidence": .8}
            assert len(processes) == 1  # same loaded model across utterances
        assert not engine.worker.busy and list((tmp_path / "speech").iterdir()) == []
        if failure in {"timeout", "cancel"}: assert processes[0].killed
        await engine.close()
        assert all(p.killed for p in processes)
    asyncio.run(run())


def test_whisper_service_health_and_tts_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(whisper, "model_path", lambda root: tmp_path)
    async def start(self): self.stt_engine = "Whisper small.en cuda"
    monkeypatch.setattr(WhisperSpeech, "start", start)
    with TestClient(create_speech_app(WhisperSpeech(tmp_path, tmp_path), tts_enabled=False)) as client:
        assert client.get("/health").json() == {"status": "ready", "stt": "Whisper small.en cuda", "tts": "disabled"}
        assert client.post("/tts", json={"request_id": "a", "text": "Hello"}).status_code == 503


@pytest.mark.parametrize("external,extra", [(False, []), (False, ["--local-speech"]), (True, [])])
def test_launcher_defaults_prepare_whisper_preserve_external_and_do_not_rewrite_config(tmp_path, monkeypatch, external, extra):
    from npc.adapters.skyrim.__main__ import main
    async def start(self): pass
    monkeypatch.setattr(WhisperSpeech, "start", start)
    config = tmp_path / "connector.json"
    raw = {"game_directory": str(tmp_path / "game"), "pipeline_mode": "inbox",
           "stt_url": "http://localhost:9999/stt" if external else None}
    config.write_text(json.dumps(raw))
    prepared = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(whisper, "prepare_model", lambda root: prepared.append(root))
    monkeypatch.setattr(sys, "argv", ["connector", "--config", str(config), *extra])
    def serve(app, **kwargs):
        assert any(getattr(route, "path", "") == "/v1/speech" for route in app.routes) is not external
        with TestClient(app): pass
    monkeypatch.setattr("uvicorn.run", serve)
    main()
    assert prepared == ([] if external else [tmp_path])
    assert json.loads(config.read_text()) == raw


def test_setup_failure_does_not_start_server_or_record(tmp_path, monkeypatch):
    from npc.adapters.skyrim.__main__ import main
    async def start(self): pass
    monkeypatch.setattr(WhisperSpeech, "start", start)
    config = tmp_path / "connector.json"
    config.write_text(json.dumps({"game_directory": str(tmp_path / "game"), "pipeline_mode": "inbox"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["connector", "--config", str(config)])
    def broken(root): raise OSError("download failed")
    monkeypatch.setattr(whisper, "prepare_model", broken)
    def forbidden(*a, **kw): raise AssertionError("server must not start")
    monkeypatch.setattr("uvicorn.run", forbidden)
    with pytest.raises(SystemExit) as result: main()
    assert result.value.code == 1


def test_whisper_gpu_load_failure_warns_and_falls_back(tmp_path, monkeypatch):
    calls = []
    def load(*args, **kwargs):
        calls.append(kwargs)
        if kwargs["device"] == "cuda": raise RuntimeError("out of memory")
        return "cpu-model"
    monkeypatch.setattr("faster_whisper.WhisperModel", load)
    with pytest.warns(RuntimeWarning, match="Whisper small.en model is running on CPU"):
        assert whisper.load_model(tmp_path) == "cpu-model"
    assert [call["device"] for call in calls] == ["cuda", "cpu"]
