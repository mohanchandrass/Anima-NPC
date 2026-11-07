import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import wave

import numpy as np
import pytest

from npc.output import chatterbox as turbo


def voice(root, name, value=1):
    directory = root / "data/tts/voices"; directory.mkdir(parents=True, exist_ok=True)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(bytes([value, 0]) * 16000 * 6)
    raw = stream.getvalue(); (directory / name).write_bytes(raw)
    return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}


class Audio:
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return np.zeros(2400, dtype=np.float32)


def test_pinned_download_omits_unused_checkpoint(tmp_path, monkeypatch):
    calls = []
    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        for name in turbo.FILES: (tmp_path / name).touch()
        return str(tmp_path)
    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    assert turbo.model_path(tmp_path, download=True) == tmp_path
    assert calls[0][0] == turbo.MODEL
    assert calls[0][1]["revision"] == turbo.REVISION
    assert calls[0][1]["local_files_only"] is False
    assert "s3gen.safetensors" not in calls[0][1]["allow_patterns"]
    turbo.model_path(tmp_path)
    assert calls[-1][1]["local_files_only"] is True


@pytest.mark.parametrize("cuda", [False, True])
def test_model_loading_uses_existing_device_and_caps_cpu_threads(tmp_path, monkeypatch, cuda):
    threads, loads = [], []
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=threads.append, set_grad_enabled=lambda enabled: None,
        cuda=SimpleNamespace(is_available=lambda: cuda)))
    monkeypatch.setitem(sys.modules, "perth", SimpleNamespace(PerthImplicitWatermarker=object))
    class Model:
        @classmethod
        def from_local(cls, path, **kwargs): loads.append(kwargs); return "model"
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", SimpleNamespace(ChatterboxTurboTTS=Model))
    assert turbo.load_model(tmp_path) == "model"
    assert threads == [2] and loads == [{"device": "cuda" if cuda else "cpu"}]


def test_missing_watermark_dependency_fails_before_loading_large_model(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "perth", SimpleNamespace(PerthImplicitWatermarker=None))
    with pytest.raises(RuntimeError, match="watermark dependency"):
        turbo.load_model(tmp_path)


def test_voice_switching_restores_conditioning_and_npc_override(tmp_path):
    a, b = voice(tmp_path, "a.wav"), voice(tmp_path, "b.wav", 2)
    manifest = tmp_path / "data/tts/voices/manifest.json"
    manifest.write_text(json.dumps({"voices": {"a": a, "b": b}, "npcs": {"npc": b}}))
    class Model:
        sr = 24000
        def __init__(self): self.prepared, self.used = [], []
        def prepare_conditionals(self, path, **kwargs):
            self.prepared.append(Path(path).name); self.conds = Path(path).name
        def generate(self, text, **kwargs): self.used.append(self.conds); return Audio()
    model = Model(); engine = turbo.TurboEngine(tmp_path, model)
    for key in ("a", "b", "a"):
        raw = engine.synthesize(turbo.TurboRequest(request_id="r", text="Hello", voice_key=key))
        assert raw[:4] == b"RIFF"
    engine.synthesize(turbo.TurboRequest(request_id="r", text="Hello", voice_key="a", npc_key="npc"))
    assert model.prepared == ["a.wav", "b.wav"]
    assert model.used == ["a.wav", "b.wav", "a.wav", "b.wav"]
    with pytest.raises(ValueError, match="unavailable"):
        engine.synthesize(turbo.TurboRequest(request_id="r", text="Hello", voice_key="unknown"))
    (tmp_path / "data/tts/voices/a.wav").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        engine.synthesize(turbo.TurboRequest(request_id="r", text="Hello", voice_key="a"))


@pytest.mark.parametrize("samples,rate", [([], 24000), ([float("nan")], 24000), ([0], 1000), (np.zeros(24000 * 31), 24000)])
def test_invalid_audio_is_rejected(samples, rate):
    with pytest.raises(ValueError): turbo.pcm_wav(samples, rate)


def test_reference_cannot_escape_directory(tmp_path):
    entry = voice(tmp_path, "a.wav")
    entry["file"] = "../../../outside.wav"
    (tmp_path / "data/tts/voices/manifest.json").write_text(json.dumps({"voices": {"a": entry}}))
    with pytest.raises(ValueError, match="path"):
        turbo.TurboEngine(tmp_path, None).reference(turbo.TurboRequest(request_id="r", text="Hello", voice_key="a"))


