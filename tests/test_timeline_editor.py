import unittest

from ui.timeline_editor import (
    TimelineEditor,
    events_in_range,
    pattern_duration,
    phase_duration,
    playhead_scroll_fraction,
    range_summary,
)


class TimelineHelpersTests(unittest.TestCase):
    def setUp(self):
        self.pattern = {
            "events": [
                {"id": "pre", "phase": "pre_sync", "at": 1.0, "action": "tap"},
                {"id": "a", "phase": "synced", "at": 1.5, "action": "jump"},
                {"id": "b", "phase": "synced", "at": 2.5, "action": "slide", "duration_ms": 500},
            ],
            "safe_zones": [{"id": "z", "start": 4.0, "end": 6.0}],
        }

    def test_range_lists_only_synced_events(self):
        self.assertEqual([event["id"] for event in events_in_range(self.pattern, 1.0, 3.0)], ["a", "b"])

    def test_pattern_duration_includes_zone_end(self):
        self.assertEqual(pattern_duration(self.pattern), 6.0)

    def test_phase_duration_resets_for_each_lane(self):
        self.assertEqual(phase_duration(self.pattern, "pre_sync"), 1.0)
        self.assertEqual(phase_duration(self.pattern, "synced"), 3.0)
        self.assertEqual(phase_duration(self.pattern, "post_game"), 0.0)

    def test_playhead_scroll_tracks_long_timeline(self):
        self.assertEqual(playhead_scroll_fraction(200, 800, 2000), 0.0)
        self.assertAlmostEqual(playhead_scroll_fraction(1500, 800, 2000), 0.41)

    def test_range_summary_stays_compact_across_many_events(self):
        self.pattern["events"].extend(
            {"id": f"event-{index}", "phase": "synced", "at": 3.0 + index / 10, "action": "tap"}
            for index in range(20)
        )

        count, detail = range_summary(self.pattern, 1.0, 5.0)

        self.assertEqual(count, 22)
        self.assertIn("jump@1.500s", detail)
        self.assertIn("และอีก 18", detail)
        self.assertLess(len(detail), 120)

    def test_timeline_delete_forwards_selected_zone(self):
        selected = []
        editor = object.__new__(TimelineEditor)
        editor.selected_zone_id = "zone_007"
        editor.on_delete = selected.append

        editor._delete_zone()

        self.assertEqual(selected, ["zone_007"])

    def test_zone_edge_hit_wins_at_same_position_as_event(self):
        editor = object.__new__(TimelineEditor)
        editor.pattern = {
            "events": [{"id": "event-on-edge", "phase": "synced", "at": 4.0}],
            "safe_zones": [{"id": "zone_007", "start": 4.0, "end": 6.0}],
        }
        editor.pixels_per_second = 70.0

        hit = editor._zone_edge_at(editor._time_to_x(4.0), 120)

        self.assertIsNotNone(hit)
        self.assertEqual((hit[0]["id"], hit[1]), ("zone_007", "start"))


if __name__ == "__main__":
    unittest.main()
