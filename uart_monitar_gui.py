"""
UartMonitor: UARTログをGUIで表示しながらCSVに保存するツール（customtkinter版・計測機器風/水色デザイン）。

機能:
    - 起動時にCOMポートを自動検出し、それらしいポート（ST-Link等）を自動選択
    - ボーレートをプルダウンから選択
    - 出力ファイル名のベース名を指定可能（初期値: "mcu_log" → mcu_log1.csv, mcu_log2.csv, ...）
    - OUTPUT欄の「参照...」でCSVの保存先フォルダを選択可能（初期値: exeと同じフォルダ）。
      選んだフォルダは UartMonitor_settings.json に保存され、次回起動時も使われる
    - INTERVAL欄で、timeに加算する固定値(ms)を指定可能（初期値: 1000）
    - 「接続」ボタンで通信開始、押すと「切断」に切り替わる単一のトグルボタン
    - ウィンドウにログをリアルタイム表示
    - 受信データは自動的にCSV(指定したベース名+連番)へ保存
    - CSVは pc_timestamp, t_ms, elapsed_ms の後に、受信した項目の列が続く通常の表形式
      (現在のフォーマットでは voltage_mV, current_mA, cap_mAh, cap_max_mAh, soc_percent,
      soh_percent, temp_C。列は接続後に最初に受信した行の項目で決まる)
    - 2番目の値(t_ms)以降は「ラベル, 値」の組として読むため、マイコン側で項目を追加・削除・
      並べ替えしても time の付与とCSV保存が続く。知らないラベルはラベル名をそのまま列名にする
    - 経過時間(elapsed_ms)は実時間ではなく、INTERVAL欄で指定した固定値を加算幅として使う(1行目は0)
    - ログ表示・CSVの両方に経過時間を追加
    - 直近の電圧・電流・SOCをステータスバーに表示

必要なライブラリ:
    pip install pyserial customtkinter

入力フォーマット例(1行1サンプル):
    UP, 27368, V, 3925, I, -90, Cap, 812/1261, SOC, 65, SOH, 94, T, 24. 8
"""

import csv
import json
import os
import re
import sys
import queue
import threading
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox
import serial
import serial.tools.list_ports

BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
DEFAULT_FILENAME_BASE = "mcu_log"
DEFAULT_INTERVAL_MS = 1000
SETTINGS_FILENAME = "UartMonitor_settings.json"

# ===== カラーパレット（計測機器風・水色） =====
COL_BG = "#0d1117"
COL_PANEL = "#161b22"
COL_PANEL_BORDER = "#21262d"
COL_INPUT_BG = "#0d1117"
COL_INPUT_BORDER = "#30363d"
COL_TEXT = "#c9d1d9"
COL_TEXT_DIM = "#6e7681"
COL_ACCENT = "#22c55e"        # 緑アクセント
COL_ACCENT_TEXT = "#052e16"   # アクセント背景の上に乗る文字色
COL_ACCENT_HOVER = "#4ade80"
COL_ACCENT_DISABLED = "#14532d"  # CONNECTボタンが無効化されたときの落ち着いた背景色
COL_OK = "#3fb950"            # 接続中インジケータ(緑)
COL_LOG_BG = "#010409"
COL_LOG_RECENT = "#22c55e"
COL_LOG_OLD = "#6e7681"
COL_TEXT_DISABLED = "#9aa4ad"   # 無効化時でも読みやすいグレー
COL_DANGER = "#f85149"         # 接続中の切断ボタン用の赤
COL_DANGER_HOVER = "#ff7b72"

FONT_MONO = ("Consolas", 12)
FONT_MONO_SMALL = ("Consolas", 10)
FONT_MONO_BOLD = ("Consolas", 14, "bold")


# CSVの先頭に必ず置く列
FIXED_COLUMNS = ["pc_timestamp", "t_ms", "elapsed_ms"]

# 既知のラベル → CSV列名。"812/1261" のように "/" で区切られた値は列を分ける。
# ここにないラベルは、ラベル名をそのまま列名にする("/" 区切りの2つ目以降は "ラベル_2" ...)
KNOWN_COLUMNS = {
    "V": ["voltage_mV"],
    "I": ["current_mA"],
    "Cap": ["cap_mAh", "cap_max_mAh"],
    "SOC": ["soc_percent"],
    "SOH": ["soh_percent"],
    "T": ["temp_C"],
}


