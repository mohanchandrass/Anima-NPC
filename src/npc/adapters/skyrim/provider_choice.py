"""Pick the provider profile that matches the model and key in .env.

A profile named `auto` (for example `configs/providers/auto.yaml`, which need not exist) selects OpenRouter when
the key or model clearly belongs to OpenRouter, and Gemini otherwise. Other names pass through unchanged, so an
explicit choice always wins. Only the key's prefix is looked at, and it is never logged.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

AUTO = "auto"


def looks_like_openrouter(env: Mapping[str, str]) -> bool:
    key = (env.get("OPENROUTER_API_KEY") or env.get("NPC_LLM_API_KEY") or "").strip().strip("'\"")
    model = (env.get("NPC_LLM_MODEL") or "").strip().strip("'\"")
    # Model identity wins over leftover credentials for another provider.
    if model.startswith(("gemini-", "models/gemini")):
        return False
    if "/" in model or model.endswith(":free"):
        return True
    common_key = (env.get("NPC_LLM_API_KEY") or "").strip().strip("'\"")
    if common_key.startswith("AIza"):
        return False
    return bool((env.get("OPENROUTER_API_KEY") or "").strip()) or key.startswith("sk-or-")


def choose_profile(requested: str, env: Mapping[str, str] | None = None, *, root: Path | None = None) -> str:
    """Return the profile path to load. `env` defaults to the repository .env merged under the process environment."""
    path = Path(requested)
    if path.stem != AUTO:
        return requested
    if env is None:
        local = dotenv_values((root or Path.cwd()) / ".env", interpolate=False)
        env = {**local, **os.environ}
    name = "openrouter.yaml" if looks_like_openrouter(env) else "gemini.yaml"
    return str(path.with_name(name)).replace("\\", "/")
