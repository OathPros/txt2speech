import tempfile
import unittest
from pathlib import Path

from app_settings import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            expected = AppSettings(Path("E:/Qwen Model Files"), "GPU")
            store.save(expected)
            self.assertEqual(store.load(Path("C:/default")), expected)

    def test_invalid_settings_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"gpu_mode":"invalid"}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "GPU mode"):
                SettingsStore(path).load(Path("C:/default"))


if __name__ == "__main__":
    unittest.main()