def _to_number(text: str):
    """数値にできればint/floatにする。"24. 8" のような空白混入は除去する。数値でなければ文字列のまま。"""
    text = text.replace(" ", "")
    for conv in (int, float):
        try:
            return conv(text)
        except ValueError:
            pass
    return text


def parse_line(line: str):
    """
    'UP, 27368, V, 3925, I, -90, Cap, 812/1261, SOC, 65, SOH, 94, T, 24. 8'
    のような行を {"t_ms": 27368, "voltage_mV": 3925, ...} の辞書で返す(キーの順は受信順)。
    "UP" で始まらない行は None を返す。

    2番目の値が整数なら t_ms とし、以降を「ラベル, 値」の組として読む。位置で決め打ちしないので、
    項目の追加・削除・並べ替えがあっても読める。
    """
    tokens = [t.strip() for t in line.split(",")]
    if tokens[0] != "UP":
        return None

    data = {"t_ms": ""}
    rest = tokens[1:]
    if rest and isinstance(_to_number(rest[0]), int):
        data["t_ms"] = _to_number(rest[0])
        rest = rest[1:]

    for label, value in zip(rest[0::2], rest[1::2]):
        if not label:
            continue
        names = KNOWN_COLUMNS.get(label, [])
        for i, part in enumerate(value.split("/")):
            if i < len(names):
                column = names[i]
            else:
                column = label if i == 0 else f"{label}_{i + 1}"
            data[column] = _to_number(part)
    return data


class CsvRowWriter:
    """
    最初に書く行の項目から列(ヘッダー)を決めてCSVに書き込む。
    列は FIXED_COLUMNS の後に受信順で並ぶ。以降の行で欠けている項目は空欄になる。
    接続中に新しく現れた項目は列を増やせないため、write() の戻り値で知らせる(初回のみ)。
    """

    def __init__(self, file):
        self.file = file
        self.writer = None
        self.reported = set()

    def write(self, row: dict) -> list:
        if self.writer is None:
            columns = FIXED_COLUMNS + [k for k in row if k not in FIXED_COLUMNS]
            self.writer = csv.DictWriter(self.file, fieldnames=columns, restval="", extrasaction="ignore")
            self.writer.writeheader()
        self.writer.writerow(row)
        self.file.flush()

        new_columns = [k for k in row if k not in self.writer.fieldnames and k not in self.reported]
        self.reported.update(new_columns)
        return new_columns


def app_dir() -> str:
    """
    CSVの保存先フォルダを返す。
    exe化(PyInstaller)時はexeと同じフォルダ、スクリプト実行時はこの.pyと同じフォルダ。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def settings_path() -> str:
    """設定ファイルのパス(exe/スクリプトと同じフォルダ)。"""
    return os.path.join(app_dir(), SETTINGS_FILENAME)


def load_settings(path: str) -> dict:
    """設定ファイルを読む。無い・壊れている場合は空の辞書を返す。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(path: str, settings: dict) -> bool:
    """設定ファイルを書く。書けなければ False を返す。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        return False
    return True


def resolve_output_dir(saved, default: str) -> str:
    """保存されていた出力先フォルダが今も存在すればそれを、なければ既定のフォルダを返す。"""
    if isinstance(saved, str) and saved and os.path.isdir(saved):
        return saved
    return default


def shorten_path(path: str, max_chars: int) -> str:
    """長いパスは先頭を省略して末尾(フォルダ名側)を残す。"""
    if len(path) <= max_chars:
        return path
    return "…" + path[-(max_chars - 1):]


def resource_path(relative: str) -> str:
    """
    同梱リソース(アイコン等)のパスを返す。
    exe化(PyInstaller)時は展開先(_internal)、スクリプト実行時はこの.pyと同じフォルダを基準にする。
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def get_next_log_filename(base_name: str, directory: str = ".") -> str:
    """
    directory内の {base_name}1.csv, {base_name}2.csv, ... を調べて、
    次に使うべき {base_name}{n}.csv のファイル名を返す。
    (例: base_name="log" で log1.csv, log2.csv が存在するなら "log3.csv" を返す)
    """
    base_name = base_name.strip() or DEFAULT_FILENAME_BASE
    pattern = re.compile(r"^" + re.escape(base_name) + r"(\d+)\.csv$")

    max_n = 0
    for name in os.listdir(directory):
        m = pattern.match(name)
        if m:
            n = int(m.group(1))
            max_n = max(max_n, n)
    return f"{base_name}{max_n + 1}.csv"


class UartLoggerApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("UartMonitor")
        self._set_window_icon()
        self.root.geometry("760x560")
        self.root.configure(fg_color=COL_BG)

        self.serial_conn = None
        self.read_thread = None
        self.stop_event = threading.Event()
        self.line_queue = queue.Queue()
        self.csv_file = None
        self.csv_writer = None
        self.elapsed_counter = 0
        self.line_increment_ms = DEFAULT_INTERVAL_MS

        # CSVの保存先フォルダ(前回選んだフォルダがあればそれを使う)
        self.settings = load_settings(settings_path())
        saved_dir = self.settings.get("output_dir")
        self.output_dir = resolve_output_dir(saved_dir, app_dir())

        self._build_widgets()
        self._refresh_ports()
        if saved_dir and self.output_dir != saved_dir:
            self._append_log(f"※ 前回の保存先 {saved_dir} が見つからないため、{self.output_dir} に保存します")

        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_window_icon(self):
        # タイトルバー・タスクバーのアイコン(.icoはWindowsのみ対応。失敗しても起動は続ける)
        self.icon_path = resource_path(os.path.join("assets", "icon.ico"))
        try:
            self.root.iconbitmap(self.icon_path)
        except Exception:
            pass

        # Tkは表示倍率に関係なく16px/32pxのアイコンしか渡さないため、125%・150%表示などで
        # タイトルバー・タスクバーのアイコンが引き伸ばされてぼやける。倍率に合ったサイズを
        # .icoから読み直し、Win32 APIで直接設定する。
        # ウィンドウ枠はCTkの初期化中(update)に作成済みなので、ここで設定できる。
        # 表示のタイミングに依存しないようmainloop開始後にも再設定し、
        # 倍率の違うモニターへ移動したときは<Configure>で設定し直す
        if sys.platform == "win32":
            self._win_icons = {}
            self._win_icon_dpi = None
            self._apply_win_icons()
            self.root.after(300, self._apply_win_icons)
            self.root.bind("<Configure>", self._apply_win_icons, add="+")

    def _apply_win_icons(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.LoadImageW.argtypes = [
                wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            ]
            user32.SendMessageW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            ]
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1

            hwnd = int(self.root.wm_frame(), 16)
            try:
                user32.GetDpiForWindow.argtypes = [wintypes.HWND]
                dpi = user32.GetDpiForWindow(hwnd) or 96
            except AttributeError:  # Windows 10 1607より前
                dpi = 96
            if event is not None and dpi == self._win_icon_dpi:
                return  # 倍率が変わっていなければ何もしない
            self._win_icon_dpi = dpi

            for kind, base in ((ICON_SMALL, 16), (ICON_BIG, 32)):
                size = round(base * dpi / 96)
                if size not in self._win_icons:
                    self._win_icons[size] = user32.LoadImageW(
                        None, self.icon_path, IMAGE_ICON, size, size, LR_LOADFROMFILE
                    )
                if self._win_icons[size]:
                    user32.SendMessageW(hwnd, WM_SETICON, kind, self._win_icons[size])
        except Exception:
            pass

    # ---------- UI構築 ----------

    def _build_widgets(self):
        # ===== ヘッダー =====
        header = ctk.CTkFrame(self.root, fg_color=COL_PANEL, corner_radius=0)
        header.pack(fill="x")

        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="x", padx=20, pady=(14, 14))

        dot = ctk.CTkLabel(header_inner, text="■", text_color=COL_ACCENT, font=("Consolas", 12))
        dot.pack(side="left")

        title = ctk.CTkLabel(
            header_inner, text="UartMonitor", text_color=COL_TEXT,
            font=FONT_MONO_BOLD
        )
        title.pack(side="left", padx=(8, 0))

        subtitle = ctk.CTkLabel(
            header_inner, text="STM32L552VET6", text_color=COL_TEXT_DIM,
            font=FONT_MONO_SMALL
        )
        subtitle.pack(side="right")

        # アクセントカラーの下線
        underline = ctk.CTkFrame(self.root, fg_color=COL_ACCENT, height=2, corner_radius=0)
        underline.pack(fill="x")

        # ===== 接続設定パネル =====
        control_panel = ctk.CTkFrame(self.root, fg_color=COL_PANEL, corner_radius=0)
        control_panel.pack(fill="x")

        control_inner = ctk.CTkFrame(control_panel, fg_color="transparent")
        control_inner.pack(fill="x", padx=20, pady=(16, 10))

        # PORT
        port_col = ctk.CTkFrame(control_inner, fg_color="transparent")
        port_col.pack(side="left", padx=(0, 10))

        port_label_row = ctk.CTkFrame(port_col, fg_color="transparent")
        port_label_row.pack(anchor="w", fill="x")
        ctk.CTkLabel(
            port_label_row, text="PORT", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL
        ).pack(side="left")
        self.refresh_btn = ctk.CTkButton(
            port_label_row, text="⟳", width=20, height=16,
            fg_color="transparent", hover_color=COL_INPUT_BORDER,
            text_color=COL_TEXT_DIM, text_color_disabled=COL_TEXT_DISABLED,
            font=("Consolas", 12), corner_radius=4,
            command=self._refresh_ports,
        )
        self.refresh_btn.pack(side="left", padx=(4, 0))

        self.port_menu = ctk.CTkOptionMenu(
            port_col, values=[""], width=170,
            fg_color=COL_INPUT_BG, button_color=COL_PANEL_BORDER,
            button_hover_color=COL_INPUT_BORDER, text_color=COL_TEXT,
            text_color_disabled=COL_TEXT_DISABLED,
            dropdown_fg_color=COL_PANEL, dropdown_text_color=COL_TEXT,
            dropdown_hover_color=COL_PANEL_BORDER,
            font=FONT_MONO_SMALL, corner_radius=4,
        )
        self.port_menu.pack(anchor="w", pady=(4, 0))

        # BAUD
        baud_col = ctk.CTkFrame(control_inner, fg_color="transparent")
        baud_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            baud_col, text="BAUDRATE(bps)", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL
        ).pack(anchor="w")
        self.baud_menu = ctk.CTkOptionMenu(
            baud_col, values=BAUD_RATES, width=120,
            fg_color=COL_INPUT_BG, button_color=COL_PANEL_BORDER,
            button_hover_color=COL_INPUT_BORDER, text_color=COL_TEXT,
            text_color_disabled=COL_TEXT_DISABLED,
            dropdown_fg_color=COL_PANEL, dropdown_text_color=COL_TEXT,
            dropdown_hover_color=COL_PANEL_BORDER,
            font=FONT_MONO_SMALL, corner_radius=4,
        )
        self.baud_menu.set("115200")
        self.baud_menu.pack(anchor="w", pady=(4, 0))

        # INTERVAL（timeに加算する数値）
        interval_col = ctk.CTkFrame(control_inner, fg_color="transparent")
        interval_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            interval_col, text="INTERVAL(ms)", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL
        ).pack(anchor="w")
        self.interval_entry = ctk.CTkEntry(
            interval_col, width=90,
            fg_color=COL_INPUT_BG, border_color=COL_INPUT_BORDER,
            text_color=COL_TEXT,
            font=FONT_MONO_SMALL, corner_radius=4,
        )
        self.interval_entry.insert(0, str(DEFAULT_INTERVAL_MS))
        self.interval_entry.pack(anchor="w", pady=(4, 0))

        # ファイル名（ベース名）
        filename_col = ctk.CTkFrame(control_inner, fg_color="transparent")
        filename_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            filename_col, text="FILENAME", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL
        ).pack(anchor="w")
        self.filename_entry = ctk.CTkEntry(
            filename_col, width=120,
            fg_color=COL_INPUT_BG, border_color=COL_INPUT_BORDER,
            text_color=COL_TEXT,
            font=FONT_MONO_SMALL, corner_radius=4,
        )
        self.filename_entry.insert(0, DEFAULT_FILENAME_BASE)
        self.filename_entry.pack(anchor="w", pady=(4, 0))

        # 接続/切断（1つのトグルボタン）
        btn_col = ctk.CTkFrame(control_inner, fg_color="transparent")
        btn_col.pack(side="right")
        ctk.CTkLabel(btn_col, text="", font=FONT_MONO_SMALL).pack(anchor="w")

        self.toggle_btn = ctk.CTkButton(
            btn_col, text="接続", width=140, height=30,
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
            text_color=COL_ACCENT_TEXT, text_color_disabled=COL_ACCENT_TEXT,
            font=("Consolas", 12, "bold"),
            corner_radius=4, command=self._on_toggle_connection,
        )
        self.toggle_btn.pack(anchor="w", pady=(4, 0))

        # 出力先フォルダ（接続設定パネルの2段目）
        output_row = ctk.CTkFrame(control_panel, fg_color="transparent")
        output_row.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(
            output_row, text="OUTPUT", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL
        ).pack(side="left")

        secondary_btn = dict(
            height=24, fg_color=COL_PANEL_BORDER, hover_color=COL_INPUT_BORDER,
            text_color=COL_TEXT, text_color_disabled=COL_TEXT_DISABLED,
            font=FONT_MONO_SMALL, corner_radius=4,
        )
        # 右側のボタンを先に配置し、残りの幅をパス表示に使う
        self.output_open_btn = ctk.CTkButton(
            output_row, text="開く", width=56, command=self._on_open_output, **secondary_btn
        )
        self.output_open_btn.pack(side="right")
        self.output_browse_btn = ctk.CTkButton(
            output_row, text="参照...", width=70, command=self._on_browse_output, **secondary_btn
        )
        self.output_browse_btn.pack(side="right", padx=(0, 6))

        self.output_label = ctk.CTkLabel(
            output_row, text="", height=24, anchor="w",
            fg_color=COL_INPUT_BG, text_color=COL_TEXT, font=FONT_MONO_SMALL, corner_radius=4,
        )
        self.output_label.pack(side="left", fill="x", expand=True, padx=(10, 10))
        self._update_output_label()

        # ===== ステータスバー =====
        status_bar = ctk.CTkFrame(self.root, fg_color=COL_BG, corner_radius=0)
        status_bar.pack(fill="x")

        status_inner = ctk.CTkFrame(status_bar, fg_color="transparent")
        status_inner.pack(fill="x", padx=20, pady=(10, 4))

        self.status_dot = ctk.CTkLabel(status_inner, text="●", text_color=COL_TEXT_DIM, font=("Consolas", 10))
        self.status_dot.pack(side="left")

        self.status_label = ctk.CTkLabel(
            status_inner, text="NOT CONNECTED", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL
        )
        self.status_label.pack(side="left", padx=(6, 20))

        self.v_label = ctk.CTkLabel(status_inner, text="V: --", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL)
        self.v_label.pack(side="left", padx=(0, 16))

        self.i_label = ctk.CTkLabel(status_inner, text="I: --", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL)
        self.i_label.pack(side="left", padx=(0, 16))

        self.soc_label = ctk.CTkLabel(status_inner, text="SOC: --", text_color=COL_TEXT_DIM, font=FONT_MONO_SMALL)
        self.soc_label.pack(side="left")

        # ===== ログ表示 =====
        log_frame = ctk.CTkFrame(
            self.root, fg_color=COL_LOG_BG, corner_radius=6,
            border_width=1, border_color=COL_PANEL_BORDER,
        )
        log_frame.pack(fill="both", expand=True, padx=20, pady=(6, 20))

        self.log_text = ctk.CTkTextbox(
            log_frame, fg_color=COL_LOG_BG, text_color=COL_LOG_OLD,
            font=FONT_MONO, corner_radius=6, wrap="none",
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.log_text.configure(state="disabled")

        # 直近の行を明るい水色でハイライトするためのタグ
        self.log_text.tag_config("recent", foreground=COL_LOG_RECENT)
        self.log_text.tag_config("old", foreground=COL_LOG_OLD)

    # ---------- 出力先フォルダ ----------

    def _update_output_label(self):
        self.output_label.configure(text=" " + shorten_path(self.output_dir, 64))

    def _on_browse_output(self):
        chosen = filedialog.askdirectory(
            parent=self.root, initialdir=self.output_dir, title="CSVの保存先フォルダを選択"
        )
        if not chosen:  # キャンセル
            return
        self.output_dir = os.path.normpath(chosen)
        self._update_output_label()

        self.settings["output_dir"] = self.output_dir
        if not save_settings(settings_path(), self.settings):
            self._append_log("※ 設定を保存できませんでした(次回起動時は既定のフォルダに戻ります)")

    def _on_open_output(self):
        try:
            os.startfile(self.output_dir)  # Windows のエクスプローラーで開く
        except (AttributeError, OSError) as e:
            messagebox.showerror("エラー", f"フォルダを開けませんでした。\n\n{self.output_dir}\n\n{e}")

    # ---------- ポート検出 ----------

    def _refresh_ports(self):
        ports = list(serial.tools.list_ports.comports())
        port_names = [p.device for p in ports]

        if not port_names:
            self.port_menu.configure(values=[""])
            self.port_menu.set("")
            return

        self.port_menu.configure(values=port_names)
        best_port = self._guess_best_port(ports)
        self.port_menu.set(best_port if best_port else port_names[0])

    @staticmethod
    def _guess_best_port(ports):
        keyword_scores = [
            ("stlink", 100),
            ("st-link", 100),
            ("stmicroelectronics", 90),
            ("stm32", 90),
            ("usb serial", 50),   # "USB Serial Port" "USB Serial Device" 等を広くカバー
            ("usb-serial", 50),
            ("ch340", 40),
            ("cp210", 40),
            ("ftdi", 40),
        ]

        best_device = None
        best_score = -1

        for p in ports:
            text = f"{p.description} {p.hwid}".lower()
            score = 0
            for keyword, weight in keyword_scores:
                if keyword in text:
                    score = max(score, weight)

            if "bluetooth" in text:
                score -= 100
            if "active management technology" in text:
                score -= 100
            if "modem" in text:
                score -= 100

            if score > best_score:
                best_score = score
                best_device = p.device

        if best_score <= 0:
            return None

        return best_device

    # ---------- 接続処理 ----------

    def _on_toggle_connection(self):
        if self.serial_conn is None:
            self._do_connect()
        else:
            self._do_disconnect()

    def _do_connect(self):
        port = self.port_menu.get()
        baud = self.baud_menu.get()

        if not port:
            messagebox.showerror("エラー", "COMポートを選択してください。")
            return

        # ボタンを押した直後に「接続中...」を表示し、処理中であることを示す
        self.toggle_btn.configure(text="接続中...", state="disabled")
        self.root.update_idletasks()

        try:
            self.serial_conn = serial.Serial(port, int(baud), timeout=1)
        except serial.SerialException as e:
            self.toggle_btn.configure(text="接続", state="normal")
            messagebox.showerror("接続エラー", f"{port} を開けませんでした。\n\n{e}")
            return

        save_dir = self.output_dir
        try:
            # 保存先フォルダが削除されていた場合も、ここで OSError になる
            self.current_csv_name = get_next_log_filename(self.filename_entry.get(), save_dir)
            csv_path = os.path.join(save_dir, self.current_csv_name)
            self.csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        except OSError as e:
            self.serial_conn.close()
            self.serial_conn = None
            self.toggle_btn.configure(text="接続", state="normal")
            messagebox.showerror(
                "保存エラー",
                f"CSVファイルを作成できませんでした。\n\n保存先: {save_dir}\n\n{e}\n\n"
                "OUTPUT欄の「参照...」で別のフォルダを選んでください。",
            )
            return
        # ヘッダーは最初に受信した行の項目から決めるため、ここでは書かない
        self.csv_writer = CsvRowWriter(self.csv_file)

        # 経過時間(elapsed_ms)は実時間ではなく、INTERVAL欄で指定した固定値を加算幅として使う
        try:
            self.line_increment_ms = int(self.interval_entry.get())
        except ValueError:
            self.line_increment_ms = DEFAULT_INTERVAL_MS
        self.elapsed_counter = 0

        self.stop_event.clear()
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()

        # ログ表示を接続のたびにリセット
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._append_log(f"保存先: {csv_path}")

        self.status_dot.configure(text_color=COL_OK)
        self.status_label.configure(
            text=f"LINKED · {port} @ {baud} · {self.current_csv_name}", text_color=COL_OK
        )
        self.toggle_btn.configure(
            text="切断", state="normal", fg_color=COL_DANGER, hover_color=COL_DANGER_HOVER
        )
        self.port_menu.configure(state="disabled")
        self.baud_menu.configure(state="disabled")
        self.refresh_btn.configure(state="disabled")
        self.filename_entry.configure(state="disabled")
        self.interval_entry.configure(state="disabled")
        self.output_browse_btn.configure(state="disabled")

    def _read_loop(self):
        while not self.stop_event.is_set():
            try:
                raw = self.serial_conn.readline()
            except serial.SerialException:
                self.line_queue.put(("__error__", "シリアルポートが切断されました。"))
                break

            if not raw:
                continue

            line = raw.decode(errors="ignore").strip()
            if not line:
                continue

            data = parse_line(line)
            if data is None:
                # "UP" で始まらない行(デバッグ出力など)はそのまま表示するだけ
                self.line_queue.put(("line", line, None))
                continue

            # 経過時間(ms): INTERVAL欄で指定した固定値を加算幅として使う(1行目は0)
            elapsed_ms = self.elapsed_counter
            self.elapsed_counter += self.line_increment_ms

            # 表示用: "time, {経過時間}, up, {元のt_ms}, V, ..." の形式に組み替える
            tokens = [t.strip() for t in line.split(",")]
            display_line = ", ".join(["time", str(elapsed_ms), "up"] + tokens[1:])
            self.line_queue.put(("line", display_line, data))

            pc_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            new_columns = self.csv_writer.write({"pc_timestamp": pc_now, "elapsed_ms": elapsed_ms, **data})
            if new_columns:
                self.line_queue.put((
                    "line",
                    f"※ 接続中に追加された項目 {', '.join(new_columns)} はこのCSVに含まれません"
                    "(再接続すると列に追加されます)",
                    None,
                ))

    def _poll_queue(self):
        while not self.line_queue.empty():
            item = self.line_queue.get_nowait()

            if item[0] == "__error__":
                messagebox.showerror("通信エラー", item[1])
                self._do_disconnect()
                break

            _, line, data = item
            self._append_log(line)

            if data is not None:
                if "voltage_mV" in data:
                    self.v_label.configure(text=f"V: {data['voltage_mV']}mV")
                if "current_mA" in data:
                    self.i_label.configure(text=f"I: {data['current_mA']}mA")
                if "soc_percent" in data:
                    self.soc_label.configure(text=f"SOC: {data['soc_percent']}%")

        self.root.after(100, self._poll_queue)

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")

        self.log_text.tag_remove("recent", "1.0", "end")
        self.log_text.insert("end", line + "\n", "recent")

        total_lines = int(self.log_text.index("end-1c").split(".")[0])
        if total_lines > 3:
            self.log_text.tag_add("old", "1.0", f"{total_lines - 2}.0")

        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _do_disconnect(self):
        # ボタンを押した直後に「切断中...」を表示し、処理中であることを示す(赤のまま維持)
        self.toggle_btn.configure(text="切断中...", state="disabled", fg_color=COL_DANGER)
        self.root.update_idletasks()

        self.stop_event.set()
        if self.read_thread is not None:
            self.read_thread.join(timeout=2)

        if self.serial_conn is not None:
            self.serial_conn.close()
            self.serial_conn = None

        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = None

        self.status_dot.configure(text_color=COL_TEXT_DIM)
        self.status_label.configure(text="NOT CONNECTED", text_color=COL_TEXT_DIM)
        self.v_label.configure(text="V: --")
        self.i_label.configure(text="I: --")
        self.soc_label.configure(text="SOC: --")

        self.toggle_btn.configure(
            text="接続", state="normal",
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
        )
        self.port_menu.configure(state="normal")
        self.baud_menu.configure(state="normal")
        self.refresh_btn.configure(state="normal")
        self.filename_entry.configure(state="normal")
        self.interval_entry.configure(state="normal")
        self.output_browse_btn.configure(state="normal")

    def _on_close(self):
        if self.serial_conn is not None:
            self._do_disconnect()
        self.root.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    app = UartLoggerApp(root)
    root.mainloop()
