"""Pinned Turbo setup and bounded, voice-conditioned synthesis worker."""

import argparse
import asyncio
import base64
from collections import OrderedDict
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import wave

from pydantic import Field
from npc.contracts.context import Contract

MODEL = "ResembleAI/chatterbox-turbo"
REVISION = "749d1c1a46eb10492095d68fbcf55691ccf137cd"
FILES = ("t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "ve.safetensors",
         "conds.pt", "added_tokens.json", "merges.txt", "special_tokens_map.json",
         "tokenizer_config.json", "vocab.json")
MAX_REPLY = 9 * 1024 * 1024
MAX_WAV = 6 * 1024 * 1024


class TurboRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=600)
    voice_key: str | None = Field(default=None, max_length=256)
    npc_key: str | None = Field(default=None, max_length=256)


def model_path(root, *, download=False):
    # Use the normal resumable HTTP path on fresh setup; no user Xet/CAS setup.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    from huggingface_hub import snapshot_download
    path = Path(snapshot_download(MODEL, revision=REVISION,
        cache_dir=str(root / "data/tts/hub"), allow_patterns=list(FILES),
        local_files_only=not download))
    if not all((path / name).is_file() for name in FILES):
        raise ValueError("Turbo download incomplete; rerun setup_chatterbox.bat")
    return path


def load_model(path, device="auto"):
    import torch
    import perth
    if perth.PerthImplicitWatermarker is None:
        raise RuntimeError("Chatterbox watermark dependency is unavailable; rerun setup_chatterbox.bat to restore compatible packages")
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    torch.set_num_threads(2)
    torch.set_grad_enabled(False)
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    import warnings
    try:
        model = ChatterboxTurboTTS.from_local(path, device=device)
    except (RuntimeError, OSError):
        if device != "cuda":
            raise
        torch.cuda.empty_cache()
        device = "cpu"
        model = ChatterboxTurboTTS.from_local(path, device=device)
    if device == "cpu":
        warnings.warn("Chatterbox Turbo model is running on CPU (GPU unavailable or insufficient memory).", RuntimeWarning)
    return model


def pcm_wav(samples, rate):
    import numpy as np
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not 8000 <= rate <= 48000 or not 0 < samples.size <= rate * 30 or not np.isfinite(samples).all():
        raise ValueError("invalid or oversized synthesized audio")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    raw = out.getvalue()
    if len(raw) > MAX_WAV:
        raise ValueError("synthesized audio exceeds limit")
    return raw


class TurboEngine:
    def __init__(self, root, model):
        self.root, self.model = root.resolve(), model
        self.cache = OrderedDict()

    def reference(self, request):
        path = self.root / "data/tts/voices/manifest.json"
        if not path.is_file() or path.stat().st_size > 128 * 1024:
            raise ValueError("NPC voice references missing; rerun setup_chatterbox.bat with Skyrim installed")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        entry = manifest.get("npcs", {}).get(request.npc_key) or manifest.get("voices", {}).get(request.voice_key)
        if not entry:
            raise ValueError("NPC voice reference unavailable")
        reference = (path.parent / entry["file"]).resolve()
        if not reference.is_relative_to(path.parent.resolve()) or reference.suffix.lower() != ".wav":
            raise ValueError("invalid reference path")
        if reference.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("reference exceeds limit")
        raw = reference.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError("voice reference hash mismatch")
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if (not 8000 <= wav.getframerate() <= 48000 or wav.getnchannels() not in (1, 2)
                    or not 5 < wav.getnframes() / wav.getframerate() <= 15 or wav.getsampwidth() != 2
                    or len(wav.readframes(wav.getnframes())) != wav.getnframes() * wav.getnchannels() * 2):
                raise ValueError("voice reference must be 5-15 seconds of PCM16")
        return reference, digest

    def synthesize(self, request):
        if not request.text.strip():
            raise ValueError("empty speech")
        reference, digest = self.reference(request)
        # Explicitly restore conditioning for EVERY request, including cache hits.
        if digest not in self.cache:
            self.model.prepare_conditionals(str(reference), exaggeration=0.0)
            self.cache[digest] = self.model.conds
            while len(self.cache) > 4:
                self.cache.popitem(last=False)
        self.model.conds = self.cache[digest]
        self.cache.move_to_end(digest)
        audio = self.model.generate(request.text, temperature=0.8,
            repetition_penalty=1.2, exaggeration=0.0, cfg_weight=0.0)
        return pcm_wav(audio.detach().cpu().numpy(), self.model.sr)


