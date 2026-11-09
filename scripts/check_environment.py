"""Cheap local readiness check; never installs packages or calls a provider."""

import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ("requirements.txt", "pyproject.toml", "assets/runtime/manifest.json",
          "scripts/setup_environment.py", "scripts/anima_assets.py",
          "scripts/check_environment.py", "scripts/setup_chatterbox.py")


def fingerprint(root):
    checksum = hashlib.sha256()
    for name in INPUTS:
        checksum.update(name.encode())
        path = root / name
        checksum.update(path.read_bytes() if path.is_file() else b"missing")
    return checksum.hexdigest()


def record_ready(root):
    target = root / ".venv/.anima-ready.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"fingerprint": fingerprint(root)}), encoding="utf-8")
    temporary.replace(target)


def readiness(root, *, version=None, installed=importlib.metadata.version, find=importlib.util.find_spec):
    if tuple(version or sys.version_info[:2]) != (3, 11):
        return "Python 3.11 environment required"
    try:
        marker = json.loads((root / ".venv/.anima-ready.json").read_text(encoding="utf-8"))
        if marker.get("fingerprint") != fingerprint(root):
            return "setup inputs changed"
        from packaging.requirements import Requirement
        for line in (root / "requirements.txt").read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            requirement = Requirement(line)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            name = requirement.name
            actual = installed(name)
            # CPU/CUDA wheel tags are hardware builds of the same pinned Torch release.
            if name.lower() in {"torch", "torchaudio"}:
                actual = actual.split("+", 1)[0]
            if not requirement.specifier.contains(actual, prereleases=True):
                return "missing or outdated dependency"
        project = find("npc")
        if project is None or not project.origin or not Path(project.origin).resolve().is_relative_to((root / "src/npc").resolve()):
            return "project package missing or points to another checkout"
        if not (root / ".env").is_file():
            return "local configuration template missing"
        manifest = root / "assets/runtime/manifest.json"
        if manifest.is_file():
            files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
            destination = root / "assets/private/npc_lab"
            managed = json.loads((destination / ".installed-runtime.json").read_text(encoding="utf-8"))
            # Existing overrides are deliberately retained, matching the installer.
            # Full SHA verification happens in setup, not on every game-side launch.
            for name, entry in files.items():
                path = destination / name
                if not path.is_file() or (name in managed and path.stat().st_size != entry["bytes"]):
                    return "runtime media missing or incomplete"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        return "installation incomplete"
    return None


if __name__ == "__main__":
    reason = readiness(ROOT)
    if reason:
        print(f"Anima setup needed: {reason}.")
    raise SystemExit(1 if reason else 0)
