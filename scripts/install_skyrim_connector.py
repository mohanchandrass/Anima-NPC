"""Install the built connector into Skyrim's Data folder, keeping a backup you can revert to. Run with the game closed.

Copies the plugin, Anima.esp and scripts from output/skyrim-mod/AnimaConnector.zip, writes
AnimaConsole.ini, and merges new INI settings without losing existing ones. Every install saves
what it replaced, and what it added, so scripts/revert_skyrim_connector.py can undo it exactly.

    python scripts/install_skyrim_connector.py --game-dir "D:/steam/steamapps/common/Skyrim Special Edition"
    python scripts/install_skyrim_connector.py --game-dir ... --no-console
    python scripts/install_skyrim_connector.py --game-dir ... --dry-run
"""
from __future__ import annotations

import argparse
import configparser
import io
import json
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "output/skyrim-mod/AnimaConnector.zip"
CONSOLE_EXE = ROOT / "output/skyrim-mod/AnimaConsole.exe"
BACKUPS = ROOT / "output/skyrim-mod/backups"
COPIED = ("SKSE/Plugins/AnimaConnector.dll", "Anima.esp", "Scripts/AnimaNative.pex", "Scripts/AnimaStartDialogue.pex")
INI = "SKSE/Plugins/AnimaConnector.ini"
MANIFEST = "manifest.json"


def game_running(process: str = "SkyrimSE.exe") -> bool:
    result = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {process}", "/NH"], capture_output=True, text=True, check=False)
    return process.casefold() in result.stdout.casefold()


def merge_ini(installed: str, packaged: str, console_exe: Path | None) -> str:
    """Add settings the installed INI lacks. Existing values always win, except the console switch."""
    result = configparser.ConfigParser(interpolation=None, strict=False)
    result.optionxform = str
    result.read_string(packaged)
    current = configparser.ConfigParser(interpolation=None, strict=False)
    current.optionxform = str
    current.read_string(installed)
    for section in current.sections():
        if not result.has_section(section):
            result.add_section(section)
        for key, value in current.items(section):
            result.set(section, key, value)
    if console_exe is not None:
        if not result.has_section("Console"):
            result.add_section("Console")
        result.set("Console", "LaunchConsole", "1")
        result.set("Console", "ConsoleExe", str(console_exe))
    buffer = io.StringIO()
    result.write(buffer, space_around_delimiters=False)
    return buffer.getvalue()


def unique_folder(root: Path, prefix: str) -> Path:
    """A folder name that is never reused, so one save can never overwrite another."""
    base = root / time.strftime(f"{prefix}-%Y%m%d-%H%M%S")
    folder, number = base, 1
    while folder.exists():
        number += 1
        folder = base.with_name(f"{base.name}-{number}")
    return folder


def snapshot(data: Path, names, backup: Path, reason: str, *, dry_run: bool = False) -> dict:
    """Copy the current files to `backup` and record which did not exist, so a revert can remove them."""
    saved = [name for name in names if (data / name).is_file()]
    added = [name for name in names if not (data / name).exists()]
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason, "backed_up": saved, "added": added}
    if not dry_run:
        for name in saved:
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(data / name, backup / name)
        backup.mkdir(parents=True, exist_ok=True)
        (backup / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def install(game_dir: Path, *, package: Path = PACKAGE, console_exe: Path | None = CONSOLE_EXE, backup_root: Path | None = None, dry_run: bool = False) -> list[str]:
    data = game_dir / "Data"
    if not data.is_dir():
        raise ValueError(f"Not a game folder: {game_dir}")
    if not package.is_file():
        raise ValueError(f"{package} not found; run scripts/build_skyrim_connector.py first")
    if console_exe is not None and not console_exe.is_file():
        raise ValueError(f"{console_exe} not found; build first or pass --no-console")
    actions = []
    backup = unique_folder(backup_root or BACKUPS, "pre-install")
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        missing = [name for name in (*COPIED, INI) if name not in names]
        if missing:
            raise ValueError(f"Package lacks {missing}")
        manifest = snapshot(data, (*COPIED, INI), backup, "install", dry_run=dry_run)
        actions += [f"back up {data / name}" for name in manifest["backed_up"]]
        actions += [f"(new file, revert will remove it) {data / name}" for name in manifest["added"]]
        for name in COPIED:
            actions.append(f"install {data / name}")
            if not dry_run:
                (data / name).parent.mkdir(parents=True, exist_ok=True)
                (data / name).write_bytes(archive.read(name))
        ini_path = data / INI
        installed = ini_path.read_text(encoding="utf-8-sig") if ini_path.exists() else ""
        merged = merge_ini(installed, archive.read(INI).decode("utf-8-sig"), console_exe)
        actions.append(f"merge {ini_path}")
        if not dry_run:
            ini_path.write_text(merged, encoding="utf-8")
    if not dry_run:
        actions.append(f"saved a revert point: {backup}")
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--no-console", action="store_true", help="do not open the console window at game start")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and game_running():
        parser.exit(1, "Close Skyrim first: the plugin file is locked while the game runs.\n")
    exe = None if args.no_console else CONSOLE_EXE
    try:
        if exe is not None and not args.dry_run:
            ini = f"[Console]\nPython={ROOT / '.venv/Scripts/python.exe'}\nWorkingDir={ROOT}\nArguments=-m npc.adapters.skyrim --provider-profile configs/providers/gemini.yaml --local-speech --log-file output/skyrim/anima-console.log --console\n"
            (exe.parent / "AnimaConsole.ini").write_text(ini, encoding="utf-8")
        for action in install(args.game_dir, console_exe=exe, dry_run=args.dry_run):
            print(action)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print("Done. In Mod Organizer, tick Anima.esp in the right-hand Plugins list, then start the game.")
    print("To go back: python scripts/revert_skyrim_connector.py --game-dir <same folder> --list")


if __name__ == "__main__":
    main()
