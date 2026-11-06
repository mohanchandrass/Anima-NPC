"""Go back to an earlier installed version of the connector. Run with the game closed.

Every install and every revert saves a revert point under output/skyrim-mod/backups, so you can
move in either direction. Reverting restores the files that existed and removes files the install
added (for example Anima.esp). Your save and the rest of the game folder are not touched.

    python scripts/revert_skyrim_connector.py --game-dir "D:/steam/steamapps/common/Skyrim Special Edition" --list
    python scripts/revert_skyrim_connector.py --game-dir ...                 revert the latest install
    python scripts/revert_skyrim_connector.py --game-dir ... --to NAME       revert to a specific revert point
    python scripts/revert_skyrim_connector.py --game-dir ... --dry-run

Revert points made before this script existed have no list of added files. Reverting to one restores
the plugin and INI it saved and leaves everything else as it is.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from install_skyrim_connector import BACKUPS, COPIED, INI, MANIFEST, game_running, snapshot, unique_folder

KNOWN = (*COPIED, INI)
LEGACY_FLAT = {"AnimaConnector.dll": "SKSE/Plugins/AnimaConnector.dll", "AnimaConnector.ini": INI}


def read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    files = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())
    flat = [name for name in files if name in LEGACY_FLAT]
    return {"created": time.strftime("%Y-%m-%d %H:%M", time.localtime(folder.stat().st_mtime)), "reason": "older revert point",
            "backed_up": files if not flat else [LEGACY_FLAT[name] for name in flat], "added": [], "legacy": True}


def list_points(root: Path = BACKUPS) -> list[tuple[str, dict]]:
    if not root.is_dir():
        return []
    return [(folder.name, read_manifest(folder)) for folder in sorted(root.iterdir()) if folder.is_dir()]


def source_for(folder: Path, name: str) -> Path:
    direct = folder / name
    if direct.is_file():
        return direct
    flat = folder / Path(name).name
    return flat if Path(name).name in LEGACY_FLAT and flat.is_file() else direct


def revert(game_dir: Path, *, to: str | None = None, root: Path = BACKUPS, dry_run: bool = False) -> list[str]:
    data = game_dir / "Data"
    if not data.is_dir():
        raise ValueError(f"Not a game folder: {game_dir}")
    points = list_points(root)
    if not points:
        raise ValueError(f"No revert points in {root}")
    names = [name for name, _ in points]
    if to is None:
        installs = [name for name, manifest in points if manifest.get("reason") == "install"]
        target = (installs or names)[-1]
    elif to in names:
        target = to
    else:
        raise ValueError(f"No revert point named {to!r}. Available: {', '.join(names)}")
    folder = root / target
    manifest = read_manifest(folder)
    actions = [f"reverting to {target} ({manifest['created']})"]
    # Save the current state first, so the revert itself can be undone.
    point = unique_folder(root, "pre-revert")
    snapshot(data, KNOWN, point, "revert", dry_run=dry_run)
    actions.append(f"saved the current files as {point.name}")
    for name in manifest["backed_up"]:
        source = source_for(folder, name)
        if not source.is_file():
            raise ValueError(f"Revert point {target} is missing {name}")
        actions.append(f"restore {data / name}")
        if not dry_run:
            (data / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, data / name)
    for name in manifest["added"]:
        if (data / name).exists():
            actions.append(f"remove {data / name}")
            if not dry_run:
                (data / name).unlink()
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--list", action="store_true", help="show the available revert points")
    parser.add_argument("--to", help="name of the revert point to restore (default: the latest install)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.list:
        for name, manifest in list_points():
            print(f"{name}   {manifest['created']}   {manifest['reason']}   restores {len(manifest['backed_up'])} files, removes {len(manifest['added'])}")
        return
    if not args.dry_run and game_running():
        parser.exit(1, "Close Skyrim first: the plugin file is locked while the game runs.\n")
    try:
        for action in revert(args.game_dir, to=args.to, dry_run=args.dry_run):
            print(action)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print("Done. If you removed Anima.esp, it is gone from the plugin list; the game runs without it.")


if __name__ == "__main__":
    main()
