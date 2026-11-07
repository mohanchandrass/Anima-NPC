import importlib.util
import zipfile
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts/install_skyrim_connector.py"
spec = importlib.util.spec_from_file_location("install_skyrim_connector", PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

PACKAGED = "[Connection]\nPort=8768\nChatKey=35\n\n[Console]\nLaunchConsole=0\nConsoleExe=\n"


def package(path):
    with zipfile.ZipFile(path, "w") as archive:
        for name in installer.COPIED:
            archive.writestr(name, b"new")
        archive.writestr(installer.INI, PACKAGED)
    return path


def test_merge_keeps_existing_values_and_adds_new_ones():
    merged = installer.merge_ini("[Connection]\nPort=9000\n", PACKAGED, None)
    assert "Port=9000" in merged and "ChatKey=35" in merged and "LaunchConsole=0" in merged


def test_merge_migrates_the_old_default_chat_key(capsys):
    merged = installer.merge_ini("[Connection]\nChatKey=64\nPort=9000\n", PACKAGED, None)
    assert "ChatKey=35" in merged and "ChatKey=64" not in merged and "Port=9000" in merged
    assert "Migrated ChatKey=64" in capsys.readouterr().out


def test_merge_keeps_a_deliberately_chosen_chat_key(capsys):
    merged = installer.merge_ini("[Connection]\nChatKey=62\n", PACKAGED, None)
    assert "ChatKey=62" in merged and "Migrated" not in capsys.readouterr().out


def test_merge_turns_console_on_with_its_path():
    merged = installer.merge_ini("", PACKAGED, Path("C:/x/AnimaConsole.exe"))
    assert "LaunchConsole=1" in merged and "AnimaConsole.exe" in merged


def test_install_backs_up_then_replaces(tmp_path):
    game = tmp_path / "game"
    plugins = game / "Data/SKSE/Plugins"
    plugins.mkdir(parents=True)
    (plugins / "AnimaConnector.dll").write_bytes(b"old")
    (plugins / "AnimaConnector.ini").write_text("[Connection]\nPort=9000\n", encoding="utf-8")
    exe = tmp_path / "AnimaConsole.exe"
    exe.write_bytes(b"exe")
    installer.install(game, package=package(tmp_path / "p.zip"), console_exe=exe, backup_root=tmp_path / "b")
    assert (plugins / "AnimaConnector.dll").read_bytes() == b"new"
    assert (game / "Data/Anima.esp").read_bytes() == b"new"
    assert (game / "Data/Scripts/AnimaStartDialogue.pex").is_file()
    ini = (plugins / "AnimaConnector.ini").read_text(encoding="utf-8")
    assert "Port=9000" in ini and "LaunchConsole=1" in ini
    assert list((tmp_path / "b").rglob("AnimaConnector.dll"))[0].read_bytes() == b"old"


def test_dry_run_changes_nothing(tmp_path):
    game = tmp_path / "game"
    (game / "Data").mkdir(parents=True)
    actions = installer.install(game, package=package(tmp_path / "p.zip"), console_exe=None, dry_run=True, backup_root=tmp_path / "b")
    assert actions and not (game / "Data/Anima.esp").exists() and not (tmp_path / "b").exists()


def test_rejects_a_folder_that_is_not_a_game(tmp_path):
    with pytest.raises(ValueError, match="Not a game folder"):
        installer.install(tmp_path, package=package(tmp_path / "p.zip"), console_exe=None)


def test_rejects_a_package_with_missing_files(tmp_path):
    (tmp_path / "Data").mkdir()
    with zipfile.ZipFile(tmp_path / "p.zip", "w") as archive:
        archive.writestr("Anima.esp", b"x")
    with pytest.raises(ValueError, match="lacks"):
        installer.install(tmp_path, package=tmp_path / "p.zip", console_exe=None)
