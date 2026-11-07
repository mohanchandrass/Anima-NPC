"""GPU-first Whisper setup and resident isolated transcription worker. No game logic."""

import argparse
import json
import math
import sys
import warnings
from pathlib import Path

MODEL = "Systran/faster-whisper-small.en"
REVISION = "d1d751a5f8271d482d14ca55d9e2deeebbae577f"
FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def model_path(root: Path, *, download=False, force=False):
    from huggingface_hub import snapshot_download
    path = Path(snapshot_download(MODEL, revision=REVISION,
        cache_dir=str(root / "data/stt/hub"), allow_patterns=list(FILES),
        local_files_only=not download, force_download=force))
    if not all((path / name).is_file() for name in FILES):
        raise ValueError("Whisper model incomplete; restart the connector to repair setup")
    return path


def load_model(path):
    import torch  # load the CUDA runtime bundled by setup before CTranslate2
    if sys.platform == "win32":
        import os
        os.environ["PATH"] = str(Path(torch.__file__).parent / "lib") + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(str(path), device="cuda", compute_type="int8_float16",
                            cpu_threads=2, num_workers=1, local_files_only=True)
    except (RuntimeError, ValueError, OSError):
        warnings.warn("Whisper small.en model is running on CPU (GPU unavailable or insufficient memory).", RuntimeWarning)
        return WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=2,
                            num_workers=1, local_files_only=True)



def prepare_model(root: Path):
    """Download once if missing, then validate loading before microphone admission."""
    try:
        path = model_path(root)
    except (OSError, ValueError):
        print("Preparing Whisper small.en (first setup requires internet)...", flush=True)
        path = model_path(root, download=True)
    try:
        load_model(path)
    except (OSError, ValueError, RuntimeError):
        print("Repairing the local Whisper model...", flush=True)
        path = model_path(root, download=True, force=True)
        load_model(path)
    return path


def transcribe(model, path):
    segments, _ = model.transcribe(str(path), language="en", beam_size=1,
        condition_on_previous_text=False, vad_filter=True)
    parts, scores = [], []
    for segment in segments:
        if segment.no_speech_prob > 0.6:
            continue
        parts.append(segment.text.strip())
        scores.append(math.exp(min(0, segment.avg_logprob)))
    text = " ".join(parts).strip()
    if not text or len(text) > 4000:
        raise ValueError("No usable speech recognized")
    # Mean token probability proxy, not a calibrated recognition confidence.
    return {"text": text, "confidence": sum(scores) / len(scores)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.worker:
            from contextlib import redirect_stdout
            with redirect_stdout(sys.stderr):
                model = load_model(model_path(args.root))
            print(json.dumps({"ready": True, "device": model.model.device}), flush=True)
            while line := sys.stdin.buffer.readline(16385):
                if len(line) > 16384:
                    return
                try:
                    request = json.loads(line)
                    try:
                        result = transcribe(model, request["input"])
                    except RuntimeError:
                        if model.model.device != "cuda":
                            raise
                        del model
                        from faster_whisper import WhisperModel
                        warnings.warn("Whisper small.en model is running on CPU (GPU inference failed).", RuntimeWarning)
                        model = WhisperModel(str(model_path(args.root)), device="cpu", compute_type="int8",
                                             cpu_threads=2, num_workers=1, local_files_only=True)
                        result = transcribe(model, request["input"])
                    result["device"] = model.model.device
                except Exception as error:
                    result = {"error": type(error).__name__}
                print(json.dumps(result), flush=True)
        elif args.prepare:
            prepare_model(args.root)
            print("Whisper small.en prepared (English, GPU preferred).")
        elif args.request:
            request = json.loads(args.request.read_text(encoding="utf-8"))
            print(json.dumps(transcribe(load_model(model_path(args.root)), request["input"])))
        else:
            parser.error("use --prepare or --request")
    except Exception as error:
        # Do not expose request text, audio contents or local credentials.
        parser.exit(1, f"Whisper failed ({type(error).__name__}); rerun setup/restart the connector.\n")


if __name__ == "__main__":
    main()
