"""Windows-only device boundaries, loaded lazily so replay/tests work anywhere."""

import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import threading

from .assets import png_from_bgra


PW_CLIENTONLY_RENDERFULLCONTENT = 0x1 | 0x2


def capture_client(pid: int) -> bytes:
    """Capture only the foreground game client. Borderless mode is recommended.

    PrintWindow with full-content rendering reads the DirectX surface; BitBlt from the window DC returned black.
    This process must stay DPI-unaware: the client size then matches the frame the game draws, whereas a DPI-aware
    caller gets a larger bitmap with the game in one corner and the rest black.
    """
    if os.name != "nt":
        raise OSError("game capture requires Windows")
    user = C.WinDLL("user32", use_last_error=True)
    gdi = C.WinDLL("gdi32", use_last_error=True)
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.EnumWindows.argtypes = [callback_type, W.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user.IsWindowVisible.argtypes = [W.HWND]
    user.IsIconic.argtypes = [W.HWND]
    user.GetForegroundWindow.restype = W.HWND
    user.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user.GetDC.argtypes, user.GetDC.restype = [W.HWND], W.HDC
    user.ReleaseDC.argtypes = [W.HWND, W.HDC]
    user.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [W.HDC], W.HDC
    gdi.CreateCompatibleBitmap.argtypes, gdi.CreateCompatibleBitmap.restype = [W.HDC, C.c_int, C.c_int], W.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [W.HDC, W.HGDIOBJ], W.HGDIOBJ
    gdi.DeleteObject.argtypes = [W.HGDIOBJ]
    gdi.DeleteDC.argtypes = [W.HDC]
    gdi.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
    windows = []

    @callback_type
    def visit(hwnd, _):
        process = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, C.byref(process))
        if process.value == pid and user.IsWindowVisible(hwnd) and not user.IsIconic(hwnd):
            windows.append(hwnd)
        return True

    user.EnumWindows(visit, 0)
    hwnd = user.GetForegroundWindow()
    if hwnd not in windows:
        raise OSError("Skyrim is not the foreground visible window")
    rectangle = W.RECT()
    if not user.GetClientRect(hwnd, C.byref(rectangle)):
        raise OSError("cannot read game client bounds")
    width, height = rectangle.right, rectangle.bottom
    if not 0 < width <= 4096 or not 0 < height <= 2160:
        raise OSError("unsupported game capture size (maximum 4096x2160)")

    class BitmapHeader(C.Structure):
        _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                    ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                    ("image_size", W.DWORD), ("x", W.LONG), ("y", W.LONG),
                    ("used", W.DWORD), ("important", W.DWORD)]

    source = user.GetDC(hwnd)
    target = gdi.CreateCompatibleDC(source) if source else None
    bitmap = gdi.CreateCompatibleBitmap(source, width, height) if target else None
    previous = None
    try:
        if not source or not target or not bitmap:
            raise OSError("cannot allocate game capture surface")
        previous = gdi.SelectObject(target, bitmap)
        if not user.PrintWindow(hwnd, target, PW_CLIENTONLY_RENDERFULLCONTENT):
            raise OSError("game capture failed")
        # GetDIBits requires the bitmap to be deselected from the memory DC.
        gdi.SelectObject(target, previous)
        previous = None
        header = BitmapHeader(40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        raw = C.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(source, bitmap, 0, height, raw, C.byref(header), 0) != height:
            raise OSError("cannot read captured game pixels")
        return png_from_bgra(width, height, raw.raw)
    finally:
        if previous:
            gdi.SelectObject(target, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if target:
            gdi.DeleteDC(target)
        if source:
            user.ReleaseDC(hwnd, source)


class Microphone:
    """Push-to-talk PCM capture, maximum 15 seconds; transcription is external."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.active = False
        self._lock = threading.RLock()

    @staticmethod
    def _command(command):
        if os.name != "nt":
            raise OSError("microphone capture requires Windows")
        winmm = C.WinDLL("winmm")
        winmm.mciSendStringW.argtypes = [W.LPCWSTR, W.LPWSTR, W.UINT, W.HWND]
        if winmm.mciSendStringW(command, None, 0, None):
            raise OSError("Windows wave recording command failed")

    def start(self):
        with self._lock:
            if self.active:
                raise ValueError("microphone already active")
            self.directory.mkdir(parents=True, exist_ok=True)
            self._command("open new type waveaudio alias anima_connector_mic")
            try:
                self._command("set anima_connector_mic time format milliseconds bitspersample 16 channels 1 samplespersec 16000 alignment 2 bytespersec 32000")
                self._command("record anima_connector_mic to 15000")
                self.active = True
            except BaseException:
                self._command("close anima_connector_mic")
                raise

    def stop(self) -> bytes:
        with self._lock:
            if not self.active:
                raise ValueError("microphone is not active")
            path = (self.directory / "input.wav").resolve()
            if '"' in str(path):
                raise ValueError("invalid recording directory")
            try:
                self._command("stop anima_connector_mic")
                self._command(f'save anima_connector_mic "{path}"')
                with path.open("rb") as stream:
                    return stream.read(512 * 1024)
            finally:
                self.active = False
                self._command("close anima_connector_mic")
                path.unlink(missing_ok=True)

    def discard(self):
        # Wait for any in-flight native start call before deciding whether to close.
        with self._lock:
            if self.active:
                self.stop()
