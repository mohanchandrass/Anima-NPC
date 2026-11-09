"""Prepare AnimaConsole.ini next to AnimaConsole.exe and print the AnimaConnector.ini lines.

Never touches the game or mod-manager folders: only writes the INI in --target-dir (default output/skyrim-mod).
"""

import argparse
import ntpath
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARGUMENTS = ("-m npc.adapters.skyrim --provider-profile configs/providers/auto.yaml --local-speech "
             "--log-file output/skyrim/anima-console.log --console")
PROTECTED = ("d:/steam", "d:/mo2")


def console_ini(python: Path, working_dir: Path, arguments: str = ARGUMENTS) -> str:
    return f"[Console]\nPython={python}\nWorkingDir={working_dir}\nArguments={arguments}\n"


def connector_lines(exe: Path) -> str:
    return f"[Console]\nLaunchConsole=1\nConsoleExe={exe}\n"


def check_target(target: Path):
    # Inspect the original spelling as well as the host-resolved path. On Linux
    # Path.resolve treats a Windows drive path as a relative POSIX filename.
    paths = (str(target), str(target.resolve()))
    normalised = [ntpath.normpath(path).replace("\\", "/").lower() for path in paths]
    if any(path == prefix or path.startswith(prefix + "/") for path in normalised for prefix in PROTECTED):
        raise ValueError(f"Refusing to write inside {target}: copy the INI by hand into the game/mod folder")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", type=Path, default=ROOT / "output/skyrim-mod", help="folder holding AnimaConsole.exe")
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/Scripts/python.exe")
    parser.add_argument("--dry-run", action="store_true", help="print what would be written")
    args = parser.parse_args()
    try:
        check_target(args.target_dir)
        target = args.target_dir.resolve()
        ini = console_ini(args.python.resolve(), ROOT, ARGUMENTS)
        exe = target / "AnimaConsole.exe"
        if args.dry_run:
            print(f"Would write {target / 'AnimaConsole.ini'}:\n{ini}")
        else:
            if not exe.is_file():
                raise ValueError(f"{exe} not found; run scripts/build_skyrim_connector.py first or pass --target-dir")
            (target / "AnimaConsole.ini").write_text(ini, encoding="utf-8")
            print(f"Wrote {target / 'AnimaConsole.ini'}")
        print("Add to AnimaConnector.ini in the installed SKSE/Plugins folder:\n" + connector_lines(exe))
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
