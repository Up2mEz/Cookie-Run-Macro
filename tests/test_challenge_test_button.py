import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image, ImageDraw

from challenge_solver import CARD_CENTERS, CARD_INTERIORS, ChallengeSolveError, ChallengeTiming
from ui.main_window import MainWindow


def _six_card_screen(targets=(3, 5)):
    image = Image.new("RGB", (1280, 720), (218, 213, 202))
    draw = ImageDraw.Draw(image)
    for slot in range(6):
        left, top, right, bottom = CARD_INTERIORS[slot]
        draw.rectangle((left, top, right, bottom), fill=(255, 241, 219))
        cx, cy = CARD_CENTERS[slot]
        if slot in targets:
            draw.ellipse((cx - 42, cy - 18, cx + 42, cy + 18), fill=(230, 90, 30))
        else:
            draw.ellipse((cx - 18, cy - 42, cx + 18, cy + 42), fill=(230, 90, 30))
    return image


class _ADB:
    def __init__(self, frame):
        self.frame = frame
        self.captures = 0
        self.shell_calls = []

    def capture_image(self, _serial):
        self.captures += 1
        return self.frame, "ADB Raw"

    def shell(self, serial, command):
        self.shell_calls.append((serial, command))


class _SolvedChallenge:
    created = None

    def __init__(self, capture, tap, stop_event, *, timing, on_status=None):
        self.capture = capture
        self.tap = tap
        self.stop_event = stop_event
        self.timing = timing
        self.on_status = on_status
        self.__class__.created = self

    def solve_three_rounds(self, analysis):
        self.analysis = analysis
        if self.on_status:
            self.on_status("ตรวจสอบแล้ว")
        return 3


class ChallengeTestButtonTests(unittest.TestCase):
    def test_standalone_solver_uses_current_screen_without_pattern_or_result_router(self):
        adb = _ADB(_six_card_screen())
        stop_event = threading.Event()
        timing = ChallengeTiming()
        statuses = []

        with patch("ui.main_window.CardChallengeSolver", _SolvedChallenge):
            result = MainWindow._solve_current_challenge(
                adb, "127.0.0.1:16416", timing, stop_event, statuses.append,
            )

        self.assertEqual(result, (3, "six_cards", "ADB Raw"))
        self.assertEqual(adb.captures, 1)
        self.assertEqual(adb.shell_calls, [])
        self.assertEqual(_SolvedChallenge.created.timing, timing)
        self.assertEqual(_SolvedChallenge.created.analysis.target_slots, (3, 5))
        self.assertEqual(statuses, ["ตรวจสอบแล้ว"])

    def test_standalone_solver_allows_borderline_pair_for_guarded_consensus(self):
        frame = _six_card_screen()
        borderline = replace(
            __import__("challenge_solver").analyze_card_grid(frame),
            confident=False,
            confidence_margin=1.59,
        )
        adb = _ADB(frame)
        with (
            patch("ui.main_window.analyze_card_grid", return_value=borderline),
            patch("ui.main_window.CardChallengeSolver", _SolvedChallenge),
        ):
            result = MainWindow._solve_current_challenge(
                adb, "127.0.0.1:16416", ChallengeTiming(), threading.Event(),
            )
        self.assertEqual(result[:2], (3, "six_cards"))
        self.assertEqual(_SolvedChallenge.created.analysis.confidence_margin, 1.59)

    def test_no_challenge_never_constructs_solver_or_taps(self):
        adb = _ADB(Image.new("RGB", (1280, 720), "black"))
        _SolvedChallenge.created = None

        with patch("ui.main_window.CardChallengeSolver", _SolvedChallenge):
            with self.assertRaisesRegex(ChallengeSolveError, "ไม่พบหน้าจอ Surprise Card"):
                MainWindow._solve_current_challenge(
                    adb, "127.0.0.1:16416", ChallengeTiming(), threading.Event(),
                )

        self.assertIsNone(_SolvedChallenge.created)
        self.assertEqual(adb.shell_calls, [])

    def test_running_flag_stays_locked_until_worker_cleanup(self):
        window = object.__new__(MainWindow)
        window.challenge_test_stop = threading.Event()
        self.assertTrue(window._challenge_test_running())
        window.challenge_test_stop.set()
        self.assertTrue(window._challenge_test_running())
        window.challenge_test_stop = None
        self.assertFalse(window._challenge_test_running())


if __name__ == "__main__":
    unittest.main()