@pytest.mark.parametrize("failure", [None, "timeout", "cancel", "wrong_id"])
def test_resident_worker_protocol_and_cancellation(tmp_path, failure):
    processes = []
    class Input:
        def write(self, raw): self.request = json.loads(raw)
        async def drain(self): pass
    class Process:
        returncode = None
        def __init__(self): self.stdin, self.stdout, self.lines = Input(), self, 0
        async def readline(self):
            self.lines += 1
            if self.lines == 1: return b'{"ready":true}\n'
            if failure in {"timeout", "cancel"}: await asyncio.sleep(10)
            return json.dumps({"request_id": "wrong" if failure == "wrong_id" else "r",
                "wav": base64.b64encode(b"audio").decode()}).encode() + b"\n"
        def kill(self): self.returncode = -1
        async def wait(self): return self.returncode
    async def launch(*args, **kwargs):
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        process = Process(); processes.append(process); return process
    async def run():
        worker = turbo.TurboWorker(tmp_path, timeout_s=.03, launch=launch)
        task = asyncio.create_task(worker.synthesize(turbo.TurboRequest(request_id="r", text="Hello", voice_key="a")))
        if failure == "cancel":
            while not processes: await asyncio.sleep(0)
            task.cancel()
        if failure:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else TimeoutError if failure == "timeout" else ValueError):
                await task
            assert processes[0].returncode == -1
            assert worker._recovery is not None
            await worker.close()
            assert worker.process is None
        else:
            assert await task == b"audio"
            assert await worker.synthesize(turbo.TurboRequest(request_id="r", text="Again")) == b"audio"
            assert len(processes) == 1
            await worker.close()
        assert not worker.busy
    asyncio.run(run())


def test_worker_busy_does_not_admit_more_work(tmp_path):
    worker = turbo.TurboWorker(tmp_path); worker.busy = True
    with pytest.raises(ValueError, match="busy"):
        asyncio.run(worker.synthesize(turbo.TurboRequest(request_id="r", text="Hello")))


def test_worker_warms_and_emits_only_correlated_protocol_json(tmp_path, monkeypatch):
    events=[]
    class Model:
        sr=24000
        def generate(self,text):
            events.append('warm'); print('upstream model log'); return Audio()
    class Engine:
        def __init__(self,root,model): self.model=model
        def synthesize(self,request):
            events.append('synthesize'); return b'audio'
    output=io.StringIO(); errors=io.StringIO()
    request=turbo.TurboRequest(request_id='r',text='Hello',voice_key='voice')
    monkeypatch.setattr(turbo,'model_path',lambda root: tmp_path)
    monkeypatch.setattr(turbo,'load_model',lambda *args: Model())
    monkeypatch.setattr(turbo,'TurboEngine',Engine)
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(request.model_dump_json().encode()+b'\n')))
    monkeypatch.setattr(sys,'stdout',output)
    monkeypatch.setattr(sys,'stderr',errors)
    assert turbo.worker(tmp_path,'cpu')==0
    records=[json.loads(line) for line in output.getvalue().splitlines()]
    assert records==[{'ready':True},{'request_id':'r','wav':base64.b64encode(b'audio').decode()}]
    assert events==['warm','synthesize']
    assert 'upstream model log' in errors.getvalue()


def test_timeout_recovery_warms_outside_new_request_deadlines(tmp_path):
    processes=[]
    async def run():
        recovery_ready=asyncio.Event()
        class Input:
            def write(self,raw): pass
            async def drain(self): pass
        class Process:
            returncode=None
            def __init__(self,index): self.index=index;self.stdin=Input();self.stdout=self;self.lines=0
            async def readline(self):
                self.lines+=1
                if self.lines==1:
                    if self.index==1: await recovery_ready.wait()
                    return b'{"ready":true}\n'
                if self.index==0: await asyncio.sleep(10)
                return json.dumps({'request_id':'r','wav':base64.b64encode(b'audio').decode()}).encode()+b'\n'
            def kill(self): self.returncode=-1
            async def wait(self): return self.returncode
        async def launch(*args,**kwargs):
            process=Process(len(processes));processes.append(process);return process
        worker=turbo.TurboWorker(tmp_path,timeout_s=.03,launch=launch)
        request=turbo.TurboRequest(request_id='r',text='Hello')
        try:
            with pytest.raises(TimeoutError): await worker.synthesize(request)
            while len(processes)<2: await asyncio.sleep(0)
            assert not worker.ready
            with pytest.raises(ValueError,match='busy'): await worker.synthesize(request)
            assert len(processes)==2
            recovery_ready.set()
            await worker._recovery
            assert worker.ready and await worker.synthesize(request)==b'audio'
        finally: await worker.close()
        assert all(process.returncode==-1 for process in processes)
    asyncio.run(run())


def test_gpu_load_failure_falls_back_once_with_warning(tmp_path, monkeypatch):
    loads = []
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None,
        set_grad_enabled=lambda enabled: None, cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None)))
    monkeypatch.setitem(sys.modules, "perth", SimpleNamespace(PerthImplicitWatermarker=object))
    class Model:
        @classmethod
        def from_local(cls, path, device):
            loads.append(device)
            if device == "cuda": raise RuntimeError("GPU out of memory")
            return "cpu-model"
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", SimpleNamespace(ChatterboxTurboTTS=Model))
    with pytest.warns(RuntimeWarning, match="Chatterbox Turbo model is running on CPU"):
        assert turbo.load_model(tmp_path) == "cpu-model"
    assert loads == ["cuda", "cpu"]
