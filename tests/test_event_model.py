import unittest

from event_model import EventValidationError, normalize_event, normalize_pattern, remove_safe_zone


ZONES = [{"id": "zone_001", "start": 2.0, "end": 4.0, "label": "safe"}]
REQUIRED = [{"id": "required", "at": 1.0, "event_class": "required", "type": "action", "action": "jump", "chance": 100, "jitter_ms": 0}]


class EventModelTests(unittest.TestCase):
    def test_old_pattern_gets_safe_post_game_defaults(self):
        pattern, _ = normalize_pattern({"name": "old", "events": []})
        self.assertEqual(pattern["sync"]["poll_ms"], 20)
        self.assertEqual(pattern["sync"]["consecutive_matches"], 2)
        self.assertTrue(pattern["post_game"]["enabled"])
        self.assertEqual(pattern["post_game"]["roi"], {"x": 155, "y": 450, "width": 130, "height": 75})

    def test_post_game_allows_tap_but_rejects_jump(self):
        tap, _ = normalize_event(
            {"id": "ok", "at": 0.2, "phase": "post_game", "action": "tap", "x": 500, "y": 620},
            [], [], 250,
        )
        self.assertEqual(tap["phase"], "post_game")
        with self.assertRaises(EventValidationError):
            normalize_event(
                {"id": "bad", "at": 0.2, "phase": "post_game", "action": "jump"},
                [], [], 250,
            )
    def test_old_event_becomes_required(self):
        event, _ = normalize_event({"id": "old", "at": 1, "action": "jump"}, [], [], 250)
        self.assertEqual(event["event_class"], "required")
        self.assertEqual(event["chance"], 100)
        self.assertEqual(event["jitter_ms"], 0)

    def test_required_chance_and_jitter_are_forced(self):
        event, warnings = normalize_event({"id": "r", "at": 1, "action": "jump", "chance": 50, "jitter_ms": 20}, [], [], 250)
        self.assertEqual((event["chance"], event["jitter_ms"]), (100, 0))
        self.assertGreaterEqual(len(warnings), 2)

    def test_required_event_inside_safe_zone_stays_required(self):
        event, warnings = normalize_event(
            {"id": "obstacle", "at": 3, "phase": "synced", "action": "jump"},
            ZONES, [], 250,
        )
        self.assertEqual(event["event_class"], "required")
        self.assertEqual(event["action"], "jump")
        self.assertNotIn("safe_zone_id", event)
        self.assertFalse(any("Safe Random" in warning for warning in warnings))

    def test_required_choice_converts_to_deterministic_action(self):
        event, _ = normalize_event({"id": "r", "at": 1, "event_class": "required", "type": "choice", "options": {"none": 50, "jump": 20, "slide": 30}}, [], [], 250)
        self.assertEqual(event["action"], "slide")
        self.assertNotIn("options", event)

    def test_safe_random_outside_zone_is_invalid(self):
        with self.assertRaises(EventValidationError):
            normalize_event({"id": "s", "at": 5, "event_class": "safe_random", "type": "optional_action", "action": "jump", "chance": 20, "safe_zone_id": "zone_001"}, ZONES, [], 250)

    def test_jitter_is_clamped_to_zone(self):
        event, warnings = normalize_event({"id": "s", "at": 2.1, "event_class": "safe_random", "type": "optional_action", "action": "jump", "chance": 20, "jitter_ms": 500, "safe_zone_id": "zone_001"}, ZONES, [], 250)
        self.assertEqual(event["jitter_ms"], 100)
        self.assertTrue(any("ลด Jitter" in warning for warning in warnings))

    def test_negative_and_zero_weights_are_invalid(self):
        base = {"id": "s", "at": 3, "event_class": "safe_random", "type": "choice", "safe_zone_id": "zone_001"}
        with self.assertRaises(EventValidationError):
            normalize_event({**base, "options": {"none": -1, "jump": 1}}, ZONES, [], 250)
        with self.assertRaises(EventValidationError):
            normalize_event({**base, "options": {"none": 0, "jump": 0, "slide": 0}}, ZONES, [], 250)

    def test_safe_random_too_close_to_required_is_invalid(self):
        zones = [{"id": "z", "start": 0, "end": 2}]
        with self.assertRaises(EventValidationError):
            normalize_event({"id": "s", "at": 1.2, "event_class": "safe_random", "type": "optional_action", "action": "jump", "chance": 20, "safe_zone_id": "z"}, zones, REQUIRED, 250)

    def test_valid_safe_random(self):
        event, _ = normalize_event({"id": "s", "at": 3, "event_class": "safe_random", "type": "choice", "options": {"none": 50, "jump": 30, "slide": 20}, "duration_ms": 400, "jitter_ms": 30, "safe_zone_id": "zone_001"}, ZONES, REQUIRED, 250)
        self.assertEqual(event["event_class"], "safe_random")
        self.assertEqual(event["options"]["none"], 50)

    def test_safe_random_weights_are_normalized_to_percentages(self):
        event, warnings = normalize_event(
            {"id": "s", "at": 3, "event_class": "safe_random", "type": "choice",
             "options": {"none": 1, "jump": 1, "slide": 0}, "safe_zone_id": "zone_001"},
            ZONES, REQUIRED, 250,
        )
        self.assertEqual(event["options"], {"none": 50.0, "jump": 50.0, "slide": 0.0})
        self.assertTrue(any("เปอร์เซ็นต์รวม 100%" in warning for warning in warnings))

    def test_safe_random_slide_must_finish_inside_zone(self):
        with self.assertRaisesRegex(EventValidationError, "สิ้นสุดนอก Safe Zone"):
            normalize_event(
                {"id": "s", "at": 3.8, "event_class": "safe_random", "type": "choice",
                 "options": {"none": 0, "jump": 0, "slide": 100}, "duration_ms": 400,
                 "safe_zone_id": "zone_001"},
                ZONES, REQUIRED, 250,
            )

    def test_pattern_without_safe_zones_is_backward_compatible(self):
        pattern, _ = normalize_pattern({"name": "old", "events": [{"at": 1, "action": "jump"}]})
        self.assertEqual(pattern["schema_version"], 2)
        self.assertEqual(pattern["safe_zones"], [])
        self.assertEqual(pattern["stats"]["play_count"], 0)
        self.assertTrue(pattern["recording"]["rapid_tap_to_hold"])

    def test_required_tap_keeps_exact_coordinates(self):
        event, _ = normalize_event({"id": "tap", "at": 1.25, "action": "tap", "x": 640, "y": 360}, [], [], 250)
        self.assertEqual((event["action"], event["x"], event["y"]), ("tap", 640, 360))

    def test_pre_sync_phase_is_kept_for_required_event(self):
        event, _ = normalize_event({"id": "pre", "at": 0.5, "phase": "pre_sync", "action": "tap", "x": 10, "y": 20}, [], [], 250)
        self.assertEqual(event["phase"], "pre_sync")

    def test_safe_random_cannot_be_pre_sync(self):
        with self.assertRaises(EventValidationError):
            normalize_event({"id": "safe", "at": 3, "phase": "pre_sync", "event_class": "safe_random", "type": "optional_action", "action": "jump", "chance": 20, "safe_zone_id": "zone_001"}, ZONES, [], 250)

    def test_hold_requires_coordinates_and_duration(self):
        with self.assertRaises(EventValidationError):
            normalize_event({"id": "hold", "at": 1, "action": "hold", "duration_ms": 300}, [], [], 250)
        event, _ = normalize_event({"id": "hold", "at": 1, "action": "hold", "x": 100, "y": 200, "duration_ms": 300}, [], [], 250)
        self.assertEqual(event["duration_ms"], 300)

    def test_multiple_legacy_events_receive_unique_ids(self):
        pattern, _ = normalize_pattern({
            "name": "old",
            "events": [{"time": 1, "type": "jump"}, {"timestamp": 2, "type": "slide", "duration_ms": 300}],
        })
        self.assertEqual([event["id"] for event in pattern["events"]], ["evt_0001", "evt_0002"])
        self.assertEqual([event["action"] for event in pattern["events"]], ["jump", "slide"])
        self.assertTrue(all(event["phase"] == "synced" for event in pattern["events"]))

    def test_playback_loop_settings_are_normalized(self):
        pattern, _ = normalize_pattern({"name": "loop", "playback": {"repeat_count": 5, "loop_forever": True, "loop_interval_ms": 2500, "sync_each_loop": False}})
        self.assertEqual(pattern["playback"], {"repeat_count": 5, "loop_forever": True, "loop_interval_ms": 2500, "pre_sync_extra_ms": 0, "sync_each_loop": False})

    def test_pre_sync_extra_is_independent_and_clamped(self):
        pattern, _ = normalize_pattern({"name": "timing", "playback": {"pre_sync_extra_ms": 750}})
        self.assertEqual(pattern["playback"]["pre_sync_extra_ms"], 750)
        self.assertEqual(pattern["sync"]["offset_ms"], 0)
        self.assertEqual(pattern["post_game"]["min_gameplay_seconds"], 15)

    def test_overlapping_zones_are_merged_and_event_reference_is_migrated(self):
        pattern, warnings = normalize_pattern({
            "name": "zones",
            "safe_zones": [
                {"id": "a", "start": 2, "end": 3, "label": "A"},
                {"id": "b", "start": 2.5, "end": 4, "label": "B"},
            ],
            "events": [{
                "id": "safe", "at": 3.5, "event_class": "safe_random", "type": "optional_action",
                "action": "jump", "chance": 20, "safe_zone_id": "b",
            }],
        })
        self.assertEqual(pattern["safe_zones"], [{"id": "a", "start": 2.0, "end": 4.0, "label": "A / B"}])
        self.assertEqual(pattern["events"][0]["safe_zone_id"], "a")
        self.assertTrue(any("รวม Safe Zone" in warning for warning in warnings))

    def test_remove_safe_zone_can_confirm_removing_linked_safe_random(self):
        source, _ = normalize_pattern({
            "name": "delete-zone",
            "safe_zones": ZONES,
            "events": [{
                "id": "safe", "at": 3, "event_class": "safe_random", "type": "optional_action",
                "action": "jump", "chance": 20, "safe_zone_id": "zone_001",
            }],
        })
        with self.assertRaisesRegex(EventValidationError, "มี Safe Random 1 Event"):
            remove_safe_zone(source, "zone_001")

        updated, _warnings, removed_events = remove_safe_zone(
            source, "zone_001", remove_linked_events=True,
        )

        self.assertEqual(removed_events, 1)
        self.assertEqual(updated["safe_zones"], [])
        self.assertEqual(updated["events"], [])
        self.assertEqual(len(source["safe_zones"]), 1)


if __name__ == "__main__":
    unittest.main()
