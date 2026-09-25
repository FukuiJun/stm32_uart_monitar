"""
ビルドした UartMonitor.exe を起動し、ウィンドウが開くこととアイコンの画質を確認する (Windows 専用)。

Tk が .ico の各サイズを読めないと、16px の画像を引き伸ばしたアイコンがウィンドウに設定され
タスクバーでぼやける。ウィンドウのアイコン(大・小)が、同梱の icon.ico を同じサイズで
読み込んだ画像とピクセル単位で一致することを検査する。

1回目: 同梱の icon.ico のまま起動する。
2回目: icon.ico を Tk が読めない PNG 形式に差し替えたコピーで起動する。Tk だけでは 16px を
引き伸ばしたアイコンになるため、アプリ側の Win32 API による設定 (_apply_win_icons) が
実際に効いていることを確認できる (CI は 100% 表示で、通常は Tk と結果が同じになるため)。
2回目には Pillow が必要。

使い方: python smoke_test_exe.py dist/UartMonitor/UartMonitor.exe
"""

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

WINDOW_TITLE = "UartMonitor"
WM_GETICON, ICON_SMALL, ICON_BIG = 0x007F, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
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


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]


def icon_pixels(hicon):
    """HICON のカラー画像を (幅, 高さ, 32bpp BGRA バイト列) で返す。"""
    info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return 0, 0, b""
    try:
        bmp = BITMAP()
        gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bmp), ctypes.byref(bmp))
        w, h = bmp.bmWidth, bmp.bmHeight
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32)
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(None)
        gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf, ctypes.byref(bih), 0)
        user32.ReleaseDC(None, hdc)
        return w, h, buf.raw
    finally:
        gdi32.DeleteObject(info.hbmColor)
        gdi32.DeleteObject(info.hbmMask)


def check_window_icon(hwnd, kind, label, ico_path):
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_GETICON, kind, 0, SMTO_ABORTIFHUNG, 5000, ctypes.byref(result))
    if not result.value:
        return f"{label}: no icon set"
    w, h, actual = icon_pixels(result.value)
    expected_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, w, h, LR_LOADFROMFILE)
    _, _, expected = icon_pixels(expected_icon)
    print(f"{label}: {w}x{h}px, matches icon.ico: {actual == expected}")
    if actual != expected:
        return f"{label}: differs from icon.ico at {w}px (stretched from another size -> blurry)"
    return None


def run_check(exe_path):
    """exe を起動してアイコンを検査し、エラーメッセージのリストを返す。"""
    proc = subprocess.Popen([exe_path])
    try:
        hwnd = None
        deadline = time.time() + 30
        while time.time() < deadline and not hwnd:
            if proc.poll() is not None:
                return [f"exe exited early with code {proc.returncode}"]
            hwnd = user32.FindWindowW(None, WINDOW_TITLE)
            time.sleep(0.5)
        if not hwnd:
            return [f"window '{WINDOW_TITLE}' did not appear within 30s"]

        time.sleep(3)  # customtkinter の初期化(タイトルバー配色の再表示等)を待つ
        ico_path = bundled_ico(exe_path)
        return [
            e for e in (
                check_window_icon(hwnd, ICON_BIG, "big icon", ico_path),
                check_window_icon(hwnd, ICON_SMALL, "small icon", ico_path),
            ) if e
        ]
    finally:
        proc.kill()
        proc.wait()


def bundled_ico(exe_path):
    return os.path.abspath(os.path.join(os.path.dirname(exe_path), "_internal", "assets", "icon.ico"))


def main():
    exe_path = os.path.abspath(sys.argv[1])

    print("[1] bundled icon.ico")
    errors = run_check(exe_path)

    print("[2] icon.ico replaced with PNG entries (Tk cannot read sizes; Win32 path must handle it)")
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        app_copy = os.path.join(tmp, "UartMonitor")
        shutil.copytree(os.path.dirname(exe_path), app_copy)
        exe_copy = os.path.join(app_copy, os.path.basename(exe_path))
        ico = Image.open(bundled_ico(exe_path))
        sizes = sorted(ico.info["sizes"])
        ico.save(bundled_ico(exe_copy), format="ICO", sizes=sizes)  # Pillow の既定は PNG 形式
        errors += [f"[PNG ico] {e}" for e in run_check(exe_copy)]

    if errors:
        sys.exit("\n".join(errors))


if __name__ == "__main__":
    main()
