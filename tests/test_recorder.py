import time
import unittest

from recorder import Recorder, prepare_recording_candidate
from state import StateMachine


class _Shell:
    def __init__(self):
        self.commands = []

    def send(self, command):
        self.commands.append(command)

    def close(self):
        pass


class RecorderRapidTapTests(unittest.TestCase):
    def test_recording_over_pattern_with_safe_zone_converts_covered_event(self):
        recorder = Recorder(StateMachine())
        recorder.start({
            "name": "safe-base",
            "events": [],
            "safe_zones": [{"id": "zone_001", "start": 0.5, "end": 2.0, "label": "safe"}],
        })
        recorder.sync()
        anchor = recorder._anchor
        recorder.key_down("j", now=anchor + 1.0, send_input=False)
        recorder.key_up("j", now=anchor + 1.05)

        pattern, _warnings = recorder.finish()

        self.assertEqual(pattern["safe_zones"][0]["id"], "zone_001")
        self.assertEqual(pattern["events"][0]["event_class"], "safe_random")
        self.assertEqual(pattern["events"][0]["safe_zone_id"], "zone_001")
        self.assertEqual(pattern["events"][0]["options"], {"none": 40, "jump": 40, "slide": 20})

    def test_new_recording_destination_starts_without_safe_zones(self):
        recorder = Recorder(StateMachine())
        recorder.start({
            "name": "safe-base",
            "events": [],
            "safe_zones": [{"id": "zone_001", "start": 0.5, "end": 2.0, "label": "safe"}],
        })
        recorder.sync()
        anchor = recorder._anchor
        recorder.key_down("j", now=anchor + 1.0, send_input=False)
        recorder.key_up("j", now=anchor + 1.05)
        recorded, _ = recorder.finish()

        candidate, _ = prepare_recording_candidate(recorded, "fresh-take", new_pattern=True)

        self.assertEqual(candidate["name"], "fresh-take")
        self.assertEqual(candidate["safe_zones"], [])
        self.assertEqual(candidate["events"][0]["event_class"], "required")
        self.assertEqual(candidate["events"][0]["action"], "jump")
        self.assertEqual(recorded["safe_zones"][0]["id"], "zone_001")

    def test_overwrite_recording_destination_keeps_safe_zones(self):
        recorded = {
            "name": "safe-base",
            "events": [{
                "id": "evt_0001", "at": 1.0, "phase": "synced",
                "event_class": "required", "type": "action", "action": "jump",
            }],
            "safe_zones": [{"id": "zone_001", "start": 0.5, "end": 2.0, "label": "safe"}],
        }

        candidate, _ = prepare_recording_candidate(recorded, "existing", new_pattern=False)

        self.assertEqual(candidate["name"], "existing")
        self.assertEqual(candidate["safe_zones"][0]["id"], "zone_001")
        self.assertEqual(candidate["events"][0]["event_class"], "safe_random")

    def test_sync_can_backdate_anchor_to_first_matching_frame(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "anchor", "events": []})
        now = time.perf_counter()
        recorder.sync(automatic=True, anchor=now - 0.2)
        recorder.key_down("j", now=now)
        pattern, _ = recorder.finish()
        self.assertAlmostEqual(pattern["events"][0]["at"], 0.2, delta=0.03)

    def test_mumu_keymap_can_record_without_duplicate_adb_input(self):
        shell = _Shell()
        recorder = Recorder(StateMachine())
        recorder.start({"name": "keys", "events": []}, shell)
        recorder.key_down("j", send_input=False)
        recorder.key_down("k", send_input=False)
        recorder.key_up("k")
        pattern, _ = recorder.finish()
        self.assertEqual(shell.commands, [])
        self.assertEqual([event["action"] for event in pattern["events"]], ["jump", "slide"])

    def test_jump_release_allows_repeated_j_presses(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "repeat-j", "events": []})
        armed = recorder._armed_at
        recorder.key_down("j", now=armed + 1.0, send_input=False)
        recorder.key_up("j", now=armed + 1.05)
        recorder.key_down("j", now=armed + 1.24, send_input=False)
        recorder.key_up("j", now=armed + 1.29)
        status = recorder.status(now=armed + 1.3)
        pattern, _ = recorder.finish()
        self.assertEqual([event["action"] for event in pattern["events"]], ["jump", "jump"])
        self.assertEqual(status["last_jump_kind"], "double")
        self.assertEqual(status["last_jump_gap_ms"], 240)

    def test_tap_before_sync_is_recorded_as_pre_sync(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "pre", "events": []})
        armed = recorder._armed_at
        recorder.record_external_tap("tap", x=100, y=200, started_at=armed + 0.75)
        recorder.sync()
        anchor = recorder._anchor
        recorder.record_external_tap("tap", x=300, y=400, started_at=anchor + 0.25)
        pattern, _ = recorder.finish()
        self.assertCountEqual([event["phase"] for event in pattern["events"]], ["pre_sync", "synced"])
        phases = {event["phase"]: event for event in pattern["events"]}
        self.assertAlmostEqual(phases["pre_sync"]["at"], 0.75)
        self.assertAlmostEqual(phases["synced"]["at"], 0.25)

    def test_post_game_uses_new_anchor_and_ignores_jump_keys(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "result", "events": []})
        recorder.sync()
        detected = recorder._anchor + 10.0
        self.assertTrue(recorder.mark_post_game(detected))
        recorder.key_down("j", now=detected + 0.1, send_input=False)
        recorder.key_up("j", now=detected + 0.2)
        recorder.record_external_tap("tap", x=460, y=620, started_at=detected + 0.35)
        status = recorder.status(now=detected + 0.5)
        pattern, _ = recorder.finish()
        self.assertEqual(status["post_game_count"], 1)
        self.assertEqual([event["action"] for event in pattern["events"]], ["tap"])
        self.assertEqual(pattern["events"][0]["phase"], "post_game")
        self.assertAlmostEqual(pattern["events"][0]["at"], 0.35)

    def test_rapid_slide_taps_merge_into_one_hold(self):
        recorder = Recorder(StateMachine())
        recorder.start({
            "name": "rapid",
            "events": [],
            "recording": {"rapid_tap_to_hold": True, "rapid_tap_gap_ms": 180},
        })
        recorder.sync()
        anchor = recorder._anchor
        recorder.record_external_tap("slide", started_at=anchor + 1.0, duration_ms=60)
        _event, merged = recorder.record_external_tap("slide", started_at=anchor + 1.2, duration_ms=60)
        pattern, _ = recorder.finish()
        self.assertTrue(merged)
        self.assertEqual(len(pattern["events"]), 1)
        self.assertEqual(pattern["events"][0]["duration_ms"], 260)

    def test_jump_taps_remain_separate(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "jump", "events": []})
        recorder.sync()
        anchor = recorder._anchor
        recorder.record_external_tap("jump", started_at=anchor + 1.0)
        recorder.record_external_tap("jump", started_at=anchor + 1.1)
        status = recorder.status(now=anchor + 1.2)
        pattern, _ = recorder.finish()
        self.assertEqual(len(pattern["events"]), 2)
        self.assertEqual(status["last_jump_kind"], "double")
        self.assertEqual(status["last_jump_gap_ms"], 100)

    def test_generic_tap_is_recorded_with_coordinates(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "touch", "events": []})
        recorder.sync()
        anchor = recorder._anchor
        recorder.record_external_tap("tap", x=640, y=360, started_at=anchor + 1.0)
        status = recorder.status(now=anchor + 2.0)
        pattern, _ = recorder.finish()
        self.assertEqual(status["event_count"], 1)
        self.assertAlmostEqual(status["elapsed_seconds"], 2.0)
        self.assertEqual((pattern["events"][0]["x"], pattern["events"][0]["y"]), (640, 360))

    def test_rapid_generic_taps_merge_to_hold(self):
        recorder = Recorder(StateMachine())
        recorder.start({"name": "touch", "events": [], "recording": {"rapid_tap_to_hold": True, "rapid_tap_gap_ms": 180}})
        recorder.sync()
        anchor = recorder._anchor
        recorder.record_external_tap("tap", x=640, y=360, started_at=anchor + 1.0, duration_ms=60)
        _event, merged = recorder.record_external_tap("tap", x=648, y=355, started_at=anchor + 1.2, duration_ms=60)
        pattern, _ = recorder.finish()
        self.assertTrue(merged)
        self.assertEqual(pattern["events"][0]["action"], "hold")
        self.assertEqual(pattern["events"][0]["duration_ms"], 260)


if __name__ == "__main__":
    unittest.main()
