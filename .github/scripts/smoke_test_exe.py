"""
ビルドした UartMonitor.exe を起動し、ウィンドウが開くこととアイコンサイズを確認する (Windows 専用)。

ウィンドウの大アイコンが 16px になっている (Tk が .ico のサイズを読めていない) とタスクバーでぼやけるため、
大アイコン >= 32px、小アイコン >= 16px であることを検査する。

使い方: python smoke_test_exe.py dist/UartMonitor/UartMonitor.exe
"""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

WINDOW_TITLE = "UartMonitor"
WM_GETICON, ICON_SMALL, ICON_BIG = 0x007F, 0, 1
SMTO_ABORTIFHUNG = 0x0002

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p),
    ]


user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]


def icon_width(hwnd, kind):
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_GETICON, kind, 0, SMTO_ABORTIFHUNG, 5000, ctypes.byref(result))
    if not result.value:
        return 0
    info = ICONINFO()
    if not user32.GetIconInfo(result.value, ctypes.byref(info)):
        return 0
    bmp = BITMAP()
    gdi32.GetObjectW(info.hbmColor or info.hbmMask, ctypes.sizeof(bmp), ctypes.byref(bmp))
    return bmp.bmWidth


def main():
    proc = subprocess.Popen([sys.argv[1]])
    try:
        hwnd = None
        deadline = time.time() + 30
        while time.time() < deadline and not hwnd:
            if proc.poll() is not None:
                sys.exit(f"exe exited early with code {proc.returncode}")
            hwnd = user32.FindWindowW(None, WINDOW_TITLE)
            time.sleep(0.5)
        if not hwnd:
            sys.exit(f"window '{WINDOW_TITLE}' did not appear within 30s")

        time.sleep(3)  # customtkinter の初期化(タイトルバー配色の再表示等)を待つ
        big, small = icon_width(hwnd, ICON_BIG), icon_width(hwnd, ICON_SMALL)
        print(f"window icon: big={big}px small={small}px")
        if big < 32 or small < 16:
            sys.exit("window icon is too small (taskbar icon would be blurry)")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
