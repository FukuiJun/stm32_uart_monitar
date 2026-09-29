# UartMonitor

STM32L552VET6 から UART 経由で送信されるバッテリー測定データ（電圧・電流・容量・SOC/SOH・温度）を、PC 上でリアルタイム表示し CSV に保存する GUI ツールです。

## 使い方（Python 不要・exe 版）

1. GitHub の **Actions** タブ → 「Build exe」の最新の実行 → **Artifacts** の `UartMonitor` をダウンロード（zip でダウンロードされる）
   （`v1.0` のようなタグを push した場合は **Releases** からも `UartMonitor.zip` を入手可能）
2. zip を解凍し、`UartMonitor` フォルダ内の `UartMonitor.exe` を起動
3. CSV は OUTPUT 欄のフォルダに保存される（初期設定は exe と同じフォルダ。「参照...」で変更でき、次回起動時も引き継がれる）

詳細は zip に同梱の `使い方.txt` を参照。

## 開発者向け（Python から実行）

```
pip install -r requirements.txt
python uart_monitar_gui.py
```

CSV の保存先の初期値は `uart_monitar_gui.py` と同じフォルダ。

## ドキュメント

- [引き継ぎ資料](docs/HANDOFF.md) — 背景、通信仕様、CSV フォーマット、設計判断の経緯
