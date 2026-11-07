"""capture_client must read the game through PrintWindow: BitBlt on a DirectX window returned black frames."""
import ctypes
from types import SimpleNamespace

import pytest

from npc.adapters.skyrim import windows

pytestmark = pytest.mark.skipif(not hasattr(ctypes, "WINFUNCTYPE"), reason="WINFUNCTYPE exists only on Windows")

PID, HWND, WIDTH, HEIGHT = 42, 100, 4, 2
PW_CLIENTONLY_RENDERFULLCONTENT = 3


class _Function:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def __call__(self, *args):
        return self.behaviour(*args)


class FakeLibrary:
    """Records calls by name. Anything without a configured result returns a non-null handle."""

    def __init__(self, calls, **behaviours):
        self._calls, self._behaviours = calls, behaviours

    def __getattr__(self, name):
        behaviour = self._behaviours.get(name, lambda *_: 7)

        def record(*args):
            self._calls.append((name, args))
            return behaviour(*args)

        function = _Function(record)
        setattr(self, name, function)
        return function


def fake_windows(monkeypatch, *, print_window=1, foreground=HWND):
    calls = []

    def thread_process(_, process):
        process._obj.value = PID
        return 1

    def client_rect(_, rectangle):
        rectangle._obj.right, rectangle._obj.bottom = WIDTH, HEIGHT
        return 1

    user = FakeLibrary(calls, EnumWindows=lambda visit, _: visit(HWND, 0), GetWindowThreadProcessId=thread_process,
                       IsWindowVisible=lambda _: 1, IsIconic=lambda _: 0, GetForegroundWindow=lambda: foreground,
                       GetClientRect=client_rect, PrintWindow=lambda *_: print_window)
    gdi = FakeLibrary(calls, GetDIBits=lambda *_: HEIGHT)
    libraries = {"user32": user, "gdi32": gdi}
    monkeypatch.setattr(windows.C, "WinDLL", lambda name, **_: libraries[name], raising=False)
    monkeypatch.setattr(windows, "os", SimpleNamespace(name="nt"))
    return calls


def names(calls):
    return [name for name, _ in calls]


def test_game_pixels_are_read_with_printwindow_full_content_rendering(monkeypatch):
    calls = fake_windows(monkeypatch)

    frame = windows.capture_client(PID)

    assert frame.startswith(b"\x89PNG\r\n\x1a\n")
    assert "BitBlt" not in names(calls)
    window, _, flags = next(args for name, args in calls if name == "PrintWindow")
    assert window == HWND and flags == PW_CLIENTONLY_RENDERFULLCONTENT


def test_failed_printwindow_raises_and_releases_every_gdi_resource(monkeypatch):
    calls = fake_windows(monkeypatch, print_window=0)

    with pytest.raises(OSError, match="game capture failed"):
        windows.capture_client(PID)

    called = names(calls)
    assert "GetDIBits" not in called
    assert all(release in called for release in ("DeleteObject", "DeleteDC", "ReleaseDC"))


def test_background_game_is_still_not_captured(monkeypatch):
    calls = fake_windows(monkeypatch, foreground=999)

    with pytest.raises(OSError, match="not the foreground"):
        windows.capture_client(PID)

    assert "PrintWindow" not in names(calls)
