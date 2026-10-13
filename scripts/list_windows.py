"""List visible window titles related to LDPlayer / camera."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

titles: list[str] = []


def cb(hwnd, _lparam):  # type: ignore[no-untyped-def]
    if user32.IsWindowVisible(hwnd):
        n = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(n)
        user32.GetWindowTextW(hwnd, buf, n)
        t = buf.value
        if t and any(
            k in t for k in ("雷电", "LDPlayer", "得物", "摄像头", "dnplayer", "截取")
        ):
            titles.append(t)
    return True


user32.EnumWindows(WNDENUMPROC(cb), 0)
Path("screenshots/_wins.txt").write_text(
    "\n".join(titles) if titles else "(none)", encoding="utf-8"
)
