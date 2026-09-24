# UartMonitor

STM32L552VET6 から UART 経由で送信されるバッテリー測定データ（電圧・電流・容量・SOC/SOH・温度）を、PC 上でリアルタイム表示し CSV に保存する GUI ツールです。

## セットアップ

```
pip install -r requirements.txt
python uart_monitar_gui.py
```

## ドキュメント

- [引き継ぎ資料](docs/HANDOFF.md) — 背景、通信仕様、CSV フォーマット、設計判断の経緯
