import random
import time
import unittest

from player import Player, ScheduledAction, prepare_round_events
from state import StateMachine


class _FakeShell:
    def __init__(self):
        self.commands = []
        self.closed = False

    def send(self, command):
        self.commands.append(command)

    def close(self):
        self.closed = True


class _FakeADB:
    def __init__(self):
        self.shell = _FakeShell()

    def probe_device(self, _serial):
        return object()

    def open_persistent_shell(self, _serial):
        return self.shell


class PlayerSchedulingTests(unittest.TestCase):
    def test_required_is_always_scheduled_without_jitter(self):
        pattern = {"name": "p", "events": [{"id": "r", "at": 2.35, "event_class": "required", "type": "action", "action": "jump", "chance": 1, "jitter_ms": 999}]}
        actions = prepare_round_events(pattern, random.Random(1))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].at, 2.35)

    def test_weighted_none_can_skip(self):
        pattern = {
            "name": "p", "safe_zones": [{"id": "z", "start": 0, "end": 2}],
            "events": [{"id": "s", "at": 1, "event_class": "safe_random", "type": "choice", "options": {"none": 1, "jump": 0, "slide": 0}, "safe_zone_id": "z"}],
        }
        self.assertEqual(prepare_round_events(pattern, random.Random(1)), [])

    def test_tap_and_hold_use_recorded_coordinates(self):
        controls = {"jump": {"x": 1, "y": 2}, "slide": {"x": 3, "y": 4}}
        self.assertEqual(Player._command(ScheduledAction(1, "tap", x=555, y=333), controls), "input tap 555 333")
        self.assertEqual(Player._command(ScheduledAction(1, "hold", 420, x=555, y=333), controls), "input swipe 555 333 555 333 420")

    def test_pre_sync_events_play_before_sync_and_loop_delay(self):
        adb = _FakeADB()
        player = Player(adb, StateMachine())
        sync_calls = []
        pattern = {
            "name": "loop",
            "events": [
                {"id": "pre", "phase": "pre_sync", "at": 0, "action": "tap", "x": 10, "y": 20},
                {"id": "post", "phase": "synced", "at": 0, "action": "jump"},
            ],
        }
        player.play(pattern, "serial", repeat_count=2, loop_interval_ms=1, sync_each_loop=True, sync_waiter=lambda *_args: sync_calls.append(True) or True)
        self.assertEqual(len(sync_calls), 2)
        self.assertEqual(adb.shell.commands, ["input tap 10 20", "input tap 160 635"] * 2)

    def test_pre_sync_extra_delays_only_sync_boundary(self):
        adb = _FakeADB()
        sent = []
        sync_called = []
        adb.shell.send = lambda command: (adb.shell.commands.append(command), sent.append((command, time.perf_counter())))
        player = Player(adb, StateMachine())
        pattern = {
            "name": "pre-padding",
            "playback": {"pre_sync_extra_ms": 40},
            "events": [
                {"id": "pre", "phase": "pre_sync", "at": 0, "action": "tap", "x": 10, "y": 20},
                {"id": "synced", "phase": "synced", "at": 0, "action": "jump"},
            ],
        }
        started = time.perf_counter()

        player.play(
            pattern,
            "serial",
            sync_waiter=lambda *_args: sync_called.append(time.perf_counter()) or True,
        )

        self.assertGreaterEqual(sync_called[0] - started, 0.035)
        self.assertLess(sent[1][1] - sync_called[0], 0.03)
        self.assertEqual(adb.shell.commands, ["input tap 10 20", "input tap 160 635"])

    def test_sync_can_run_only_on_first_loop(self):
        adb = _FakeADB()
        player = Player(adb, StateMachine())
        sync_calls = []
        pattern = {"name": "loop", "events": [{"id": "a", "at": 0, "action": "jump"}]}
        player.play(pattern, "serial", repeat_count=3, sync_each_loop=False, sync_waiter=lambda *_args: sync_calls.append(True) or True)
        self.assertEqual(len(sync_calls), 1)
        self.assertEqual(len(adb.shell.commands), 3)

    def test_pre_sync_extra_applies_only_to_rounds_that_sync(self):
        adb = _FakeADB()
        snapshots = []
        player = Player(adb, StateMachine(), on_progress=snapshots.append)
        sync_calls = []
        started = time.perf_counter()
        pattern = {
            "name": "sync-once-padding",
            "playback": {"pre_sync_extra_ms": 30},
            "events": [{"id": "a", "at": 0, "action": "jump"}],
        }

        player.play(
            pattern, "serial", repeat_count=3, sync_each_loop=False,
            sync_waiter=lambda *_args: sync_calls.append(time.perf_counter()) or True,
        )

        self.assertEqual(len(sync_calls), 1)
        self.assertGreaterEqual(sync_calls[0] - started, 0.025)
        pre_rounds = {item["round_number"] for item in snapshots if item["phase"] == "pre_sync"}
        self.assertEqual(pre_rounds, {1})

    def test_numeric_sync_anchor_compensates_confirmation_delay(self):
        adb = _FakeADB()
        sent_at = []
        adb.shell.send = lambda command: (adb.shell.commands.append(command), sent_at.append(time.perf_counter()))
        player = Player(adb, StateMachine())
        pattern = {"name": "anchor", "events": [{"id": "a", "at": 0.12, "action": "jump"}]}
        started = time.perf_counter()
        player.play(
            pattern,
            "serial",
            sync_waiter=lambda *_args: time.perf_counter() - 0.10,
        )
        self.assertEqual(adb.shell.commands, ["input tap 160 635"])
        self.assertLess(sent_at[0] - started, 0.08)

    def test_progress_reports_phase_time_events_and_delay(self):
        adb = _FakeADB()
        snapshots = []
        player = Player(adb, StateMachine(), on_progress=snapshots.append)
        pattern = {
            "name": "progress",
            "events": [
                {"id": "pre", "phase": "pre_sync", "at": 0.001, "action": "tap", "x": 10, "y": 20},
                {"id": "post", "phase": "synced", "at": 0.001, "action": "jump"},
            ],
        }
        player.play(pattern, "serial", repeat_count=2, loop_interval_ms=2, sync_waiter=lambda *_args: True)
        phases = {snapshot["phase"] for snapshot in snapshots}
        self.assertTrue({"pre_sync", "waiting_sync", "synced", "loop_delay", "completed"}.issubset(phases))
        synced = [snapshot for snapshot in snapshots if snapshot["phase"] == "synced"]
        self.assertTrue(any(snapshot["event_index"] == 1 and snapshot["event_total"] == 1 for snapshot in synced))
        self.assertTrue(all(snapshot["elapsed"] >= 0 for snapshot in snapshots))

    def test_result_detection_cuts_gameplay_and_runs_only_post_game_taps(self):
        adb = _FakeADB()
        snapshots = []
        player = Player(adb, StateMachine(), on_progress=snapshots.append)
        pattern = {
            "name": "result",
            "events": [
                {"id": "late-jump", "phase": "synced", "at": 0.03, "action": "jump"},
                {"id": "ok", "phase": "post_game", "at": 0.001, "action": "tap", "x": 460, "y": 620},
            ],
        }
        player.play(
            pattern, "serial", sync_waiter=lambda *_args: True,
            result_waiter=lambda _stop, _cancel, _duration: True,
        )
        self.assertEqual(adb.shell.commands, ["input tap 460 620"])
        self.assertIn("post_game", {snapshot["phase"] for snapshot in snapshots})

    def test_result_late_fallback_waits_after_last_game_event(self):
        adb = _FakeADB()
        player = Player(adb, StateMachine())
        pattern = {
            "name": "late-result",
            "events": [
                {"id": "jump", "phase": "synced", "at": 0, "action": "jump"},
                {"id": "ok", "phase": "post_game", "at": 0, "action": "tap", "x": 460, "y": 620},
            ],
        }

        def late_result(_stop, _cancel, _duration):
            time.sleep(0.01)
            return True

        player.play(pattern, "serial", sync_waiter=lambda *_args: True, result_waiter=late_result)
        self.assertEqual(adb.shell.commands, ["input tap 160 635", "input tap 460 620"])

    def test_result_timeout_never_sends_post_game_tap(self):
        adb = _FakeADB()
        snapshots = []
        player = Player(adb, StateMachine(), on_progress=snapshots.append)
        pattern = {
            "name": "no-result",
            "events": [{"id": "ok", "phase": "post_game", "at": 0, "action": "tap", "x": 460, "y": 620}],
        }
        player.play(
            pattern, "serial", sync_waiter=lambda *_args: True,
            result_waiter=lambda _stop, _cancel, _duration: False,
        )
        self.assertEqual(adb.shell.commands, [])
        self.assertIn("result_timeout", {snapshot["phase"] for snapshot in snapshots})


if __name__ == "__main__":
    unittest.main()
