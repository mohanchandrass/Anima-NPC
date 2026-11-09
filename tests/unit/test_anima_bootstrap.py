import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.check_environment import readiness, record_ready
from scripts.launch_anima import launch
from scripts.source_update import update_source
from tools.npc_lab import __main__ as app
from npc.generation.config import ConfigurationFailure
from npc.generation.provider import OfflineProvider


def prepared(root):
    (root / "requirements.txt").write_text("example==1.0\n")
    (root / "pyproject.toml").write_text("[project]\n")
    (root / ".env").write_text("LOCAL_SECRET")
    record_ready(root)
    return root


def ready(root, **kwargs):
    return readiness(root, version=(3, 11), installed=lambda name: "1.0",
                     find=lambda name: SimpleNamespace(origin=str(root / "src/npc/__init__.py")), **kwargs)


def test_readiness_detects_changed_setup_missing_packages_and_configuration(tmp_path):
    root = prepared(tmp_path)
    assert ready(root) is None
    assert readiness(root, version=(3, 11), installed=lambda _: "2.0") == "missing or outdated dependency"
    assert readiness(root, version=(3, 11), installed=lambda _: "1.0",
                     find=lambda _: SimpleNamespace(origin=str(root / "other/npc/__init__.py"))) == "project package missing or points to another checkout"
    (root / "requirements.txt").write_text("example==2.0")
    assert ready(root) == "setup inputs changed"
    (root / "requirements.txt").write_text("example==1.0")
    record_ready(root)
    (root / ".env").unlink()
    assert ready(root) == "local configuration template missing"
    assert readiness(root, version=(3, 12)) == "Python 3.11 environment required"


def test_readiness_missing_media_and_preserved_override(tmp_path):
    root = prepared(tmp_path)
    manifest = root / "assets/runtime/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"files": {"models/person.glb": {"bytes": 3}}}))
    destination = root / "assets/private/npc_lab"
    destination.mkdir(parents=True)
    (destination / ".installed-runtime.json").write_text('{"models/person.glb": "hash"}')
    record_ready(root)
    assert ready(root) == "runtime media missing or incomplete"
    model = destination / "models/person.glb"
    model.parent.mkdir()
    model.write_bytes(b"abc")
    assert ready(root) is None
    model.write_bytes(b"different-size")
    assert ready(root) == "runtime media missing or incomplete"
    (destination / ".installed-runtime.json").write_text('{}')
    assert ready(root) is None  # installer explicitly preserved this override
    (destination / ".installed-runtime.json").write_text('not-json')
    assert ready(root) == "installation incomplete"


@pytest.mark.parametrize("failure", ["dirty", "detached", "upstream", "network", "timeout", "missing-git", None])
def test_source_update_never_resets_or_merges_and_handles_offline(tmp_path, failure, capsys):
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        assert kwargs["timeout"] <= 45
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 45)
        if failure == "missing-git":
            raise FileNotFoundError()
        code = 1 if ((failure == "detached" and "symbolic-ref" in args) or
                     (failure == "upstream" and "rev-parse" in args) or
                     (failure == "network" and "pull" in args)) else 0
        output = " M local.py" if failure == "dirty" else ("" if "status" in args else "main\n")
        return SimpleNamespace(returncode=code, stdout=output)
    update_source(tmp_path, run=run)
    pulls = [args for args in commands if "pull" in args]
    assert bool(pulls) == (failure in {None, "network"})
    if pulls:
        assert pulls == [["git", "pull", "--ff-only", "--no-rebase", "--no-autostash"]]
    assert "PRIVATE" not in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("git") is None, reason="Git not installed; updater unit failure-path tests still run")