def worker(root, device):
    # Model/library messages must not corrupt the JSON line protocol.
    with redirect_stdout(sys.stderr):
        engine = TurboEngine(root, load_model(model_path(root), device))
        # Warm lazy audio kernels before admitting deadline-bound NPC requests.
        try:
            pcm_wav(engine.model.generate("Ready.").detach().cpu().numpy(), engine.model.sr)
        except RuntimeError:
            if not str(engine.model.device).startswith("cuda"):
                raise
            del engine
            import torch
            torch.cuda.empty_cache()
            engine = TurboEngine(root, load_model(model_path(root), "cpu"))
            pcm_wav(engine.model.generate("Ready.").detach().cpu().numpy(), engine.model.sr)
    print(json.dumps({"ready": True}), flush=True)
    while line := sys.stdin.buffer.readline(16385):
        if len(line) > 16384:
            return 1
        try:
            request = TurboRequest.model_validate_json(line)
            with redirect_stdout(sys.stderr):
                try:
                    raw = engine.synthesize(request)
                except RuntimeError:
                    if not str(engine.model.device).startswith("cuda"):
                        raise
                    del engine
                    import torch
                    torch.cuda.empty_cache()
                    engine = TurboEngine(root, load_model(model_path(root), "cpu"))
                    raw = engine.synthesize(request)
            reply = {"request_id": request.request_id, "wav": base64.b64encode(raw).decode("ascii")}
        except Exception as error:
            reply = {"error": type(error).__name__}
        print(json.dumps(reply), flush=True)
    return 0


class TurboWorker:
    """One resident process. No queue; timeout/cancellation destroys stale work."""

    def __init__(self, root, *, timeout_s=20, launch=asyncio.create_subprocess_exec):
        self.root, self.timeout_s, self.launch = root, timeout_s, launch
        self.process = None
        self.busy = False
        self.ready = False
        self._recovery = None
        self._closed = False

    async def _stop_process(self):
        self.ready = False
        process, self.process = self.process, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def close(self):
        self._closed = True
        if self._recovery is not None:
            self._recovery.cancel()
            await asyncio.gather(self._recovery, return_exceptions=True)
            self._recovery = None
        await self._stop_process()

    async def _recover(self):
        try:
            await self.start()
        except Exception:
            # No automatic retry loop. A later request can initiate one new attempt.
            pass

    async def start(self):
        if self._closed:
            raise ValueError("Turbo worker closed")
        if self.ready and self.process is not None and self.process.returncode is None:
            return
        self.ready = False
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
                       "HF_HOME": str(self.root / "data/tts/hf")}
        self.process = await self.launch(sys.executable, "-m", "npc.output.chatterbox", "--worker",
            "--root", str(self.root), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=None, limit=MAX_REPLY, env=environment)
        try:
            async with asyncio.timeout(120):
                ready = json.loads(await self.process.stdout.readline())
                if ready != {"ready": True}:
                    raise ValueError("Turbo worker failed readiness")
                self.ready = True
        except BaseException:
            await self._stop_process()
            raise

    async def synthesize(self, request):
        if self.busy or (self._recovery is not None and not self._recovery.done()):
            raise ValueError("Turbo worker busy")
        self.busy = True
        try:
            async with asyncio.timeout(self.timeout_s):
                await self.start()
                self.process.stdin.write(request.model_dump_json().encode() + b"\n")
                await self.process.stdin.drain()
                raw = await self.process.stdout.readline()
                if len(raw) > MAX_REPLY:
                    raise ValueError("Turbo worker reply exceeds limit")
                reply = json.loads(raw)
                if reply.get("request_id") != request.request_id or "wav" not in reply:
                    raise ValueError("Turbo synthesis failed or voice reference missing")
                audio = base64.b64decode(reply["wav"], validate=True)
                if len(audio) > MAX_WAV:
                    raise ValueError("Turbo WAV exceeds limit")
                return audio
        except BaseException:
            await self._stop_process()
            if not self._closed:
                self._recovery = asyncio.create_task(self._recover())
            raise
        finally:
            self.busy = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    os.environ.setdefault("HF_HOME", str(args.root / "data/tts/hf"))
    try:
        if args.prepare:
            path = model_path(args.root, download=True)
            model = load_model(path, args.device)
            raw = pcm_wav(model.generate("Anima voice setup is ready.").detach().cpu().numpy(), model.sr)
            target = args.root / "output/chatterbox/setup-check.wav"
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            print(f"Chatterbox Turbo ready; sample: {target}")
        elif args.worker:
            return worker(args.root, args.device)
        else:
            parser.error("use --prepare or --worker")
    except Exception as error:
        parser.exit(1, f"Chatterbox failed ({type(error).__name__}: {error}). Rerun setup_chatterbox.bat.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
