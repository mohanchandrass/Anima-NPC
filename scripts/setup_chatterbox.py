"""One project environment, automatic Turbo download and installed-game references."""

import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def torch_backend(probe=subprocess.run):
    try:
        result = probe(["nvidia-smi", "--query-gpu=driver_version,compute_cap", "--format=csv,noheader"],
                       capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines()[:1]:
                driver, capability = line.split(",")
                # Conservative CUDA 12.4 admission. Torch 2.6 does not support Blackwell.
                if int(driver.strip().split(".")[0]) >= 552 and 6 <= float(capability) < 10:
                    return "cu124"
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        pass
    return "cpu"


def install_torch(interpreter, command, *, probe=subprocess.run):
    backend = torch_backend(probe)
    print(f"Preparing Torch runtime: {backend} (no separate CUDA toolkit installation).", flush=True)
    command([interpreter, "-m", "pip", "install", "--upgrade", "torch==2.6.0", "torchaudio==2.6.0",
             "--index-url", f"https://download.pytorch.org/whl/{backend}",
             "--extra-index-url", "https://pypi.org/simple"])


def install_onnx_gpu(interpreter, command):
    # Chroma requires CPU-distribution metadata; both wheels share import files.
    # Reinstall GPU binaries last so pip ordering cannot silently select CPU.
    command([interpreter, "-c", "import platform,subprocess,sys; subprocess.run([sys.executable,'-m','pip','install','--force-reinstall','--no-deps','onnxruntime-gpu==1.23.2'],check=True) if platform.machine().lower() in ('amd64','x86_64') else None"])


def find_game(root):
    config = root / "configs/skyrim/connector.json"
    if config.is_file():
        raw = json.loads(config.read_text(encoding="utf-8"))
        path = (root / raw["game_directory"]).resolve()
        if (path / "Data/Skyrim.esm").is_file():
            return path
    guesses = [root.parent / "TES - Skyrim - Anniversary Edition"]
    if sys.platform == "win32":
        import winreg
        for key in (r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
                    r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                    guesses.append(Path(winreg.QueryValueEx(handle, "Installed Path")[0]))
            except OSError:
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as handle:
                steam = Path(winreg.QueryValueEx(handle, "SteamPath")[0])
            libraries = [steam]
            folders = steam / "steamapps/libraryfolders.vdf"
            if folders.is_file() and folders.stat().st_size < 1024 * 1024:
                libraries += [Path(value.replace("\\\\", "\\")) for value in
                    re.findall(r'"path"\s*"([^"\n]+)"', folders.read_text(encoding="utf-8"))]
            guesses += [library / "steamapps/common/Skyrim Special Edition" for library in libraries]
        except OSError:
            pass
    return next((path for path in guesses if (path / "Data/Skyrim.esm").is_file()), None)


def configure_game(root, game):
    config = root / "configs/skyrim/connector.json"
    template = config.with_name("connector.example.json")
    source = config if config.is_file() else template
    if not source.is_file():
        raise ValueError("connector configuration template missing from checkout")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if config.is_file() and (root / raw["game_directory"]).resolve() == game.resolve():
        return
    raw["game_directory"] = str(game.resolve())
    temporary = config.with_suffix(".tmp")
    temporary.write_text(json.dumps(raw, indent=2), encoding="utf-8"); temporary.replace(config)


def setup(root, *, game=None, run=subprocess.run):
    interpreter = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not interpreter.is_file():
        raise ValueError("Run setup.bat once to create the project environment")
    def command(args):
        result = run([str(arg) for arg in args], cwd=root, check=False)
        if result.returncode:
            raise ValueError("Chatterbox setup step failed; resolve the displayed error and rerun")
    install_torch(interpreter, command, probe=lambda args, **kwargs: run(args, cwd=root, **kwargs))
    command([interpreter, "-m", "pip", "install", "-r", root / "requirements.txt"])
    install_onnx_gpu(interpreter, command)
    command([interpreter, "-m", "pip", "install", "-e", ".", "--no-deps", "--no-build-isolation"])
    command([interpreter, "-m", "pip", "check"])
    command([interpreter, "-m", "npc.output.chatterbox", "--prepare"])
    game = game or find_game(root)
    if game:
        sys.path.insert(0, str(root))
        from scripts.chatterbox_voices import prepare_voices
        count = prepare_voices(root, game)
        print(f"Prepared {count} Skyrim voice references.")
        if not count:
            raise ValueError("No usable Skyrim voices found; check installed English voice archives")
    else:
        raise ValueError("Turbo model is installed, but Skyrim was not found. Rerun setup_chatterbox.bat --game-directory \"your Skyrim folder\" to prepare NPC voices")
    command([interpreter, "-m", "pytest", "-q"])
    configure_game(root, game)
    marker = root / "data/tts/.chatterbox-ready.json"
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"model": "chatterbox-turbo", "voices": count}))
    temporary.replace(marker)
    from scripts.check_environment import record_ready
    record_ready(root)
    print("Chatterbox setup complete. Start run_skyrim_connector.bat normally.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-directory", type=Path)
    args = parser.parse_args()
    try:
        setup(ROOT, game=args.game_directory)
        return 0
    except (ValueError, OSError) as error:
        print(f"Chatterbox setup: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
