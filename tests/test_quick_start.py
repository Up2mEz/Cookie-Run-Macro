import unittest
from pathlib import Path

from PIL import Image

from builtin_assets import ensure_builtin_assets
from ui.main_window import MainWindow, saved_sync_values


PROJECT_DIR = Path(__file__).resolve().parents[1]


class QuickStartDefaultsTests(unittest.TestCase):
    def test_saved_threshold_and_poll_are_not_silently_reset_to_preset(self):
        values = saved_sync_values({
            "mode": "auto_pause_icon", "threshold": 0.67, "poll_ms": 80,
            "consecutive_matches": 4, "timeout_seconds": 30, "offset_ms": 450,
        })
        self.assertEqual(values["threshold"], "0.67")
        self.assertEqual(values["poll_ms"], "80")
        self.assertEqual(values["consecutive_matches"], "4")
        self.assertEqual(values["offset_ms"], "450")

    def test_defaults_match_supplied_1280x720_screenshot(self):
        payload = MainWindow._default_pattern_payload("stage_01")
        self.assertEqual(payload["device"]["resolution"], {"width": 1280, "height": 720})
        self.assertEqual(payload["controls"]["jump"], {"x": 160, "y": 635})
        self.assertEqual(payload["controls"]["slide"], {"x": 1115, "y": 635})
        self.assertEqual(payload["sync"]["roi"], {"x": 1165, "y": 5, "width": 62, "height": 62})
        self.assertEqual(payload["sync"]["poll_ms"], 20)
        self.assertEqual(payload["sync"]["consecutive_matches"], 2)
        self.assertEqual(payload["sync"]["offset_ms"], 300)
        self.assertEqual(payload["playback"]["pre_sync_extra_ms"], 0)
        self.assertEqual(payload["post_game"]["roi"], {"x": 155, "y": 450, "width": 130, "height": 75})

    def test_bundled_pause_template_exists(self):
        path = PROJECT_DIR / "templates" / "default_pause_1280x720.png"
        with Image.open(path) as image:
            self.assertEqual(image.size, (62, 62))

    def test_bundled_result_xp_template_exists(self):
        path = ensure_builtin_assets(PROJECT_DIR)
        with Image.open(path) as image:
            self.assertEqual(image.size, (32, 32))

    def test_batch_launchers_are_ascii_only(self):
        for filename in ("START_HERE.bat", "run_app.bat", "install_dependencies.bat"):
            with self.subTest(filename=filename):
                self.assertTrue(all(byte < 128 for byte in (PROJECT_DIR / filename).read_bytes()))


if __name__ == "__main__":
    unittest.main()
