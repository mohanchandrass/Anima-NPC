"""Local Windows speech adapter. No model download, shell interpolation or game logic."""

import asyncio
from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from contextlib import asynccontextmanager

from .assets import MAX_WAV_BYTES, wav_duration
from .diagnostics import event as log_event


class SpeechFailure(ValueError):
    pass


class SynthesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=600)
    voice: dict | None = None  # Native identity metadata; not a file/process argument.
    npc_base: dict | None = None
    npc_ref: dict | None = None  # The actor reference form, for the optional character voice table.
    sex: int | None = None  # Actor base sex: 0 male, 1 female; anything else means unknown.


class WindowsSpeech:
    stt_engine = "Windows SAPI"

    def __init__(self, directory: Path, *, timeout_s=20, launch=asyncio.create_subprocess_exec):
        self.directory = directory
        self.timeout_s = timeout_s
        self.launch = launch
        self.busy = False

    def worker_command(self, operation, request):
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                str(Path(__file__).with_name("speech_worker.ps1")), "-RequestPath", str(request)]

    async def _run(self, operation, *, text=None, audio=None):
        if self.busy:
            raise SpeechFailure("speech_busy")
        self.busy = True
        process = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.directory) as work:
                work = Path(work)
                command = {"operation": operation}
                if text is not None:
                    command.update(text=text, output=str(work / "output.wav"))
                if audio is not None:
                    (work / "input.wav").write_bytes(audio)
                    command["input"] = str(work / "input.wav")
                request = work / "request.json"
                request.write_text(json.dumps(command), encoding="utf-8")
                process = await self.launch(
                    *self.worker_command(operation, request),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                try:
                    stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout_s)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                if process.returncode != 0 or len(stdout) > 16384:
                    raise SpeechFailure("speech_engine_failed")
                result = json.loads(stdout.decode("utf-8-sig"))
                if operation == "tts":
                    with (work / "output.wav").open("rb") as stream:
                        raw = stream.read(MAX_WAV_BYTES + 1)
                    wav_duration(raw)
                    return raw
                if operation == "stt":
                    text, confidence = result.get("text"), result.get("confidence")
                    if (not isinstance(text, str) or not text.strip() or len(text) > 4000
                            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                            or not 0 <= confidence <= 1):
                        raise SpeechFailure("invalid_transcript")
                return result
        except TimeoutError:
            raise SpeechFailure("speech_timeout") from None
        except (OSError, json.JSONDecodeError, UnicodeError):
            raise SpeechFailure("speech_engine_unavailable") from None
        finally:
            # Cancellation/timeout cannot leave a speech process or microphone-file reader alive.
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            self.busy = False

    async def synthesize(self, request):
        if not request.text.strip():
            raise SpeechFailure("empty_speech")
        raw = await self._run("tts", text=request.text)
        log_event("TTS_READY", request=request.request_id, engine="Windows SAPI", voice="system default", bytes=len(raw))
        return raw

    async def transcribe(self, raw):
        if wav_duration(raw) > 16:
            raise SpeechFailure("microphone_audio_limit")
        result = await self._run("stt", audio=raw)
        log_event("STT_READY", text=result["text"], confidence=result["confidence"], engine=self.stt_engine)
        return {"text": result["text"], "confidence": result["confidence"]}


