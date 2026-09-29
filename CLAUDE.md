# CLAUDE.md

STM32L552VET6 から UART で送られるバッテリー測定データを PC で表示・CSV 保存するツール **UartMonitor** のリポジトリ。
経緯・詳細仕様は `docs/HANDOFF.md` を参照。

## 構成

- `uart_monitar_gui.py` — UartMonitor 本体（Python / customtkinter / pyserial）。ファイル名のスペル `monitar` は意図的なので修正しないこと
- `docs/HANDOFF.md` — 引き継ぎ資料（背景・通信仕様・設計判断の経緯）
- `.github/workflows/build-exe.yml` — Windows ランナーで exe をビルドし zip 化する GitHub Actions
- `packaging/使い方.txt` — 配布 zip に同梱するエンドユーザー向け説明
- `assets/icon.ico` — アプリアイコン（exe・ウィンドウ用、16〜256px マルチサイズ）。`assets/icon.svg` がマスター
  - 各サイズは **BMP 形式で格納すること**（PNG 形式だと Tk がサイズを読めず 16px を引き伸ばして使うため、タスクバーでぼやける）
  - Windows ではさらに `_apply_win_icons()` が表示倍率に合ったサイズを Win32 API で設定する
- `tests/` — ユニットテスト（`python -m unittest discover -s tests -v`、CI でも実行）。`test_parsing.py`: 受信行のパースと CSV 列、`test_settings.py`: 出力先フォルダ設定
- `.github/scripts/smoke_test_exe.py` — CI でビルドした exe を起動し、ウィンドウのアイコンが icon.ico と一致するか検査
  - CI は 100% 表示のため、icon.ico を PNG 形式に差し替えた 2 回目の起動で `_apply_win_icons()` が効いていることも確認する（150% 等の実機確認は CI ではできない）

## 受信フォーマット（マイコン → PC）

1 行 1 サンプルのカンマ区切りテキスト:

```
UP, 27368, V, 3925, I, -90, Cap, 812/1261, SOC, 65, SOH, 94, T, 24.8
```

- `UP` は行頭マーカー、2 番目は MCU のミリ秒カウンタ `t_ms`（整数でなければ t_ms なしとして扱う）
- 以降は「ラベル, 値」の組として `parse_line()` が読む。**位置で決め打ちしない**ので、マイコン側で項目を追加・削除・並べ替えしても time 付与と CSV 保存は止まらない
  - 既知ラベルは `KNOWN_COLUMNS` で列名に変換（例: `V`→`voltage_mV`、`Cap` は `/` で `cap_mAh`,`cap_max_mAh` に分割）。未知ラベルはラベル名がそのまま列名（`/` 区切りの 2 つ目以降は `ラベル_2`…）
  - `UP` で始まらない行はログ表示のみ（time・CSV なし）
- `V`=mV, `I`=mA, `Cap`=現在/最大 mAh, `SOC`/`SOH`=%, `T`=℃
- `T` の値は `24. 8` のように途中に空白が入ることがあるため、パース時に空白除去が必要

## CSV 出力フォーマット

```
pc_timestamp, t_ms, elapsed_ms, voltage_mV, current_mA, cap_mAh, cap_max_mAh, soc_percent, soh_percent, temp_C
```

- 先頭 3 列（`FIXED_COLUMNS`）は固定。以降の列は接続後に**最初に受信した `UP` 行の項目**で決まる（`CsvRowWriter`）。上記は現行フォーマットでの列
  - 以降の行で欠けた項目は空欄。接続中に新しく現れた項目は列を増やせないため CSV に入らず、ログに一度だけ通知（再接続で列に入る）

- `elapsed_ms` は実時間ではなく、1 行受信ごとに GUI の INTERVAL(ms) 値（初期 1000）を加算した値
- ファイル名は FILENAME（初期 `mcu_log`）+ 接続ごとの連番（`mcu_log1.csv`, `mcu_log2.csv`, ...）
- 保存先は GUI の OUTPUT 欄で選択（`self.output_dir`）。初期値は `app_dir()`: exe 実行時は exe と同じフォルダ、スクリプト実行時は .py と同じフォルダ（カレントディレクトリには依存しない）
  - 選んだフォルダは `app_dir()` の `UartMonitor_settings.json`（`{"output_dir": ...}`）に保存し次回起動時に使う。フォルダが無くなっていたら既定に戻してログで通知（`resolve_output_dir()`）
  - 接続時に保存先が無い・書けない場合はエラーダイアログを出して接続を中止する

## exe ビルド

- PyInstaller のフォルダ形式（onedir）。Linux ではビルドできないため GitHub Actions（`windows-latest`）で実行
- コマンド: `pyinstaller --noconfirm --windowed --onedir --name UartMonitor --icon assets/icon.ico --add-data "assets/icon.ico;assets" --collect-data customtkinter uart_monitar_gui.py`
  - `--icon` は exe ファイル自体のアイコン、`--add-data` はウィンドウ(タイトルバー・タスクバー)用に `_internal/assets/icon.ico` として同梱
  - `--collect-data customtkinter` がないとテーマ JSON が同梱されず起動時に落ちる
- `main` / `claude/**` への push と手動実行で Artifact `UartMonitor.zip`、リリース方法は下記

## バージョン管理・リリース

- ユーザーから指定がない限り、バージョン番号とリリースのタイミングは Claude が判断してよい（ユーザー了承済み）
- リリースは Actions の「Build exe」を手動実行し、入力 `release_tag` に `vMAJOR.MINOR.PATCH` を指定する（MCP `actions_run_trigger`、`ref` は作業ブランチ）
  - ビルド・スモークテスト後、ビルドしたコミットにタグを作成し Release に `UartMonitor.zip` を添付する
  - Claude のセッションからはタグの push が git プロキシで拒否される（HTTP 403）ため、この方法を使う。ユーザーが手元からタグを push しても同様にリリースされる
  - PATCH: 不具合修正・アイコン等の軽微な変更 / MINOR: 機能追加 / MAJOR: CSV フォーマット等の互換性が変わる変更
- exe の中身が変わらない変更（ドキュメントのみ等）ではタグを打たない
- リリース本文の編集・削除は MCP ツールがないためユーザーが GitHub 画面で行う

## UI の約束事

- ダーク基調の計測機器風デザイン: 背景 `#0d1117`、パネル `#161b22`
- アクセントは緑 `#22c55e` 系で統一。赤 `#f85149` は接続中の「切断」ボタンのみ
- フォントは Consolas（等幅）で統一
- シリアル受信はバックグラウンドスレッド + キュー。GUI スレッドをブロックしないこと

## 今後の予定

- GL240（GRAPHTEC）CSV と UartMonitor CSV を 1 つの Excel にまとめる統合ツール（GL240 のフォーマットは未入手）
- MCU 出力フォーマットの信頼性向上（シーケンス番号・チェックサム）は提案済み・未実装
