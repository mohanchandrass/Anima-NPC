"""Bounded, attributable Skyrim diagnostics; staging is distinct from delivery."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

LOGGER = logging.getLogger("npc.adapters.skyrim")


VERBOSE = False


def set_verbose(enabled: bool):
    """Verbose mode adds full model prompts to the console AND the log file (they can hold dialogue and game state)."""
    global VERBOSE
    VERBOSE = bool(enabled)


def event(kind: str, *, _limit: int = 1024, **fields):
    # JSON escapes newlines/ESC and keeps untrusted text on one terminal line.
    values = " ".join(f"{key}={json.dumps(str(value)[:_limit], ensure_ascii=True)}" for key, value in fields.items())
    LOGGER.info("%s %s", kind, values)


def verbose_event(kind: str, *, _limit: int = 6000, **fields):
    """Like event(), but emitted only with --verbose; console and file follow the same rule."""
    if VERBOSE:
        event(kind, _limit=_limit, **fields)


def shutdown():
    for handler in tuple(LOGGER.handlers):
        if getattr(handler, "_anima_owned", False):
            LOGGER.removeHandler(handler)
            handler.close()


def configure(log_file: str | None = None):
    shutdown()
    formatter = logging.Formatter("%(asctime)s [ANIMA] %(message)s", datefmt="%H:%M:%S")
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"))
    for handler in handlers:
        handler._anima_owned = True
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
