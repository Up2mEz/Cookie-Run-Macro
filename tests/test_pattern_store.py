import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from event_model import editable_pattern_errors
from pattern_store import PatternStore, PatternStoreError


class PatternStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = PatternStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_load_v1_migrates_and_creates_backup(self):
        path = self.store.patterns_dir / "old.json"
        path.write_text(json.dumps({"name": "old", "events": [{"at": 1, "action": "jump", "chance": 50}]}), encoding="utf-8")
        pattern, warnings = self.store.load("old")
        self.assertEqual(pattern["schema_version"], 2)
        self.assertTrue(path.with_suffix(".json.bak").exists())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)
        self.assertTrue(warnings)

    def test_atomic_save_leaves_no_tmp(self):
        path, _ = self.store.save({"name": "new", "events": []})
        self.assertTrue(path.exists())
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_relative_template_path_is_allowed(self):
        pattern = {"name": "relative", "events": [], "sync": {"template_path": "templates/pause.png"}}
        self.assertTrue(self.store.save(pattern)[0].exists())

    def test_absolute_template_path_is_rejected(self):
        pattern = {"name": "absolute", "events": [], "sync": {"template_path": str((self.root / "pause.png").resolve())}}
        with self.assertRaises(PatternStoreError):
            self.store.save(pattern)

    def test_corrupted_json_has_clear_error(self):
        (self.store.patterns_dir / "bad.json").write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(PatternStoreError, "JSON เสียหาย"):
            self.store.load("bad")

    def test_invalid_event_opens_in_repair_mode_and_progress_can_be_saved(self):
        path = self.store.patterns_dir / "repair.json"
        path.write_text(json.dumps({
            "name": "repair",
            "safe_zones": [{"id": "z", "start": 1, "end": 2, "label": "safe"}],
            "events": [
                {"id": "bad", "at": 1.8, "phase": "synced", "event_class": "safe_random",
                 "type": "choice", "options": {"none": 40, "jump": 40, "slide": 20},
                 "duration_ms": 400, "jitter_ms": 0, "safe_zone_id": "z"},
                {"id": "ok", "at": 1.2, "phase": "synced", "event_class": "safe_random",
                 "type": "choice", "options": {"none": 100, "jump": 0, "slide": 0},
                 "jitter_ms": 0, "safe_zone_id": "z"},
            ],
        }), encoding="utf-8")

        with self.assertRaisesRegex(PatternStoreError, "สิ้นสุดนอก Safe Zone"):
            self.store.load("repair")

        editable, warnings = self.store.load_for_editing("repair")
        self.assertEqual(len(editable_pattern_errors(editable)), 1)
        self.assertEqual(editable["events"][0]["id"], "bad")
        self.assertTrue(any("โหมดซ่อม" in warning for warning in warnings))

        saved_path, save_warnings = self.store.save_for_editing(editable)
        saved_raw = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertNotIn("_validation_error", saved_raw["events"][0])
        self.assertTrue(saved_path.with_suffix(".json.repair.bak").exists())
        self.assertTrue(any("ยังเล่นไม่ได้" in warning for warning in save_warnings))

        editable["safe_zones"][0]["end"] = 2.3
        repaired_path, _ = self.store.save(editable)
        repaired, _ = self.store.load(repaired_path)
        self.assertEqual(editable_pattern_errors(repaired), [])

    def test_export_import_zip_includes_both_templates_and_renames_collision(self):
        pause_template = self.store.templates_dir / "pause.png"
        result_template = self.store.templates_dir / "result_xp.png"
        Image.new("RGB", (20, 20), "white").save(pause_template)
        Image.new("RGB", (18, 12), "yellow").save(result_template)
        self.store.save({
            "name": "bundle",
            "events": [],
            "sync": {"mode": "auto_pause_icon", "template_path": "templates/pause.png", "roi": {"x": 0, "y": 0, "width": 20, "height": 20}},
            "post_game": {
                "enabled": True,
                "template_path": "templates/result_xp.png",
                "roi": {"x": 0, "y": 0, "width": 18, "height": 12},
            },
        })
        export_path = self.root / "bundle.zip"
        self.store.export_pattern("bundle", export_path)
        imported_path, warnings = self.store.import_pattern(export_path)
        self.assertEqual(imported_path.stem, "bundle_2")
        imported, _ = self.store.load("bundle_2")
        self.assertTrue((self.root / imported["sync"]["template_path"]).is_file())
        self.assertTrue((self.root / imported["post_game"]["template_path"]).is_file())
        self.assertNotEqual(imported["sync"]["template_path"], imported["post_game"]["template_path"])
        self.assertTrue(any("ชื่อซ้ำ" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
