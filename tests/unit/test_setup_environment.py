import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("anima_setup", Path(__file__).parents[2] / "scripts/setup_environment.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def checkout(tmp_path):
    (tmp_path / "requirements.txt").write_text("example==1.0")
    (tmp_path / "pyproject.toml").write_text("[project]")
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
    return tmp_path


def test_setup_installs_only_in_project_and_preserves_existing_key(tmp_path):
    root = checkout(tmp_path)
    (root / ".env").write_text("PRIVATE_EXISTING_KEY")
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        assert kwargs["cwd"] == root
        return SimpleNamespace(returncode=0)
    module.setup(root, python="base-python", version=(3, 11), run=run)
    assert commands[0] == ["base-python", "-m", "venv", str(root / ".venv")]
    assert all(str(root / ".venv") in args[0] for args in commands[1:] if args[0] != "nvidia-smi")
    assert commands[-1][-3:] == ["-m", "pytest", "-q"]
    assert (root / ".env").read_text() == "PRIVATE_EXISTING_KEY"
    whisper = next(i for i, args in enumerate(commands) if args[-3:] == ["-m", "npc.adapters.skyrim.whisper", "--prepare"])
    assert whisper == len(commands) - 2  # model prepared before offline regression


def test_setup_seeds_blank_configuration_and_stops_after_failure(tmp_path):
    root = checkout(tmp_path)
    module.setup(root, version=(3, 11), run=lambda *a, **k: SimpleNamespace(returncode=0))
    assert (root / ".env").read_bytes() == (root / ".env.example").read_bytes()
    # The subprocess fake above did not actually create a venv interpreter.
    interpreter = root / ".venv" / ("Scripts/python.exe" if module.sys.platform == "win32" else "bin/python")
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.touch()
    calls = []
    with pytest.raises(module.SetupFailure):
        module.setup(root, version=(3, 11), run=lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=1))
    assert len(calls) == 1  # aborts on the first failed subprocess; no install/test chain


def test_wrong_python_or_incomplete_checkout_never_runs_commands(tmp_path):
    def forbidden(*a, **k): raise AssertionError("must not execute")
    with pytest.raises(module.SetupFailure): module.setup(tmp_path, version=(3, 12), run=forbidden)
    with pytest.raises(module.SetupFailure): module.setup(tmp_path, version=(3, 11), run=forbidden)


def test_incomplete_environment_is_preserved_without_running_installers(tmp_path):
    root = checkout(tmp_path); (root / ".venv").mkdir()
    def forbidden(*a, **k): raise AssertionError("must not execute")
    with pytest.raises(module.SetupFailure, match="incomplete"):
        module.setup(root, version=(3, 11), run=forbidden)
    assert (root / ".venv").is_dir()


def test_setup_prepares_local_rag_before_regression_and_preserves_env_on_failure(tmp_path):
    root = checkout(tmp_path)
    (root / ".env").write_text("PRIVATE_EXISTING_KEY")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if args[-3:] == ["-m", "npc.rag", "prepare"] else 0)
    with pytest.raises(module.SetupFailure):
        module.setup(root, version=(3, 11), run=run)
    assert calls[-1][-3:] == ["-m", "npc.rag", "prepare"]
    assert not any(args[-3:] == ["-m", "pytest", "-q"] for args in calls)
    assert (root / ".env").read_text() == "PRIVATE_EXISTING_KEY"


def test_setup_installs_gpu_onnx_binaries_after_requirements(tmp_path):
    commands = []
    module.setup(checkout(tmp_path), version=(3, 11), run=lambda args, **kw: commands.append(args) or SimpleNamespace(returncode=0))
    requirements = next(i for i, args in enumerate(commands) if "-r" in args)
    gpu = next(i for i, args in enumerate(commands) if any("onnxruntime-gpu==1.23.2" in arg for arg in args))
    check = next(i for i, args in enumerate(commands) if args[-2:] == ["pip", "check"])
    assert requirements < gpu < check