class WhisperSpeech(WindowsSpeech):
    """Resident GPU-first Whisper with bounded admission and process cancellation."""

    stt_engine = "Whisper small.en (starting)"
    tts_engine = "Windows SAPI"

    def __init__(self, directory, root, **kwargs):
        super().__init__(directory, **kwargs)
        self.root = root

    async def start(self):
        if not hasattr(self, "worker"):
            self.worker = WhisperWorker(self.root, timeout_s=self.timeout_s, launch=self.launch)
        await self.worker.start()
        self.stt_engine = "Whisper small.en " + self.worker.device

    async def close(self):
        if hasattr(self, "worker"):
            await self.worker.close()

    @asynccontextmanager
    async def _audio_directory(self, worker):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            try:
                yield directory
            except BaseException:
                await worker._stop_process()
                raise

    async def transcribe(self, raw):
        if wav_duration(raw) > 16:
            raise SpeechFailure("microphone_audio_limit")
        if not hasattr(self, "worker"):
            await self.start()
        worker = self.worker
        if worker.busy or (worker._recovery and not worker._recovery.done()):
            raise SpeechFailure("speech_busy")
        worker.busy = True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            async with self._audio_directory(worker) as directory:
                path = Path(directory) / "input.wav"
                path.write_bytes(raw)
                async with asyncio.timeout(self.timeout_s):
                    await worker.start()
                    worker.process.stdin.write(json.dumps({"input": str(path)}).encode() + b"\n")
                    await worker.process.stdin.drain()
                    reply = json.loads(await worker.process.stdout.readline())
                    text, confidence = reply.get("text"), reply.get("confidence")
                    if (not isinstance(text, str) or not text.strip() or len(text) > 4000
                            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                            or not 0 <= confidence <= 1):
                        raise SpeechFailure("invalid_transcript")
                    worker.device = reply.get("device", worker.device)
                    self.stt_engine = "Whisper small.en " + worker.device
                    return {"text": text, "confidence": confidence}
        except BaseException:
            await worker._stop_process()
            if not worker._closed:
                worker._recovery = asyncio.create_task(worker._recover())
            raise
        finally:
            worker.busy = False


from npc.output.chatterbox import TurboWorker


