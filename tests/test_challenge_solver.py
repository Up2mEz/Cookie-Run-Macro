import random
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from challenge_solver import (
    CARD_CENTERS,
    CARD_INTERIORS,
    CardChallengeSolver,
    ChallengeSolveError,
    ChallengeTiming,
    SIX_CARD_MIN_MARGIN,
    SIX_CARD_CONSENSUS_MIN_MARGIN,
    _card_grid_confident,
    analyze_card_grid,
    card_grid_actionable,
)


def _card_screen(targets=(3, 4), *, jumping=False, color=(240, 120, 30)):
    image = Image.new("RGB", (1280, 720), (218, 213, 202))
    draw = ImageDraw.Draw(image)
    present = (0, 1, 3, 4, 5) if jumping else tuple(range(6))
    for slot in present:
        left, top, right, bottom = CARD_INTERIORS[slot]
        draw.rectangle((left, top, right, bottom), fill=(255, 241, 219))
        cx, cy = CARD_CENTERS[slot]
        if slot in targets:
            draw.ellipse((cx - 42, cy - 18, cx + 42, cy + 18), fill=color)
            draw.rectangle((cx + 20, cy - 5, cx + 55, cy + 6), fill=(80, 45, 20))
        else:
            draw.ellipse((cx - 18, cy - 42, cx + 18, cy + 42), fill=color)
            draw.rectangle((cx - 5, cy + 20, cx + 6, cy + 58), fill=(80, 45, 20))
    return image


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return False


class ChallengeAnalyzerTests(unittest.TestCase):
    def test_six_card_real_frame_threshold_uses_requested_margin(self):
        self.assertEqual(SIX_CARD_MIN_MARGIN, 1.8)
        self.assertEqual(SIX_CARD_CONSENSUS_MIN_MARGIN, 1.0)
        self.assertTrue(_card_grid_confident("six_cards", 4.5, 2.1))
        self.assertFalse(_card_grid_confident("six_cards", 20.0, 1.79))

    def test_borderline_six_card_frame_is_only_actionable_for_consensus(self):
        strong = analyze_card_grid(_card_screen((1, 5)))
        borderline = strong.__class__(**{**strong.__dict__, "confident": False, "confidence_margin": 1.59})
        ambiguous = strong.__class__(**{**strong.__dict__, "confident": False, "confidence_margin": 0.99})
        self.assertTrue(card_grid_actionable(borderline))
        self.assertFalse(card_grid_actionable(ambiguous))

    def test_sliding_grid_finds_exactly_two_outliers(self):
        analysis = analyze_card_grid(_card_screen((1, 5)))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.kind, "six_cards")
        self.assertEqual(analysis.target_slots, (1, 5))
        self.assertTrue(analysis.confident)

    def test_six_card_layout_accepts_realistic_animated_majority_variation(self):
        image = _card_screen((3, 5))
        draw = ImageDraw.Draw(image)
        colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 0, 255))
        # Animation can make the four majority cards differ slightly from each
        # other. Cluster scoring must still keep the two answer cards together.
        for color, slot in zip(colors, (0, 1, 2, 4)):
            cx, cy = CARD_CENTERS[slot]
            draw.rectangle((cx - 25, cy - 25, cx + 25, cy + 25), fill=color)
        analysis = analyze_card_grid(image)
        self.assertEqual(analysis.kind, "six_cards")
        self.assertEqual(analysis.target_slots, (3, 5))
        self.assertGreater(analysis.confidence_margin, SIX_CARD_MIN_MARGIN)
        self.assertTrue(analysis.confident)

    def test_six_card_cluster_handles_small_character_without_absolute_score_floor(self):
        image = Image.new("RGB", (1280, 720), (218, 213, 202))
        draw = ImageDraw.Draw(image)
        for slot in range(6):
            left, top, right, bottom = CARD_INTERIORS[slot]
            draw.rectangle((left, top, right, bottom), fill=(255, 241, 219))
            cx, cy = CARD_CENTERS[slot]
            if slot in {2, 4}:
                draw.ellipse((cx - 22, cy - 8, cx + 22, cy + 8), fill=(80, 200, 220))
            else:
                draw.ellipse((cx - 8, cy - 22, cx + 8, cy + 22), fill=(80, 200, 220))
        analysis = analyze_card_grid(image)
        self.assertEqual(analysis.target_slots, (2, 4))
        self.assertLess(analysis.target_score_floor, 8.5)
        self.assertGreaterEqual(analysis.confidence_margin, SIX_CARD_MIN_MARGIN)
        self.assertTrue(analysis.confident)

    def test_jumping_grid_finds_one_outlier_and_missing_top_right(self):
        analysis = analyze_card_grid(_card_screen((1,), jumping=True))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.kind, "five_cards")
        self.assertEqual(analysis.present_slots, (0, 1, 3, 4, 5))
        self.assertEqual(analysis.target_slots, (1,))
        self.assertTrue(analysis.confident)

    def test_normal_gameplay_is_not_mistaken_for_card_grid(self):
        self.assertIsNone(analyze_card_grid(Image.new("RGB", (1280, 720), "black")))

    def test_tap_points_scale_back_to_capture_resolution(self):
        analysis = analyze_card_grid(_card_screen((1, 5)).resize((640, 360)))
        self.assertEqual(
            analysis.target_points,
            tuple((round(CARD_CENTERS[slot][0] / 2), round(CARD_CENTERS[slot][1] / 2)) for slot in (1, 5)),
        )


