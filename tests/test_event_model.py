import unittest

from event_model import DEFAULT_SAFE_RANDOM_OPTIONS, EDITOR_VALIDATION_ERROR, EventValidationError, apply_safe_random_options, normalize_event, normalize_pattern, normalize_pattern_for_editing, remove_safe_zone, split_final_tap_for_adaptive


ZONES = [{"id": "zone_001", "start": 2.0, "end": 4.0, "label": "safe"}]
REQUIRED = [{"id": "required", "at": 1.0, "event_class": "required", "type": "action", "action": "jump", "chance": 100, "jitter_ms": 0}]


class EventModelTests(unittest.TestCase):
    def test_changing_final_play_tap_to_adaptive_preserves_the_tap(self):
        events = [
            {"id": "random", "at": 4.5, "phase": "pre_sync", "type": "action", "action": "tap", "x": 650, "y": 564},
            {"id": "play", "at": 38.5, "phase": "pre_sync", "type": "action", "action": "tap", "x": 840, "y": 625},
        ]
        remaining, adaptive, split = split_final_tap_for_adaptive(
            events,
            {"id": "play", "at": 38.5, "phase": "pre_sync", "type": "adaptive_wait", "detect_x": 1040, "detect_y": 672},
            "play",
        )
        self.assertTrue(split)
        self.assertEqual(adaptive["at"], 4.75)
        self.assertEqual(len(remaining), 2)
        final = next(event for event in remaining if event["id"] == "play_play")
        self.assertEqual((final["at"], final["x"], final["y"]), (38.5, 840, 625))
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

    def test_required_event_inside_new_safe_zone_becomes_safe_random(self):
        pattern, warnings = normalize_pattern({
            "name": "covered",
            "safe_zones": ZONES,
            "events": [{"id": "obstacle", "at": 3, "phase": "synced", "action": "jump"}],
        })
        event = pattern["events"][0]
        self.assertEqual(event["event_class"], "safe_random")
        self.assertEqual(event["type"], "choice")
        self.assertEqual(event["safe_zone_id"], "zone_001")
        self.assertEqual(event["options"], DEFAULT_SAFE_RANDOM_OPTIONS)
        self.assertTrue(any("แปลง Event" in warning for warning in warnings))

    def test_safe_zone_does_not_absorb_required_event_outside_edited_boundary(self):
        pattern, warnings = normalize_pattern({
            "name": "edge-gap",
            "safety": {"safe_random_min_gap_ms": 250},
            "safe_zones": [{"id": "z", "start": 50.571, "end": 51.5}],
            "events": [
                {"id": "edge", "at": 50.377169, "action": "slide", "duration_ms": 92},
                {"id": "inside", "at": 50.588467, "action": "slide", "duration_ms": 87},
            ],
        })
        self.assertEqual(pattern["safe_zones"][0]["start"], 50.571)
        by_id = {event["id"]: event for event in pattern["events"]}
        self.assertEqual(by_id["edge"]["event_class"], "required")
        self.assertEqual(by_id["inside"]["event_class"], "safe_random")
        self.assertFalse(any("ติดขอบ" in warning for warning in warnings))

    def test_auto_safe_random_returns_to_original_required_when_zone_moves(self):
        covered, _ = normalize_pattern({
            "name": "two-way-zone",
            "safe_zones": [{"id": "z", "start": 2, "end": 4}],
            "events": [{"id": "jump", "at": 3, "phase": "synced", "action": "jump"}],
        })
        converted = covered["events"][0]
        self.assertTrue(converted["safe_zone_auto"])
        self.assertEqual(converted["safe_zone_source_action"], "jump")

        covered["safe_zones"] = [{"id": "z", "start": 3.5, "end": 4.5}]
        restored, warnings = normalize_pattern(covered)
        event = restored["events"][0]

        self.assertEqual((event["event_class"], event["type"], event["action"]), ("required", "action", "jump"))
        self.assertNotIn("safe_zone_auto", event)
        self.assertTrue(any("กลับเป็น Required jump" in warning for warning in warnings))

    def test_auto_safe_random_restores_slide_duration(self):
        covered, _ = normalize_pattern({
            "name": "slide-two-way",
            "safe_zones": [{"id": "z", "start": 2, "end": 4}],
            "events": [{"id": "slide", "at": 3, "phase": "synced", "action": "slide", "duration_ms": 275}],
        })
        covered["safe_zones"] = [{"id": "z", "start": 4, "end": 5}]

        restored, _ = normalize_pattern(covered)

        event = restored["events"][0]
        self.assertEqual((event["event_class"], event["action"], event["duration_ms"]), ("required", "slide", 275))

    def test_repair_mode_also_restores_auto_random_after_zone_moves(self):
        pattern = {
            "name": "repair-two-way-zone",
            "safe_zones": [{"id": "z", "start": 4, "end": 5}],
            "events": [
                {
                    "id": "auto-slide", "at": 3, "phase": "synced",
                    "event_class": "safe_random", "type": "choice",
                    "options": DEFAULT_SAFE_RANDOM_OPTIONS, "duration_ms": 275,
                    "safe_zone_id": "z", "safe_zone_auto": True,
                    "safe_zone_source_action": "slide",
                    "safe_zone_source_duration_ms": 275,
                },
                {"id": "still-bad", "at": 6, "phase": "post_game", "action": "jump"},
            ],
        }

        editable, _ = normalize_pattern_for_editing(pattern)
        by_id = {event["id"]: event for event in editable["events"]}

        self.assertEqual(
            (by_id["auto-slide"]["event_class"], by_id["auto-slide"]["action"], by_id["auto-slide"]["duration_ms"]),
            ("required", "slide", 275),
        )
        self.assertNotIn(EDITOR_VALIDATION_ERROR, by_id["auto-slide"])
        self.assertIn(EDITOR_VALIDATION_ERROR, by_id["still-bad"])

    def test_auto_random_zone_extends_until_default_slide_can_finish(self):
        pattern, _ = normalize_pattern({
            "name": "finish-inside",
            "safe_zones": [{"id": "z", "start": 2.0, "end": 3.1}],
            "events": [{"id": "jump", "at": 3.0, "action": "jump"}],
        })
        self.assertEqual(pattern["safe_zones"][0]["end"], 3.4)
        self.assertEqual(pattern["events"][0]["duration_ms"], 400)

    def test_safe_zone_does_not_convert_touch_or_non_synced_event(self):
        pattern, _ = normalize_pattern({
            "name": "keep-required",
            "safe_zones": ZONES,
            "events": [
                {"id": "pre", "at": 3, "phase": "pre_sync", "action": "jump"},
                {"id": "tap", "at": 3, "phase": "synced", "action": "tap", "x": 10, "y": 20},
            ],
        })
        self.assertTrue(all(event["event_class"] == "required" for event in pattern["events"]))

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

    def test_bulk_safe_random_options_support_selected_or_all(self):
        source, _ = normalize_pattern({
            "name": "bulk", "safe_zones": ZONES,
            "events": [
                {"id": "a", "at": 2.5, "action": "jump"},
                {"id": "b", "at": 3.2, "action": "slide", "duration_ms": 300},
            ],
        })
        selected, _warnings, changed = apply_safe_random_options(source, {"jump": 70, "slide": 10, "none": 20}, ["a"])
        self.assertEqual(changed, 1)
        self.assertEqual(selected["events"][0]["options"], {"none": 20, "jump": 70, "slide": 10})
        self.assertEqual(selected["events"][1]["options"], DEFAULT_SAFE_RANDOM_OPTIONS)
        all_events, _warnings, changed = apply_safe_random_options(source, {"jump": 10, "slide": 30, "none": 60}, None)
        self.assertEqual(changed, 2)
        self.assertTrue(all(event["options"] == {"none": 60, "jump": 10, "slide": 30} for event in all_events["events"]))

    def test_bulk_safe_random_options_require_exactly_100_percent(self):
        with self.assertRaisesRegex(EventValidationError, "100%"):
            apply_safe_random_options({"events": []}, {"jump": 40, "slide": 20, "none": 30})

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
        self.assertEqual(pattern["playback"]["repeat_count"], 5)
        self.assertTrue(pattern["playback"]["loop_forever"])
        self.assertEqual(pattern["playback"]["loop_interval_ms"], 2500)
        self.assertEqual(pattern["playback"]["pre_sync_extra_ms"], 0)
        self.assertEqual(pattern["playback"]["pre_sync_delay_mode"], "fixed")
        self.assertEqual(pattern["playback"]["pre_sync_random_min_ms"], 300)
        self.assertEqual(pattern["playback"]["pre_sync_random_max_ms"], 500)
        self.assertFalse(pattern["playback"]["sync_each_loop"])

    def test_random_pre_sync_delay_settings_are_preserved(self):
        pattern, _ = normalize_pattern({
            "name": "random-delay",
            "playback": {
                "pre_sync_delay_mode": "random",
                "pre_sync_random_min_ms": 300,
                "pre_sync_random_max_ms": 500,
                "pre_sync_random_min_delta_ms": 50,
                "pre_sync_random_history": 3,
            },
        })
        playback = pattern["playback"]
        self.assertEqual(playback["pre_sync_delay_mode"], "random")
        self.assertEqual(playback["pre_sync_random_min_ms"], 300)
        self.assertEqual(playback["pre_sync_random_max_ms"], 500)
        self.assertEqual(playback["pre_sync_random_min_delta_ms"], 50)
        self.assertEqual(playback["pre_sync_random_history"], 3)

    def test_invalid_random_pre_sync_range_is_rejected(self):
        with self.assertRaisesRegex(EventValidationError, "Min ต้องไม่เกิน Max"):
            normalize_pattern({
                "name": "bad-delay",
                "playback": {
                    "pre_sync_delay_mode": "random",
                    "pre_sync_random_min_ms": 500,
                    "pre_sync_random_max_ms": 300,
                },
            })

    def test_random_pre_sync_range_must_fit_requested_history(self):
        with self.assertRaisesRegex(EventValidationError, "ระยะห่าง × จำนวนย้อนหลัง"):
            normalize_pattern({
                "name": "too-narrow-delay",
                "playback": {
                    "pre_sync_delay_mode": "random",
                    "pre_sync_random_min_ms": 300,
                    "pre_sync_random_max_ms": 400,
                    "pre_sync_random_min_delta_ms": 50,
                    "pre_sync_random_history": 3,
                },
            })

    def test_invalid_challenge_delay_order_is_rejected(self):
        with self.assertRaisesRegex(EventValidationError, "ระหว่างการ์ด Min"):
            normalize_pattern({
                "name": "bad-card-delay",
                "post_game": {
                    "challenge_inter_card_min_ms": 1000,
                    "challenge_inter_card_max_ms": 500,
                },
            })

    def test_pre_sync_extra_is_independent_and_clamped(self):
        pattern, _ = normalize_pattern({"name": "timing", "playback": {"pre_sync_extra_ms": 750}})
        self.assertEqual(pattern["playback"]["pre_sync_extra_ms"], 750)
        self.assertEqual(pattern["sync"]["offset_ms"], 0)
        self.assertEqual(pattern["post_game"]["min_gameplay_seconds"], 15)

    def test_adaptive_wait_keeps_padding_and_uses_final_tap(self):
        pattern, warnings = normalize_pattern({
            "name": "adaptive",
            "playback": {"pre_sync_extra_ms": 60000, "sync_each_loop": False},
            "events": [
                {"id": "open", "at": 1, "phase": "pre_sync", "action": "tap", "x": 20, "y": 30},
                {
                    "id": "wait", "at": 2, "phase": "pre_sync", "event_class": "required",
                    "type": "adaptive_wait", "action": "tap",
                    "detect_x": 1040, "detect_y": 672,
                },
                {"id": "play", "at": 60, "phase": "pre_sync", "action": "tap", "x": 930, "y": 610},
            ],
        })
        adaptive = pattern["events"][1]
        self.assertEqual(adaptive["type"], "adaptive_wait")
        self.assertEqual(adaptive["timeout_seconds"], 0)
        self.assertNotIn("x", adaptive)
        self.assertEqual(pattern["events"][2]["action"], "tap")
        self.assertEqual(pattern["playback"]["pre_sync_extra_ms"], 60000)
        self.assertFalse(pattern["playback"]["sync_each_loop"])
        self.assertFalse(any("ปิดเวลาเผื่อ" in warning for warning in warnings))

    def test_adaptive_wait_rejects_wrong_phase_or_later_pre_sync_event(self):
        with self.assertRaisesRegex(EventValidationError, "เฉพาะ phase pre_sync"):
            normalize_event({
                "id": "wait", "at": 2, "phase": "synced", "type": "adaptive_wait",
                "x": 930, "y": 610, "detect_x": 1040, "detect_y": 672,
            }, [], [], 250)
        with self.assertRaisesRegex(EventValidationError, "เหลือ Pre Sync อีก 1 Event"):
            normalize_pattern({
                "name": "bad-order",
                "events": [
                    {"id": "wait", "at": 2, "phase": "pre_sync", "type": "adaptive_wait",
                     "detect_x": 1040, "detect_y": 672},
                    {"id": "late", "at": 3, "phase": "pre_sync", "action": "tap", "x": 1, "y": 2},
                    {"id": "too-many", "at": 4, "phase": "pre_sync", "action": "tap", "x": 3, "y": 4},
                ],
            })

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
