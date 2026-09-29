"""受信行のパースとCSV出力のテスト。 実行: python -m unittest discover -s tests -v"""

import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from uart_monitar_gui import CsvRowWriter, parse_line  # noqa: E402

LEGACY_LINE = "UP, 27368, V, 3925, I, -90, Cap, 812/1261, SOC, 65, SOH, 94, T, 24. 8"
LEGACY_HEADER = [
    "pc_timestamp", "t_ms", "elapsed_ms", "voltage_mV", "current_mA",
    "cap_mAh", "cap_max_mAh", "soc_percent", "soh_percent", "temp_C",
]


def write_rows(rows):
    """CsvRowWriter で書いた CSV を (行のリスト, 知らされた新項目のリスト) で返す。"""
    buf = io.StringIO()
    writer = CsvRowWriter(buf)
    reported = [writer.write(row) for row in rows]
    return list(csv.reader(io.StringIO(buf.getvalue()))), reported


def row_for(line, elapsed_ms):
    return {"pc_timestamp": "2026-01-01 00:00:00.000000", "elapsed_ms": elapsed_ms, **parse_line(line)}


class ParseLineTest(unittest.TestCase):
    def test_legacy_format(self):
        self.assertEqual(parse_line(LEGACY_LINE), {
            "t_ms": 27368, "voltage_mV": 3925, "current_mA": -90, "cap_mAh": 812,
            "cap_max_mAh": 1261, "soc_percent": 65, "soh_percent": 94, "temp_C": 24.8,
        })

    def test_inserted_item_keeps_known_items(self):
        data = parse_line("UP, 100, V, 3925, Vcell, 3.91, I, -90, Cap, 812/1261, SOC, 65, SOH, 94, T, 24.8")
        self.assertEqual(data["t_ms"], 100)
        self.assertEqual(data["Vcell"], 3.91)
        self.assertEqual(data["current_mA"], -90)
        self.assertEqual(data["temp_C"], 24.8)

    def test_removed_and_reordered_items(self):
        data = parse_line("UP, 5, T, 25.0, SOC, 70, V, 4000")
        self.assertEqual(list(data), ["t_ms", "temp_C", "soc_percent", "voltage_mV"])

    def test_unknown_slash_value_is_split(self):
        data = parse_line("UP, 5, Cell, 3900/3950/3920")
        self.assertEqual((data["Cell"], data["Cell_2"], data["Cell_3"]), (3900, 3950, 3920))

    def test_non_numeric_value_kept_as_text(self):
        self.assertEqual(parse_line("UP, 5, MODE, CHG")["MODE"], "CHG")

    def test_missing_t_ms(self):
        data = parse_line("UP, V, 3925, I, -90")
        self.assertEqual(data, {"t_ms": "", "voltage_mV": 3925, "current_mA": -90})

    def test_non_up_line(self):
        self.assertIsNone(parse_line("boot ok"))
        self.assertIsNone(parse_line("ADC, 1, 2, 3"))


class CsvRowWriterTest(unittest.TestCase):
    def test_legacy_header_unchanged(self):
        rows, _ = write_rows([row_for(LEGACY_LINE, 0)])
        self.assertEqual(rows[0], LEGACY_HEADER)
        self.assertEqual(rows[1][1:], ["27368", "0", "3925", "-90", "812", "1261", "65", "94", "24.8"])

    def test_columns_follow_first_line(self):
        line = "UP, 100, V, 3925, Vcell, 3.91, I, -90"
        rows, _ = write_rows([row_for(line, 0), row_for(line, 1000)])
        self.assertEqual(rows[0], ["pc_timestamp", "t_ms", "elapsed_ms", "voltage_mV", "Vcell", "current_mA"])
        self.assertEqual([r[2] for r in rows[1:]], ["0", "1000"])

    def test_item_added_while_connected_is_reported_once(self):
        first = "UP, 1, V, 3925"
        later = "UP, 2, V, 3926, NEW, 7"
        rows, reported = write_rows([row_for(first, 0), row_for(later, 1000), row_for(later, 2000)])
        self.assertEqual(rows[0], ["pc_timestamp", "t_ms", "elapsed_ms", "voltage_mV"])
        self.assertEqual(len(rows), 4)  # 行は失われない
        self.assertEqual(reported, [[], ["NEW"], []])

    def test_missing_item_left_blank(self):
        rows, _ = write_rows([row_for("UP, 1, V, 3925, I, -90", 0), row_for("UP, 2, V, 3926", 1000)])
        self.assertEqual(rows[2], ["2026-01-01 00:00:00.000000", "2", "1000", "3926", ""])


if __name__ == "__main__":
    unittest.main()
