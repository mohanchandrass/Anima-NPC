"""Visible-console support: colours Anima bridge lines and tails the native plugin log as [PLUGIN]."""

import logging
import os
from pathlib import Path
import re
import sys
import threading
import time

RESET, RED, CYAN, GREEN, YELLOW = "[0m", "[91m", "[96m", "[92m", "[93m"
_ERROR = re.compile(r"\[(error|critical)\]|\b(ERROR|CRITICAL|Traceback|FAILED)\b|_FAILED\b|_ERROR\b")
_WARN = re.compile(r"\[warning\]|\bWARNING\b|\bWARN\b")
LOG_NAME = "AnimaConnector.log"
WHITE, MAGENTA, BLUE = "[1;97m", "[95m", "[94m"
INPUT_CYAN = "[1;96m"  # bold cyan: what the player gave (plain cyan stays with plugin lines and native acks)
# Turn traffic gets its own colour so a conversation can be followed at a glance.
TRAFFIC = {"PLAYER_INPUT": INPUT_CYAN, "INPUT_SUBMITTED": INPUT_CYAN, "PIPELINE_RESULT": MAGENTA, "OUTPUT_STAGED": YELLOW + "[1m",
           "TURN_TIMING": BLUE, "TTS_ENGINE": BLUE, "AUDIO_OUTPUT": BLUE, "NATIVE_ACK": CYAN,
           "GAME_CONTEXT": WHITE, "BANNER": WHITE,
           "MODEL_INPUT": BLUE, "MODEL_PROMPT": BLUE, "MODEL_OUTPUT": GREEN, "VALIDATION": GREEN, "RETRIEVAL": GREEN,
           "REPAIR": YELLOW, "FALLBACK": YELLOW, "CV_UNAVAILABLE": YELLOW,
           "CV_REQUEST": MAGENTA, "CV_RESULT": MAGENTA, "CV_CONTEXT": MAGENTA, "SCREENSHOT": MAGENTA}
# Lines whose payload is dialogue, prompts or model text: words in it must not trigger the generic error/warning rules.
FREE_TEXT = frozenset({"PLAYER_INPUT", "INPUT_SUBMITTED", "MODEL_PROMPT", "MODEL_OUTPUT", "OUTPUT_STAGED"})
_KIND = re.compile(r"\]\s+([A-Z][A-Z_]+)")


def colour_line(line: str, source: str, color: bool = True) -> str:
    """Return the line with ANSI colour: errors red, warnings yellow, plugin cyan, bridge green (plain with color=False)."""
    line = line.rstrip("\r\n")
    prefix = f"[{source}] " if source == "PLUGIN" else ""
    if not color:
        return prefix + line
    kind = _KIND.search(line) if source != "PLUGIN" else None
    kind = kind.group(1) if kind else None
    if kind in FREE_TEXT:
        # Only the structured status fields decide red/yellow here, never the words a player or model produced.
        if kind == "MODEL_OUTPUT":
            colour = RED if re.search(r"\bstatus=\"(failed|cancelled)\"", line) else TRAFFIC[kind]
        else:
            colour = TRAFFIC[kind]
    elif _ERROR.search(line):
        colour = RED
    elif _WARN.search(line) or (kind == "SCREENSHOT" and " warning=" in line):
        colour = YELLOW
    else:
        colour = CYAN if source == "PLUGIN" else GREEN
        if kind in TRAFFIC:
            colour = TRAFFIC[kind]
    return f"{colour}{prefix}{line}{RESET}"


def documents_folder() -> Path | None:
    """The real Documents folder (OneDrive-redirected or not) from the Windows known-folder API."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    folder = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    result = ctypes.c_wchar_p()
    shell = ctypes.windll.shell32
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    if shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(result)) != 0:
        return None
    path = Path(result.value)
    ctypes.windll.ole32.CoTaskMemFree(result)
    return path


def plugin_log_path() -> Path | None:
    documents = documents_folder()
    return documents / "My Games/Skyrim Special Edition/SKSE" / LOG_NAME if documents else None


class LogTail:
    """Incremental line reader that survives a missing file, truncation and replacement."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._offset = 0
        self._identity = None
        self._partial = b""

    def read_new(self) -> list[str]:
        try:
            stat = self.path.stat()
        except OSError:
            self._offset, self._identity, self._partial = 0, None, b""
            return []
        identity = stat.st_ino  # file index on Windows too: detects replacement by a larger file
        if (self._identity is not None and identity != self._identity) or stat.st_size < self._offset:
            self._offset, self._partial = 0, b""  # rotated, replaced or truncated: start over
        self._identity = identity
        if stat.st_size == self._offset:
            return []
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self._offset)
                data = handle.read(stat.st_size - self._offset)
        except OSError:
            return []
        self._offset += len(data)
        chunks = (self._partial + data).split(b"\n")
        self._partial = chunks.pop()  # an unfinished last line waits for its newline
        return [chunk.decode("utf-8", "replace").rstrip("\r") for chunk in chunks if chunk.strip()]


class ColourFormatter(logging.Formatter):
    def format(self, record):
        return colour_line(super().format(record), "ANIMA")


def _enable_ansi():
    if os.name != "nt":
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel.SetConsoleMode(handle, mode.value | 0x0004)


def start_tail(path: Path | None, emit=None, interval: float = 0.25, color: bool = True):
    """Print new plugin log lines from a daemon thread; never raises into the bridge."""
    if path is None:
        return None
    emit = emit or (lambda text: print(text, flush=True))
    tail = LogTail(path)

    def run():
        while True:
            try:
                for line in tail.read_new():
                    emit(colour_line(line, "PLUGIN", color))
            except Exception:  # a logging aid must never take the bridge down
                pass
            time.sleep(interval)

    thread = threading.Thread(target=run, name="anima-plugin-log-tail", daemon=True)
    thread.start()
    return thread


def setup(logger: logging.Logger, title: str = "Anima Skyrim Bridge", color: bool = True):
    """Title, ANSI colours for bridge lines (unless color=False), and the [PLUGIN] tail. Call after diagnostics.configure()."""
    if color:
        _enable_ansi()
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    for handler in logger.handlers if color else ():
        if type(handler) is logging.StreamHandler and handler.stream is sys.stdout:
            handler.setFormatter(ColourFormatter("%(asctime)s [ANIMA] %(message)s", datefmt="%H:%M:%S"))
    return start_tail(plugin_log_path(), color=color)
