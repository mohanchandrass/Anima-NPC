"""Repeatable project-only setup, runnable with the standard library alone."""

import shutil
import argparse
import subprocess
import sys
import zipfile
from pathlib import Path


class SetupFailure(RuntimeError):
    pass


def setup(root: Path, *, python=sys.executable, version=None, run=subprocess.run, sync_media=False):
    version = sys.version_info[:2] if version is None else version
    if tuple(version) != (3, 11):
        raise SetupFailure("Python 3.11 is required for the verified environment.")
    root = root.resolve()
    if not all((root / name).is_file() for name in ("requirements.txt", "pyproject.toml", ".env.example")):
        raise SetupFailure("Run from a complete Anima repository checkout.")

    def command(args):
        completed = run([str(arg) for arg in args], cwd=root, check=False)
        if completed.returncode:
            raise SetupFailure(f"A setup step failed (exit {completed.returncode}). Resolve the error above and rerun.")

    environment = root / ".venv"
    interpreter = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not environment.exists():
        print("Creating project .venv...", flush=True)
        command([python, "-m", "venv", environment])
    elif not interpreter.is_file():
        raise SetupFailure("Existing .venv is incomplete; move it aside manually before retrying. Nothing was deleted.")
    command([interpreter, "-c", "import sys; assert sys.version_info[:2] == (3, 11), 'Existing .venv must use Python 3.11'"])
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.setup_chatterbox import install_torch
    install_torch(interpreter, command, probe=lambda args, **kwargs: run(args, cwd=root, **kwargs))
    command([interpreter, "-m", "pip", "install", "-r", root / "requirements.txt"])
    from scripts.setup_chatterbox import install_onnx_gpu
    install_onnx_gpu(interpreter, command)
    command([interpreter, "-m", "pip", "install", "-e", ".", "--no-deps", "--no-build-isolation"])
    command([interpreter, "-m", "pip", "check"])
    # Keep the app's existing private asset paths; the Git repository holds one
    # LFS archive, not loose extraction sources or an environment-specific venv.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.anima_assets import install_runtime
    try:
        install_runtime(root, command, sync=sync_media)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SetupFailure(str(error)) from None
    template, destination = root / ".env.example", root / ".env"
    if not destination.exists():
        # Exclusive creation also prevents a second setup from overwriting keys.
        try:
            with destination.open("xb") as target, template.open("rb") as source:
                shutil.copyfileobj(source, target)
        except FileExistsError:
            pass
    command([interpreter, "-m", "npc.rag", "prepare"])
    command([interpreter, "-m", "npc.adapters.skyrim.whisper", "--prepare"])
    command([interpreter, "-m", "pytest", "-q"])
    from scripts.check_environment import record_ready
    record_ready(root)
    print("\nAnima setup complete. Try: run_anima.bat --offline", flush=True)
    print("For live calls, set the model and matching API key in your local .env, then run run_anima.bat.")
    print("The private project runtime pack is installed when present; see SETUP.md for Git LFS and local overrides.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-media", action="store_true", help="Replace local runtime media with the shared verified bundle; preserves .env/player state")
    parser.add_argument("--no-update", action="store_true", help="Use local source without attempting a fast-forward Git pull")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.no_update:
        from source_update import update_source
        update_source(root)
        return subprocess.run([sys.executable, str(root / "scripts/setup_environment.py"),
                               "--no-update", *(["--sync-media"] if args.sync_media else [])],
                              cwd=root, check=False).returncode
    try:
        setup(root, sync_media=args.sync_media)
        return 0
    except (SetupFailure, OSError) as error:
        print(f"Setup error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
