import io
import threading
import time
import unittest

from PIL import Image

from sync_detector import ConsecutiveMatcher, ROI, ResultDetector, SyncDetector, SyncError, SyncResult, adb_fallback_qualified, crop_roi, decode_png, derived_shape_thresholds, equalize_comparison_size, image_similarity, image_similarity_details, is_real_fast_roi_source, stable_sync_target, summarize_sync_samples, wait_for_sync_with_retries


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class SyncDetectorTests(unittest.TestCase):
    def test_adb_fallback_accepts_supplied_gameplay_pause_score(self):
        gameplay_pause = {"combined": 0.899, "feature": 0.861, "structure": 0.931}
        pre_pause = {"combined": 0.694, "feature": 0.701, "structure": 0.631}

        self.assertTrue(adb_fallback_qualified(gameplay_pause, 0.82, 0.76, 0.72))
        self.assertFalse(adb_fallback_qualified(pre_pause, 0.82, 0.76, 0.72))

    def test_user_threshold_really_moves_all_match_gates(self):
        details = {"combined": 0.71, "feature": 0.66, "structure": 0.62}
        high_feature, high_structure = derived_shape_thresholds(0.82)
        low_feature, low_structure = derived_shape_thresholds(0.70)
        self.assertFalse(adb_fallback_qualified(details, 0.82, high_feature, high_structure))
        self.assertTrue(adb_fallback_qualified(details, 0.70, low_feature, low_structure))
        feature, structure = derived_shape_thresholds(0.82)
        self.assertAlmostEqual(feature, 0.76)
        self.assertAlmostEqual(structure, 0.72)

    def test_stable_sync_target_is_based_on_first_pause_frame_not_finish_time(self):
        early_target, early_wait = stable_sync_target(100.0, 300, now=100.04)
        late_target, late_wait = stable_sync_target(100.0, 300, now=100.18)

        self.assertAlmostEqual(early_target, 100.3)
        self.assertAlmostEqual(late_target, 100.3)
        self.assertAlmostEqual(early_wait, 0.26)
        self.assertAlmostEqual(late_wait, 0.12)

    def test_stable_sync_target_never_waits_negative_or_moves_backwards(self):
        target, remaining = stable_sync_target(100.0, -500, now=100.2)
        self.assertEqual(target, 100.0)
        self.assertEqual(remaining, 0.0)

    def test_multi_frame_summary_reports_stability_and_latency(self):
        samples = [
            {
                "details": {"combined": score, "structure": structure, "feature": feature},
                "capture_ms": capture_ms,
                "source": source,
            }
            for score, structure, feature, capture_ms, source in (
                (0.90, 0.91, 0.88, 18, "MuMu Window ROI"),
                (0.89, 0.90, 0.87, 22, "MuMu Window ROI"),
                (0.80, 0.91, 0.88, 20, "MuMu Window ROI"),
            )
        ]

        summary = summarize_sync_samples(samples, 0.82, 0.76, 0.72)

        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["capture_median_ms"], 20)
        self.assertEqual(summary["sources"], {"MuMu Window ROI": 3})
        self.assertAlmostEqual(summary["combined_min"], 0.80)

    def test_black_waiting_frame_is_not_a_real_fast_roi_sample(self):
        self.assertFalse(is_real_fast_roi_source("Fast ROI unavailable: MuMu ไม่ได้อยู่ด้านหน้า"))
        self.assertFalse(is_real_fast_roi_source("ADB Raw verification fallback"))
        self.assertTrue(is_real_fast_roi_source("MuMu Window ROI 960x540"))

    def test_unavailable_sentinel_never_enters_score_history_or_callback(self):
        template = Image.new("RGB", (20, 20), "white")
        callbacks = []
        detector = SyncDetector(
            lambda: b"", template, ROI(0, 0, 20, 20),
            capture_roi=lambda _roi: (Image.new("RGB", (20, 20), "black"), "Fast ROI unavailable: Studio foreground"),
            on_similarity=lambda score, history: callbacks.append((score, history)),
        )
        self.assertEqual(detector.check_once(), 0.0)
        self.assertEqual(detector.best_score, 0.0)
        self.assertEqual(list(detector.history), [])
        self.assertEqual(callbacks, [])

    def test_identical_images_are_one(self):
        image = Image.new("RGB", (80, 80), "white")
        self.assertAlmostEqual(image_similarity(image, image), 1.0, places=6)

    def test_different_images_are_low(self):
        self.assertLess(image_similarity(Image.new("RGB", (64, 64), "black"), Image.new("L", (80, 80), "white")), 0.05)

    def test_grayscale_rgb_and_resize_work(self):
        left = Image.new("L", (20, 30), 128)
        right = Image.new("RGB", (100, 120), (128, 128, 128))
        self.assertGreater(image_similarity(left, right), 0.99)

    def test_comparison_resizes_current_to_exact_template_dimensions(self):
        current, template = equalize_comparison_size(
            Image.new("RGB", (59, 58), "white"), Image.new("RGB", (62, 62), "white"),
        )
        self.assertEqual(current.size, (62, 62))
        self.assertEqual(current.size, template.size)

    def test_invalid_roi(self):
        with self.assertRaises(SyncError):
            crop_roi(Image.new("RGB", (100, 100)), ROI(95, 95, 20, 20))
        with self.assertRaises(SyncError):
            ROI(0, 0, 10, 10).validate((100, 100))

    def test_consecutive_matching_resets(self):
        matcher = ConsecutiveMatcher(0.8, 2)
        self.assertFalse(matcher.add(0.9))
        self.assertFalse(matcher.add(0.7))
        self.assertFalse(matcher.add(0.9))
        self.assertTrue(matcher.add(0.95))

    def test_manual_event_cancels_auto_before_capture(self):
        detector = SyncDetector(lambda: (_ for _ in ()).throw(AssertionError("capture should not run")), Image.new("RGB", (20, 20)), ROI(0, 0, 20, 20), timeout_seconds=1)
        manual = threading.Event()
        manual.set()
        self.assertEqual(detector.wait(threading.Event(), manual), SyncResult.MANUAL)

    def test_fast_roi_capture_uses_first_matching_frame_as_anchor(self):
        image = Image.new("RGB", (20, 20), "white")
        detector = SyncDetector(
            lambda: (_ for _ in ()).throw(AssertionError("ADB fallback should not run")),
            image,
            ROI(0, 0, 20, 20),
            poll_ms=20,
            consecutive_matches=2,
            timeout_seconds=1,
            capture_roi=lambda _roi: (image, "MuMu Window ROI"),
        )
        result = detector.wait(threading.Event(), threading.Event())
        self.assertEqual(result, SyncResult.AUTO)
        self.assertEqual(detector.last_capture_source, "MuMu Window ROI")
        self.assertIsNotNone(detector.detected_at)
        # OS scheduling can briefly pre-empt the Python thread; burst confirmation
        # still must stay well below a normal ADB frame.
        self.assertLess(detector.confirmation_delay_ms, 60)

    def test_native_adb_rescues_fast_roi_that_is_available_but_never_matches(self):
        template = Image.new("RGB", (20, 20), "white")
        wrong = Image.new("RGB", (20, 20), "black")
        fallback_calls = []
        detector = SyncDetector(
            lambda: b"", template, ROI(0, 0, 20, 20),
            poll_ms=20, timeout_seconds=0.08,
            capture_roi=lambda _roi: (wrong, "MuMu Window ROI 1280x720"),
            fallback_capture_roi=lambda _roi: fallback_calls.append(True) or (template, "ADB fallback"),
            fallback_grace_ms=20,
        )

        self.assertEqual(detector.wait(threading.Event(), threading.Event()), SyncResult.AUTO)
        self.assertEqual(fallback_calls, [True])
        self.assertEqual(detector.last_capture_source, "ADB fallback")

    def test_native_adb_starts_after_short_fixed_rescue_delay(self):
        template = Image.new("RGB", (20, 20), "white")
        unavailable = Image.new("RGB", (20, 20), "black")
        fallback_calls = []
        started = time.perf_counter()
        detector = SyncDetector(
            lambda: b"", template, ROI(0, 0, 20, 20),
            poll_ms=20, timeout_seconds=0.5,
            capture_roi=lambda _roi: (unavailable, "Fast ROI unavailable: MuMu not foreground"),
            fallback_capture_roi=lambda _roi: fallback_calls.append(time.perf_counter()) or (template, "ADB fallback"),
            fallback_grace_ms=60,
        )

        self.assertEqual(detector.wait(threading.Event(), threading.Event()), SyncResult.AUTO)
        self.assertEqual(len(fallback_calls), 1)
        self.assertGreaterEqual(fallback_calls[0] - started, 0.05)
        self.assertLess(fallback_calls[0] - started, 0.15)

    def test_timeout_rearms_detector_until_auto_sync_for_unattended_loop(self):
        results = [SyncResult.TIMEOUT, SyncResult.TIMEOUT, SyncResult.AUTO]
        retries = []

        class FakeDetector:
            best_score = 0.68
            best_source = "MuMu Window ROI"

            def __init__(self, result):
                self.result = result

            def wait(self, *_args):
                return self.result

        def factory():
            return FakeDetector(results.pop(0))

        result, detector = wait_for_sync_with_retries(
            factory, threading.Event(), threading.Event(),
            on_retry=lambda _detector, attempt: retries.append(attempt),
        )
        self.assertEqual(result, SyncResult.AUTO)
        self.assertEqual(retries, [1, 2])
        self.assertEqual(detector.result, SyncResult.AUTO)

    def test_timeout_retry_still_allows_f9_manual_sync(self):
        manual = threading.Event()
        calls = []

        class FakeDetector:
            best_score = 0.68
            best_source = "MuMu Window ROI"

            def wait(self, _stop, manual_event, _auto):
                calls.append(True)
                if len(calls) == 1:
                    manual_event.set()
                    return SyncResult.TIMEOUT
                return SyncResult.MANUAL if manual_event.is_set() else SyncResult.TIMEOUT

        result, _detector = wait_for_sync_with_retries(
            FakeDetector, threading.Event(), manual,
        )
        self.assertEqual(result, SyncResult.MANUAL)
        self.assertEqual(len(calls), 2)

    def test_fast_roi_cancels_stale_adb_result(self):
        template = Image.new("RGB", (20, 20), "white")
        wrong = Image.new("RGB", (20, 20), "black")
        captures = 0

        def fast_capture(_roi):
            nonlocal captures
            captures += 1
            if captures <= 3:
                return wrong, "Fast ROI unavailable: switching windows"
            return template, "MuMu Window ROI 1280x720"

        def slow_fallback(_roi):
            time.sleep(0.08)
            return template, "ADB stale fallback"

        detector = SyncDetector(
            lambda: b"", template, ROI(0, 0, 20, 20),
            poll_ms=20, consecutive_matches=2, timeout_seconds=0.5,
            capture_roi=fast_capture, fallback_capture_roi=slow_fallback,
            fallback_grace_ms=40,
        )

        self.assertEqual(detector.wait(threading.Event(), threading.Event()), SyncResult.AUTO)
        self.assertTrue(detector.last_capture_source.startswith("MuMu Window ROI"))

    def test_slow_capture_latency_is_included_in_sync_compensation(self):
        image = Image.new("RGB", (20, 20), "white")

        def slow_capture(_roi):
            time.sleep(0.04)
            return image, "ADB fallback"

        started = time.perf_counter()
        detector = SyncDetector(
            lambda: b"",
            image,
            ROI(0, 0, 20, 20),
            consecutive_matches=1,
            timeout_seconds=1,
            capture_roi=slow_capture,
        )

        self.assertEqual(detector.wait(threading.Event(), threading.Event()), SyncResult.AUTO)
        self.assertLess((detector.detected_at or 0) - started, 0.02)
        self.assertGreaterEqual(detector.confirmation_delay_ms, 35)

    def test_wrong_fast_viewport_is_rescued_by_adb_verification(self):
        template = Image.new("RGB", (20, 20), "white")
        wrong = Image.new("RGB", (20, 20), "black")

        def slow_adb_verification(_roi):
            time.sleep(0.04)
            return template, "ADB verification fallback"

        started = time.perf_counter()
        detector = SyncDetector(
            lambda: (_ for _ in ()).throw(AssertionError("capture_png should not run")),
            template,
            ROI(0, 0, 20, 20),
            poll_ms=20,
            consecutive_matches=2,
            timeout_seconds=1,
            capture_roi=lambda _roi: (wrong, "Fast ROI unavailable: viewport missing"),
            fallback_capture_roi=slow_adb_verification,
            fallback_grace_ms=20,
        )
        result = detector.wait(threading.Event(), threading.Event())
        self.assertEqual(result, SyncResult.AUTO)
        self.assertEqual(detector.last_capture_source, "ADB verification fallback")
        # Anchor is the start of the successful native capture, not its finish.
        self.assertGreaterEqual((detector.detected_at or 0) - started, 0.015)
        self.assertLess((detector.detected_at or 0) - started, 0.05)
        self.assertGreaterEqual(detector.confirmation_delay_ms, 35)

    def test_adb_fallback_rechecks_without_old_600ms_gap(self):
        template = Image.new("RGB", (20, 20), "white")
        wrong = Image.new("RGB", (20, 20), "black")
        captures = []

        def fallback(_roi):
            captures.append(time.perf_counter())
            return (wrong if len(captures) == 1 else template), "ADB Raw verification fallback"

        detector = SyncDetector(
            lambda: b"",
            template,
            ROI(0, 0, 20, 20),
            poll_ms=20,
            consecutive_matches=2,
            timeout_seconds=1,
            capture_roi=lambda _roi: (wrong, "Fast ROI unavailable: viewport missing"),
            fallback_capture_roi=fallback,
            fallback_grace_ms=20,
        )

        self.assertEqual(detector.wait(threading.Event(), threading.Event()), SyncResult.AUTO)
        self.assertGreaterEqual(len(captures), 2)
        self.assertLess(captures[1] - captures[0], 0.25)

    def test_decode_error(self):
        with self.assertRaises(SyncError):
            decode_png(b"not png")

    def test_history_is_limited_to_twenty(self):
        image = Image.new("RGB", (20, 20), "white")
        detector = SyncDetector(lambda: png_bytes(image), image, ROI(0, 0, 20, 20))
        for _ in range(25):
            detector.check_once()
        self.assertEqual(len(detector.history), 20)

    def test_resolution_mismatch_is_explicit(self):
        image = Image.new("RGB", (100, 100), "white")
        detector = SyncDetector(
            lambda: png_bytes(image), image, ROI(0, 0, 20, 20), expected_size=(1280, 720)
        )
        with self.assertRaisesRegex(SyncError, "Resolution ไม่ตรง"):
            detector.check_once()

    def test_structure_score_rejects_same_brightness_wrong_shape(self):
        template = Image.new("L", (32, 32), 180)
        for x in (11, 19):
            for y in range(6, 27):
                template.putpixel((x, y), 30)
        wrong = Image.new("L", (32, 32), 180)
        for y in (11, 19):
            for x in range(6, 27):
                wrong.putpixel((x, y), 30)
        details = image_similarity_details(wrong, template)
        self.assertLess(details["structure"], 0.72)

    def test_result_requires_xp_match_and_pause_absent(self):
        xp_template = Image.new("RGB", (20, 20), "white")
        pause_template = Image.new("RGB", (20, 20), "black")
        with_pause = Image.new("RGB", (60, 20), "white")
        with_pause.paste(pause_template, (40, 0))
        detector = ResultDetector(
            lambda: png_bytes(with_pause), xp_template, ROI(0, 0, 20, 20),
            pause_template=pause_template, pause_roi=ROI(40, 0, 20, 20),
            threshold=0.8, consecutive_matches=1, min_gameplay_seconds=0,
        )
        xp_score, pause_score, confirmed = detector.check_once()
        self.assertGreater(xp_score, 0.99)
        self.assertGreater(pause_score, 0.99)
        self.assertFalse(confirmed)

        without_pause = Image.new("RGB", (60, 20), "white")
        detector.capture_png = lambda: png_bytes(without_pause)
        _xp_score, pause_score, confirmed = detector.check_once()
        self.assertLess(pause_score, 0.05)
        self.assertTrue(confirmed)


if __name__ == "__main__":
    unittest.main()
