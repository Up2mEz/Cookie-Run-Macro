import json
import tempfile
import unittest
from pathlib import Path

from pause_profiles import DEFAULT_PAUSE_PROFILE, PauseProfileError, PauseProfileStore


class PauseProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = PauseProfileStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_profile_is_always_available(self):
        profile = self.store.get(DEFAULT_PAUSE_PROFILE)
        self.assertEqual(profile["roi"]["width"], 62)
        self.assertEqual(profile["roi"]["height"], 62)

    def test_stage_profiles_round_trip_and_replace_same_stage(self):
        profile = {
            "name": "Episode 2",
            "template_path": "templates/episode_2_pause.png",
            "roi": {"x": 1100, "y": 10, "width": 62, "height": 62},
            "resolution": {"width": 1280, "height": 720},
        }
        self.store.upsert(profile)
        profile["roi"]["x"] = 1110
        self.store.upsert(profile)
        self.assertEqual(self.store.get("Episode 2")["roi"]["x"], 1110)
        self.assertEqual(len(json.loads(self.store.path.read_text(encoding="utf-8"))["profiles"]), 1)

    def test_profile_rejects_non_canonical_crop_size(self):
        with self.assertRaisesRegex(PauseProfileError, "62×62"):
            self.store.upsert({
                "name": "bad", "template_path": "templates/bad.png",
                "roi": {"x": 0, "y": 0, "width": 59, "height": 58},
                "resolution": {"width": 1280, "height": 720},
            })

    def test_profile_rejects_path_escape(self):
        with self.assertRaises(PauseProfileError):
            self.store.upsert({
                "name": "bad", "template_path": "../bad.png",
                "roi": {"x": 0, "y": 0, "width": 62, "height": 62},
            })


if __name__ == "__main__":
    unittest.main()