def test_real_fast_forward_and_dirty_work_preservation(tmp_path):
    def git(root, *args):
        return subprocess.run(["git", "-c", "user.name=Bootstrap Test", "-c", "user.email=test@example.invalid", *args],
                              cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    first, second = tmp_path / "first", tmp_path / "second"
    git(tmp_path, "clone", str(remote), str(first))
    (first / "content.txt").write_text("one")
    git(first, "add", "."); git(first, "commit", "-m", "initial"); git(first, "push", "-u", "origin", "main")
    git(tmp_path, "clone", str(remote), str(second))
    (first / "content.txt").write_text("two")
    git(first, "commit", "-am", "update"); git(first, "push")
    update_source(second)
    assert (second / "content.txt").read_text() == "two"
    assert git(first, "rev-parse", "HEAD") == git(second, "rev-parse", "HEAD")
    (second / "content.txt").write_text("teammate work")
    (first / "content.txt").write_text("three")
    git(first, "commit", "-am", "new update"); git(first, "push")
    update_source(second)
    assert (second / "content.txt").read_text() == "teammate work"
    assert git(first, "rev-parse", "HEAD") != git(second, "rev-parse", "HEAD")


@pytest.mark.parametrize("existing,results,expected", [
    (True, [0, 0], 0),
    (False, [0, 0, 0], 0),
    (False, [7], 7),
    (True, [1, 0, 1], 1),
])
def test_launcher_bootstraps_only_when_needed_and_stops_on_failure(tmp_path, existing, results, expected):
    import sys
    interpreter = tmp_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if existing:
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
    commands = []
    outcomes = iter(results)
    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=next(outcomes))
    assert launch(tmp_path, ["--offline", "--port", "8767"], run=run, python="base") == expected
    if expected == 0:
        assert commands[-1] == [str(interpreter), "-m", "tools.npc_lab", "--auto", "--open", "--offline", "--port", "8767"]
    else:
        assert all("tools.npc_lab" not in args for args in commands)
    if existing and results[0] == 0:
        assert len(commands) == 2


def test_provider_auto_fallback_is_labelled_and_explicit_live_errors(monkeypatch, capsys):
    def invalid(_):
        raise ConfigurationFailure("missing settings")
    monkeypatch.setattr(app, "load_provider", invalid)
    provider, live = app.choose_provider("auto", Path("profile.yaml"))
    assert isinstance(provider, OfflineProvider) and not live
    assert "OFFLINE SCRIPTED" in capsys.readouterr().out
    with pytest.raises(ConfigurationFailure):
        app.choose_provider("live", Path("profile.yaml"))
    provider, live = app.choose_provider("offline", Path("profile.yaml"))
    assert isinstance(provider, OfflineProvider) and not live
    sentinel = object()
    monkeypatch.setattr(app, "load_provider", lambda _: sentinel)
    assert app.choose_provider("auto", Path("profile.yaml")) == (sentinel, True)


def test_readiness_parses_active_requirement_markers_and_torch_build_tags(tmp_path):
    root = prepared(tmp_path)
    (root / "requirements.txt").write_text('example==1.0; python_version >= "3.11"\ntorch==2.6.0\n')
    record_ready(root)
    assert readiness(root, version=(3, 11), installed=lambda name: "2.6.0+cpu" if name == "torch" else "1.0",
        find=lambda name: SimpleNamespace(origin=str(root / "src/npc/__init__.py"))) is None


def test_readiness_does_not_require_packages_excluded_by_markers(tmp_path):
    root = prepared(tmp_path)
    (root / "requirements.txt").write_text('not-installed==1.0; python_version < "0"\nexample==1.0\n')
    record_ready(root)
    def installed(name):
        assert name == "example", "excluded requirements must never be queried"
        return "1.0"
    assert readiness(root, version=(3, 11), installed=installed,
        find=lambda name: SimpleNamespace(origin=str(root / "src/npc/__init__.py"))) is None


def test_readiness_invalidates_when_runtime_selection_setup_changes(tmp_path):
    root = prepared(tmp_path)
    helper = root / "scripts/setup_chatterbox.py"
    helper.parent.mkdir()
    helper.write_text("# first runtime selection\n")
    record_ready(root)
    assert ready(root) is None
    helper.write_text("# updated runtime selection\n")
    assert ready(root) == "setup inputs changed"