class ChallengeSolverTimingTests(unittest.TestCase):
    def test_three_matching_borderline_frames_confirm_without_a_strong_frame(self):
        strong = analyze_card_grid(_card_screen((1, 5)))
        borderline = strong.__class__(**{**strong.__dict__, "confident": False, "confidence_margin": 1.59})
        analyses = iter((borderline, borderline))
        clock = _FakeClock()
        solver = CardChallengeSolver(
            lambda: _card_screen((1, 5)),
            lambda _x, _y: None,
            __import__("threading").Event(),
            timing=ChallengeTiming(consensus_gap_ms=250),
            clock=clock.clock,
            wait=clock.wait,
        )
        with patch("challenge_solver.analyze_card_grid", side_effect=lambda _image: next(analyses)):
            confirmed = solver._capture_confident("six_cards", seed=borderline)
        self.assertEqual(confirmed.target_slots, (1, 5))
        self.assertEqual(clock.waits, [0.25])

    def test_consensus_keeps_same_targets_across_one_weak_animation_frame(self):
        strong = analyze_card_grid(_card_screen((1, 5)))
        weak = strong.__class__(**{**strong.__dict__, "confident": False, "confidence_margin": 1.5})
        analyses = iter((strong, weak, strong))
        clock = _FakeClock()
        solver = CardChallengeSolver(
            lambda: _card_screen((1, 5)),
            lambda _x, _y: None,
            __import__("threading").Event(),
            timing=ChallengeTiming(consensus_gap_ms=250),
            clock=clock.clock,
            wait=clock.wait,
        )
        with patch(
            "challenge_solver.analyze_card_grid", side_effect=lambda _image: next(analyses),
        ):
            confirmed = solver._capture_confident("six_cards")
        self.assertEqual(confirmed.target_slots, (1, 5))
        self.assertEqual(clock.waits, [0.25, 0.25])

    def test_three_rounds_tap_sequentially_and_wait_about_two_seconds(self):
        frames = []
        rounds = [
            _card_screen((3, 4), color=(230, 90, 30)),
            _card_screen((1, 2), color=(80, 170, 80)),
            _card_screen((2, 4), color=(90, 100, 230)),
        ]
        # Round 1 confirmation; rounds 2/3 transition confirmation followed by
        # a fresh two-frame confirmation before tapping.
        frames.extend([rounds[0], rounds[0]])
        frames.extend([rounds[1], rounds[1], rounds[1], rounds[1], rounds[1]])
        frames.extend([rounds[2], rounds[2], rounds[2], rounds[2], rounds[2]])
        iterator = iter(frames)
        clock = _FakeClock()
        taps = []
        solver = CardChallengeSolver(
            lambda: next(iterator),
            lambda x, y: taps.append((clock.now, x, y)),
            __import__("threading").Event(),
            timing=ChallengeTiming(
                first_click_delay_ms=1000,
                inter_card_min_ms=600, inter_card_max_ms=600,
                next_round_min_ms=2000, next_round_max_ms=2000,
                transition_timeout_ms=6500, consensus_gap_ms=250,
            ),
            rng=random.Random(2), clock=clock.clock, wait=clock.wait,
        )
        solved = solver.solve_three_rounds(analyze_card_grid(rounds[0]))
        self.assertEqual(solved, 3)
        self.assertEqual(len(taps), 6)
        for index in (0, 2, 4):
            self.assertGreaterEqual(taps[index + 1][0] - taps[index][0], 0.599)
        self.assertGreaterEqual(taps[2][0] - taps[1][0], 2.0)
        self.assertGreaterEqual(taps[4][0] - taps[3][0], 2.0)
        self.assertNotEqual(taps[0][1:], taps[1][1:])

    def test_unchanged_round_never_repeats_clicks(self):
        frame = _card_screen((3, 4))
        clock = _FakeClock()
        taps = []
        solver = CardChallengeSolver(
            lambda: frame,
            lambda x, y: taps.append((clock.now, x, y)),
            __import__("threading").Event(),
            timing=ChallengeTiming(
                first_click_delay_ms=500,
                inter_card_min_ms=250, inter_card_max_ms=250,
                next_round_min_ms=1000, next_round_max_ms=1000,
                transition_timeout_ms=3000, consensus_gap_ms=250,
            ),
            rng=random.Random(1), clock=clock.clock, wait=clock.wait,
        )
        with self.assertRaisesRegex(ChallengeSolveError, "ไม่คลิกซ้ำ"):
            solver.solve_three_rounds(analyze_card_grid(frame))
        self.assertEqual(len(taps), 2)

    def test_same_targets_with_animation_never_count_as_next_round(self):
        frames = __import__("itertools").cycle((
            _card_screen((3, 4), color=(230, 90, 30)),
            _card_screen((3, 4), color=(220, 120, 50)),
            _card_screen((3, 4), color=(245, 80, 45)),
        ))
        initial = next(frames)
        clock = _FakeClock()
        taps = []
        solver = CardChallengeSolver(
            lambda: next(frames),
            lambda x, y: taps.append((clock.now, x, y)),
            __import__("threading").Event(),
            timing=ChallengeTiming(
                first_click_delay_ms=500,
                inter_card_min_ms=250, inter_card_max_ms=250,
                next_round_min_ms=1000, next_round_max_ms=1000,
                transition_timeout_ms=3000, consensus_gap_ms=250,
            ),
            rng=random.Random(1), clock=clock.clock, wait=clock.wait,
        )
        with self.assertRaisesRegex(ChallengeSolveError, "ไม่คลิกซ้ำ"):
            solver.solve_three_rounds(analyze_card_grid(initial))
        self.assertEqual(len(taps), 2)


if __name__ == "__main__":
    unittest.main()