class WhisperWorker(TurboWorker):
    device = "unknown"

    async def start(self):
        if self._closed:
            raise SpeechFailure("Whisper worker closed")
        if self.ready and self.process and self.process.returncode is None:
            return
        self.process = await self.launch(sys.executable, "-m", "npc.adapters.skyrim.whisper",
            "--root", str(self.root), "--worker", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=None, limit=16384,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            async with asyncio.timeout(120):
                reply = json.loads(await self.process.stdout.readline())
                if reply.get("ready") is not True or reply.get("device") not in {"cpu", "cuda"}:
                    raise SpeechFailure("Whisper worker failed readiness")
                self.device = reply["device"]
                self.ready = True
        except BaseException:
            await self._stop_process()
            raise

class _WarmPiper:
    def __init__(self, process, work):
        self.process, self.work = process, work


class PiperSpeech(WhisperSpeech):
    """Skyrim voice-type TTS through a local `piper.exe`, falling back to Windows speech.

    Uses piper's line protocol (`load_model <path>`, `synthesize <text>`, `exit`; the file `out.wav` appears in the
    working directory and `Synthesis complete` is printed). The folder is an explicit setting; nothing is copied.
    A few models stay loaded so later turns skip the model load. Every process is killed on timeout, failure or close.
    """

    MODEL_NAME = re.compile(r"^[a-z0-9]{1,64}$")
    SEX_PREFIX = {0: "male", 1: "female"}
    SEX_DEFAULTS = ("nord", "eventoned")

    def __init__(self, directory, root, *, piper_directory, voice_types=None, command=None,
                 voice_table=None, voice_overrides=None, piper_timeout_s=8, max_warm=2, piper_launch=asyncio.create_subprocess_exec, **kwargs):
        super().__init__(directory, root, **kwargs)
        self.piper_directory = Path(piper_directory)
        self.models = self.piper_directory / "models/skyrim/low"
        self.command = list(command) if command else [str(self.piper_directory / "piper.exe")]
        self.voice_types = voice_types
        self.voice_table = voice_table  # VoiceTable or None
        self.voice_overrides = {" ".join(k.lower().split()): "".join(v.lower().split())
                                for k, v in (voice_overrides or {}).items()}
        self.piper_timeout_s, self.max_warm, self.piper_launch = piper_timeout_s, max_warm, piper_launch
        self._warm: OrderedDict[str, _WarmPiper] = OrderedDict()
        self._piper_busy = False

    def available_models(self):
        try:
            names = {p.name[:-5] for p in self.models.glob("*.onnx")
                     if self.MODEL_NAME.match(p.name[:-5]) and (self.models / (p.name + ".json")).is_file()}
        except OSError:
            return set()
        return names if Path(self.command[0]).is_file() else set()

    @property
    def tts_engine(self):
        return "Piper (fallback Windows SAPI)" if self.available_models() else "Windows SAPI"

    def sex_model(self, sex, available):
        prefix = self.SEX_PREFIX.get(sex) if isinstance(sex, int) and not isinstance(sex, bool) else None
        matches = sorted(name for name in available if prefix and name.startswith(prefix))
        for suffix in self.SEX_DEFAULTS:
            if prefix and prefix + suffix in matches:
                return prefix + suffix
        return matches[0] if matches else None

    async def candidates(self, request, available):
        """Ordered (model, reason): authored override, voice table, voice type, then a same-sex model."""
        found = []

        def add(model, reason):
            if model in available and all(model != existing for existing, _ in found):
                found.append((model, reason))

        voice = request.voice if isinstance(request.voice, dict) else {}
        base = request.npc_base if isinstance(request.npc_base, dict) else None
        ref = request.npc_ref if isinstance(request.npc_ref, dict) else None
        name = str((base or {}).get("name") or (ref or {}).get("name") or "")
        for key in ((f"{base['plugin']}:{base['local_id']}" if base and base.get("plugin") and base.get("local_id") else ""),
                    name):
            if key and (model := self.voice_overrides.get(" ".join(key.lower().split()))):
                add(model, "override")
        if self.voice_table is not None:
            for model, reason in self.voice_table.lookup(ref=ref, base=base, name=name):
                add(model, reason)
        if self.voice_types and voice:
            add(await asyncio.to_thread(self.voice_types.resolve, voice.get("plugin"), voice.get("local_id")), "voice_type")
        if fallback := self.sex_model(request.sex, available):
            add(fallback, "same_sex")
        return found

    async def synthesize(self, request):
        if not request.text.strip():
            raise SpeechFailure("empty_speech")
        available = self.available_models()
        candidates = await self.candidates(request, available) if available else []
        text = " ".join("".join(c if c.isprintable() else " " for c in request.text).split())
        if candidates and self._piper_busy:
            raise SpeechFailure("speech_busy")
        for model, reason in candidates:
            self._piper_busy = True
            try:
                raw = await self._piper(model, text)
            except SpeechFailure as failure:
                log_event("TTS_FALLBACK", request=request.request_id, engine="Piper", voice=model,
                          reason=str(failure))
                if str(failure) == "speech_timeout":
                    break  # A hung engine would only hang again; use Windows speech directly.
            else:
                log_event("TTS_READY", request=request.request_id, engine="Piper", voice=model,
                          selected_by=reason, bytes=len(raw))
                return raw
            finally:
                self._piper_busy = False
        raw = await super().synthesize(request)
        return raw

    async def _piper(self, model, text):
        entry = self._warm.pop(model, None)
        if entry is not None and entry.process.returncode is not None:
            await self._discard(entry)
            entry = None
        try:
            if entry is None:
                while len(self._warm) >= self.max_warm:
                    await self._discard(self._warm.popitem(last=False)[1])
                entry = await self._start(model)
            self._warm[model] = entry
            return await asyncio.wait_for(self._exchange(entry, text), self.piper_timeout_s)
        except TimeoutError:
            await self._drop(model)
            raise SpeechFailure("speech_timeout") from None
        except (OSError, ValueError):
            await self._drop(model)
            raise SpeechFailure("speech_engine_failed") from None
        except BaseException:
            await self._drop(model)
            raise

    async def _start(self, model):
        base = self.directory / "piper"
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(dir=base))
        try:
            process = await self.piper_launch(
                *self.command, cwd=str(work), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env={**os.environ, "OMP_NUM_THREADS": "1"})
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        entry = _WarmPiper(process, work)
        try:
            path = str(self.models / f"{model}.onnx")
            process.stdin.write(f"load_model {path}\n".encode("utf-8"))
        except BaseException:
            await self._discard(entry)
            raise
        return entry

    async def _exchange(self, entry, text):
        output = entry.work / "out.wav"
        output.unlink(missing_ok=True)
        entry.process.stdin.write(f"synthesize {text}\n".encode("utf-8"))
        await entry.process.stdin.drain()
        for _ in range(64):  # Bounded: prompt/status lines only.
            line = await entry.process.stdout.readline()
            if not line:
                raise OSError("piper exited")
            if b"Synthesis complete" in line:
                with output.open("rb") as stream:
                    raw = stream.read(MAX_WAV_BYTES + 1)
                wav_duration(raw)
                return raw
        raise ValueError("piper protocol")

    async def _drop(self, model):
        entry = self._warm.pop(model, None)
        if entry is not None:
            await self._discard(entry)

    @staticmethod
    async def _discard(entry):
        process = entry.process
        try:
            if process.returncode is None:
                process.kill()
                await process.wait()
        except ProcessLookupError:
            pass
        finally:
            shutil.rmtree(entry.work, ignore_errors=True)

    async def close(self):
        try:
            while self._warm:
                await self._discard(self._warm.popitem()[1])
        finally:
            await super().close()


