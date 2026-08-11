import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image, ImageDraw

from challenge_solver import CARD_CENTERS, CARD_INTERIORS, analyze_card_grid
from ui.main_window import MainWindow


def _sliding_screen():
    image = Image.new("RGB", (1280, 720), (218, 213, 202))
    draw = ImageDraw.Draw(image)
    for slot in range(6):
        left, top, right, bottom = CARD_INTERIORS[slot]
        draw.rectangle((left, top, right, bottom), fill=(255, 241, 219))
        cx, cy = CARD_CENTERS[slot]
        if slot in {3, 4}:
            draw.ellipse((cx - 42, cy - 18, cx + 42, cy + 18), fill=(230, 90, 30))
        else:
            draw.ellipse((cx - 18, cy - 42, cx + 18, cy + 42), fill=(230, 90, 30))
    return image


class _Root:
    def after(self, _delay, callback):
        callback()


class _Var:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class _Detector:
    def __init__(self):
        self.calls = 0

    def check_image(self, _image):
        self.calls += 1
        return (1.0, 0.0, self.calls >= 4)


class _ADB:
    def __init__(self, frame):
        self.frame = frame
        self.captures = 0

    def capture_image(self, _serial):
        self.captures += 1
        return self.frame, "ADB Raw"

    def shell(self, *_args, **_kwargs):
        raise AssertionError("patched solver must be the only component that taps")


class _SolvedChallenge:
    calls = 0
    last_analysis = None

    def __init__(self, *_args, **_kwargs):
        pass

    def solve_three_rounds(self, analysis):
        self.__class__.calls += 1
        self.__class__.last_analysis = analysis
        return 3


class EndScreenRouterTests(unittest.TestCase):
    def test_card_screen_stops_gameplay_then_waits_for_real_result(self):
        window = object.__new__(MainWindow)
        window.root = _Root()
        window.result_status_var = _Var()
        window._result_similarity_callback = lambda *_args: None
        detector = _Detector()
        window._make_result_detector = lambda *_args, **_kwargs: detector
        pattern = {
            "post_game": {
                "challenge_enabled": True,
                "min_gameplay_seconds": 0,
                "timeout_seconds": 5,
                "poll_ms": 400,
                "challenge_first_click_delay_ms": 1000,
                "challenge_inter_card_min_ms": 500,
                "challenge_inter_card_max_ms": 900,
                "challenge_next_round_min_ms": 1800,
                "challenge_next_round_max_ms": 2200,
                "challenge_transition_timeout_ms": 6500,
            },
        }
        gameplay_end = threading.Event()
        _SolvedChallenge.calls = 0
        with patch("ui.main_window.CardChallengeSolver", _SolvedChallenge):
            found = window._result_waiter(_ADB(_sliding_screen()), "serial", pattern)(
                threading.Event(), threading.Event(), gameplay_end, 0,
            )
        self.assertTrue(found)
        self.assertTrue(gameplay_end.is_set())
        self.assertEqual(_SolvedChallenge.calls, 1)
        self.assertIn("พบ XP", window.result_status_var.value)

    def test_router_keeps_one_strong_vote_across_weak_animation_frames(self):
        window = object.__new__(MainWindow)
        window.root = _Root()
        window.result_status_var = _Var()
        window._result_similarity_callback = lambda *_args: None
        detector = _Detector()
        window._make_result_detector = lambda *_args, **_kwargs: detector
        strong = analyze_card_grid(_sliding_screen())
        weak = replace(strong, confident=False, confidence_margin=1.5)
        pattern = {
            "post_game": {
                "challenge_enabled": True,
                "min_gameplay_seconds": 0,
                "timeout_seconds": 5,
                "poll_ms": 400,
            },
        }
        _SolvedChallenge.calls = 0
        _SolvedChallenge.last_analysis = None
        with (
            patch("ui.main_window.analyze_card_grid", side_effect=(strong, weak, weak)),
            patch("ui.main_window.CardChallengeSolver", _SolvedChallenge),
        ):
            found = window._result_waiter(_ADB(_sliding_screen()), "serial", pattern)(
                threading.Event(), threading.Event(), threading.Event(), 0,
            )
        self.assertTrue(found)
        self.assertEqual(_SolvedChallenge.calls, 1)
        self.assertTrue(_SolvedChallenge.last_analysis.confident)
        self.assertEqual(_SolvedChallenge.last_analysis.target_slots, strong.target_slots)

    def test_router_accepts_three_stable_borderline_frames_without_strong_frame(self):
        window = object.__new__(MainWindow)
        window.root = _Root()
        window.result_status_var = _Var()
        window._result_similarity_callback = lambda *_args: None
        detector = _Detector()
        window._make_result_detector = lambda *_args, **_kwargs: detector
        borderline = replace(
            analyze_card_grid(_sliding_screen()),
            confident=False,
            confidence_margin=1.59,
        )
        pattern = {
            "post_game": {
                "challenge_enabled": True,
                "min_gameplay_seconds": 0,
                "timeout_seconds": 5,
                "poll_ms": 400,
            },
        }
        _SolvedChallenge.calls = 0
        _SolvedChallenge.last_analysis = None
        with (
            patch("ui.main_window.analyze_card_grid", side_effect=(borderline, borderline, borderline)),
            patch("ui.main_window.CardChallengeSolver", _SolvedChallenge),
        ):
            found = window._result_waiter(_ADB(_sliding_screen()), "serial", pattern)(
                threading.Event(), threading.Event(), threading.Event(), 0,
            )
        self.assertTrue(found)
        self.assertEqual(_SolvedChallenge.calls, 1)
        self.assertEqual(_SolvedChallenge.last_analysis.confidence_margin, 1.59)


if __name__ == "__main__":
    unittest.main()
