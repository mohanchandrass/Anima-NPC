import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("install_skyrim_console", ROOT / "scripts/install_skyrim_console.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def test_ini_contents_and_connector_lines():
    ini = install.console_ini(Path("C:/p/python.exe"), Path("C:/w"))
    assert "Python=C:" in ini and "WorkingDir=C:" in ini
    assert "--local-speech" in ini and ini.rstrip().endswith("--console")
    lines = install.connector_lines(Path("C:/x/AnimaConsole.exe"))
    assert lines.startswith("[Console]\nLaunchConsole=1\nConsoleExe=") and lines.rstrip().endswith("AnimaConsole.exe")


@pytest.mark.parametrize("path", ["D:/steam/steamapps/common/Skyrim", "D:/MO2/mods/Anima", r"d:\mo2"])
def test_protected_folders_are_refused(path):
    with pytest.raises(ValueError):
        install.check_target(Path(path))


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["x", "--target-dir", str(tmp_path), "--dry-run"])
    install.main()
    assert not (tmp_path / "AnimaConsole.ini").exists()
    assert "Would write" in capsys.readouterr().out


def test_real_run_needs_exe_then_writes_ini(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--target-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as stopped:
        install.main()
    assert stopped.value.code == 1 and not (tmp_path / "AnimaConsole.ini").exists()
    (tmp_path / "AnimaConsole.exe").write_bytes(b"MZ")
    install.main()
    assert "[Console]" in (tmp_path / "AnimaConsole.ini").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["D:/steam-backup", "D:/mo2-notes", "E:/project/output"])
def test_similarly_named_unprotected_folders_are_allowed(path):
    install.check_target(Path(path))


def test_windows_parent_segments_cannot_bypass_protected_target():
    with pytest.raises(ValueError):
        install.check_target(Path("D:/other/../steam/Skyrim"))
