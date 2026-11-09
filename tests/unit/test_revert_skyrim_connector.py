import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("install_skyrim_connector")
reverter = load("revert_skyrim_connector")
PACKAGED = "[Connection]\nPort=8768\n"


def package(path, tag):
    with zipfile.ZipFile(path, "w") as archive:
        for name in installer.COPIED:
            archive.writestr(name, tag)
        archive.writestr(installer.INI, PACKAGED)
    return path


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "game"
    plugins = root / "Data/SKSE/Plugins"
    plugins.mkdir(parents=True)
    (plugins / "AnimaConnector.dll").write_bytes(b"v1")
    (plugins / "AnimaConnector.ini").write_text("[Connection]\nPort=9000\n", encoding="utf-8")
    return root


def data(game, name):
    return game / "Data" / name


def test_revert_restores_old_files_and_removes_added_ones(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    assert data(game, "Anima.esp").exists() and data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v2"
    actions = reverter.revert(game, root=root)
    assert data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v1"
    assert "Port=9000" in data(game, "SKSE/Plugins/AnimaConnector.ini").read_text(encoding="utf-8")
    assert not data(game, "Anima.esp").exists() and not data(game, "Scripts/AnimaNative.pex").exists()
    assert any("remove" in action for action in actions)


def test_revert_can_itself_be_undone(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    reverter.revert(game, root=root)
    names = [name for name, _ in reverter.list_points(root)]
    assert any(name.startswith("pre-revert") for name in names)
    reverter.revert(game, to=[n for n in names if n.startswith("pre-revert")][0], root=root)
    assert data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v2"
    assert data(game, "Anima.esp").exists()


def test_two_installs_revert_one_step_at_a_time(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p2.zip", "v2"), console_exe=None, backup_root=root)
    next(root.iterdir()).rename(root / "pre-install-20200101-000001")
    installer.install(game, package=package(tmp_path / "p3.zip", "v3"), console_exe=None, backup_root=root)
    reverter.revert(game, root=root)
    assert data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v2"
    assert data(game, "Anima.esp").exists()


def test_dry_run_changes_nothing(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    before = {path.name: path.read_bytes() for path in (game / "Data").rglob("*") if path.is_file()}
    count = len(list(root.iterdir()))
    reverter.revert(game, root=root, dry_run=True)
    assert {path.name: path.read_bytes() for path in (game / "Data").rglob("*") if path.is_file()} == before
    assert len(list(root.iterdir())) == count


def test_older_revert_points_without_a_manifest_still_restore_the_plugin(game, tmp_path):
    root = tmp_path / "b"
    old = root / "pre-0.4.1-old"
    old.mkdir(parents=True)
    (old / "AnimaConnector.dll").write_bytes(b"v0")
    (old / "AnimaConnector.ini").write_text("[Connection]\nPort=1234\n", encoding="utf-8")
    reverter.revert(game, to="pre-0.4.1-old", root=root)
    assert data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v0"
    assert "Port=1234" in data(game, "SKSE/Plugins/AnimaConnector.ini").read_text(encoding="utf-8")


def test_unknown_point_and_missing_backups_are_rejected(game, tmp_path):
    with pytest.raises(ValueError, match="No revert points"):
        reverter.revert(game, root=tmp_path / "none")
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    with pytest.raises(ValueError, match="No revert point named"):
        reverter.revert(game, to="nope", root=root)


def test_a_damaged_revert_point_fails_before_changing_anything(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    point = next(p for p in root.iterdir())
    (point / "SKSE/Plugins/AnimaConnector.dll").unlink()
    with pytest.raises(ValueError, match="missing"):
        reverter.revert(game, root=root)
    assert data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v2"


def test_manifest_lists_what_was_added(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    manifest = json.loads((next(root.iterdir()) / "manifest.json").read_text(encoding="utf-8"))
    assert "Anima.esp" in manifest["added"] and "SKSE/Plugins/AnimaConnector.dll" in manifest["backed_up"]


def test_saves_in_the_same_second_never_overwrite_each_other(tmp_path):
    first = installer.unique_folder(tmp_path, "pre-install")
    first.mkdir()
    second = installer.unique_folder(tmp_path, "pre-install")
    assert first != second and not second.exists()


@pytest.mark.parametrize("bad_name", ["../outside.txt", "/tmp/outside", "Anima.esp/../outside", "unknown.esp"])
def test_manifest_cannot_write_outside_connector_files(game, tmp_path, bad_name):
    root = tmp_path / "b"
    point = root / "pre-install-test"
    point.mkdir(parents=True)
    (point / "manifest.json").write_text(json.dumps({"created": "today", "reason": "install", "backed_up": [], "added": [bad_name]}))
    before = {str(p): p.read_bytes() for p in game.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="manifest"):
        reverter.revert(game, root=root)
    assert {str(p): p.read_bytes() for p in game.rglob("*") if p.is_file()} == before
    assert list(root.iterdir()) == [point]


def test_missing_later_backup_fails_before_any_write_or_new_backup(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    point = next(root.iterdir())
    (point / installer.INI).unlink()
    before = {str(p): p.read_bytes() for p in game.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="missing"):
        reverter.revert(game, root=root)
    assert {str(p): p.read_bytes() for p in game.rglob("*") if p.is_file()} == before
    assert list(root.iterdir()) == [point]


def test_symlink_backup_cannot_restore_an_external_file(game, tmp_path):
    root = tmp_path / "b"
    installer.install(game, package=package(tmp_path / "p.zip", "v2"), console_exe=None, backup_root=root)
    point = next(root.iterdir())
    saved = point / "SKSE/Plugins/AnimaConnector.dll"
    external = tmp_path / "outside.dll"
    external.write_bytes(b"outside")
    saved.unlink()
    try:
        saved.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ValueError, match="unsafe source"):
        reverter.revert(game, root=root)
    assert data(game, "SKSE/Plugins/AnimaConnector.dll").read_bytes() == b"v2"
