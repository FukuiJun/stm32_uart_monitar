"""出力先フォルダ設定のテスト。 実行: python -m unittest discover -s tests -v"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from uart_monitar_gui import load_settings, resolve_output_dir, save_settings, shorten_path  # noqa: E402


class SettingsFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "UartMonitor_settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_empty(self):
        self.assertEqual(load_settings(self.path), {})

    def test_round_trip_with_japanese_path(self):
        settings = {"output_dir": r"C:\計測\ログ"}
        self.assertTrue(save_settings(self.path, settings))
        self.assertEqual(load_settings(self.path), settings)

    def test_broken_file_is_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(load_settings(self.path), {})

    def test_non_dict_json_is_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[1, 2]")
        self.assertEqual(load_settings(self.path), {})

    def test_save_to_unwritable_location_returns_false(self):
        self.assertFalse(save_settings(os.path.join(self.tmp.name, "no_such_dir", "s.json"), {}))


class ResolveOutputDirTest(unittest.TestCase):
    def test_existing_saved_dir_is_used(self):
        with tempfile.TemporaryDirectory() as saved:
            self.assertEqual(resolve_output_dir(saved, "DEFAULT"), saved)

    def test_deleted_saved_dir_falls_back(self):
        with tempfile.TemporaryDirectory() as saved:
            pass
        self.assertEqual(resolve_output_dir(saved, "DEFAULT"), "DEFAULT")

    def test_unset_or_invalid_falls_back(self):
        for saved in (None, "", 123):
            self.assertEqual(resolve_output_dir(saved, "DEFAULT"), "DEFAULT")


class ShortenPathTest(unittest.TestCase):
    def test_short_path_unchanged(self):
        self.assertEqual(shorten_path(r"C:\logs", 20), r"C:\logs")

    def test_long_path_keeps_tail(self):
        path = r"C:\Users\someone\Documents\measurements\battery\2026"
        short = shorten_path(path, 20)
        self.assertEqual(len(short), 20)
        self.assertTrue(short.startswith("…"))
        self.assertTrue(path.endswith(short[1:]))


if __name__ == "__main__":
    unittest.main()