def create_speech_app(engine, *, tts_enabled=True, turbo=None):
    @asynccontextmanager
    async def lifespan(app):
        if isinstance(engine, WhisperSpeech):
            await engine.start()
        try:
            if turbo:
                await turbo.start()
            yield
        finally:
            if turbo:
                await turbo.close()
            if engine is not None and hasattr(engine, "close"):
                await engine.close()
    app = FastAPI(title="Anima local speech", lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"error": "loopback_only"}, status_code=403)
        limit = 16384 if request.url.path.endswith("/tts") else MAX_WAV_BYTES
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                return JSONResponse({"error": "speech_payload_too_large"}, status_code=413)
        request._body = bytes(raw)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def failure(request, error):
        log_event("SPEECH_FAILED", operation=request.url.path, error=type(error).__name__)
        return JSONResponse({"error": "speech_unavailable"}, status_code=503)

    @app.get("/health")
    async def health():
        if turbo:
            ready = getattr(turbo, "ready", turbo.process is not None and turbo.process.returncode is None)
            return {"status": "ready" if ready else "degraded",
                    "stt": engine.stt_engine if engine else "external", "tts": "Chatterbox Turbo"}
        if isinstance(engine, WhisperSpeech):
            from .whisper import model_path
            model_path(engine.root)
            voices = (await engine._run("probe")).get("voices", []) if tts_enabled else []
            return {"status": "ready" if not tts_enabled or voices else "degraded",
                    "stt": engine.stt_engine, "tts": engine.tts_engine if tts_enabled else "disabled"}
        result = await engine._run("probe")
        return {"status": "ready" if result.get("voices") and result.get("recognizers") else "degraded", **result}

    @app.post("/tts")
    async def tts(request: SynthesisInput, http_request: Request):
        if not tts_enabled:
            return JSONResponse({"error": "tts_not_configured"}, status_code=503)
        if turbo:
            from npc.output.chatterbox import TurboRequest
            def key(form):
                if form and form.get("plugin") and form.get("local_id"):
                    if not isinstance(form["plugin"], str) or not isinstance(form["local_id"], str):
                        raise ValueError("invalid voice identity")
                    return f"{form['plugin'].lower()}:{form['local_id'].lower()}"
                return None
            work = asyncio.create_task(turbo.synthesize(TurboRequest(
                request_id=request.request_id, text=request.text,
                voice_key=key(request.voice), npc_key=key(request.npc_base))))
            try:
                # A disconnected bridge must not leave stale GPU work occupying the slot.
                while not work.done():
                    await asyncio.wait({work}, timeout=.1)
                    if await http_request.is_disconnected():
                        raise asyncio.CancelledError()
                raw = await work
                wav_duration(raw)
                log_event("TTS_READY", request=request.request_id, engine="Chatterbox Turbo", bytes=len(raw))
                return Response(raw, media_type="audio/wav")
            except (OSError, TimeoutError):
                raise ValueError("Turbo worker unavailable") from None
            finally:
                if not work.done():
                    work.cancel()
                await asyncio.gather(work, return_exceptions=True)
        return Response(await engine.synthesize(request), media_type="audio/wav")

    @app.post("/stt")
    async def stt(request: Request):
        if engine is None:
            return JSONResponse({"error": "stt_external"}, status_code=503)
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            return JSONResponse({"error": "audio_wav_required"}, status_code=415)
        work = asyncio.create_task(engine.transcribe(await request.body()))
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=.1)
                if await request.is_disconnected():
                    raise asyncio.CancelledError()
            return await work
        finally:
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)

    return app
