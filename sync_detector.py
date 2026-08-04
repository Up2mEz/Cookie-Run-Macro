from __future__ import annotations

import io
import statistics
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError


class SyncError(RuntimeError):
    pass


class SyncResult(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    TIMEOUT = "timeout"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ROI:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_dict(cls, data: dict) -> "ROI":
        try:
            return cls(*(int(data[key]) for key in ("x", "y", "width", "height")))
        except (KeyError, TypeError, ValueError) as exc:
            raise SyncError("ROI ไม่ครบหรือไม่ใช่ตัวเลข") from exc

    def validate(self, image_size: tuple[int, int] | None = None) -> None:
        if self.x < 0 or self.y < 0:
            raise SyncError("ROI ต้องไม่เริ่มนอกภาพ")
        if self.width < 12 or self.height < 12:
            raise SyncError("ROI ต้องมีขนาดอย่างน้อย 12×12 px")
        if image_size and (self.x + self.width > image_size[0] or self.y + self.height > image_size[1]):
            raise SyncError("ROI เกินขอบภาพ")

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


def image_similarity(current: Image.Image, template: Image.Image) -> float:
    """คะแนนรวมแบบเบา: สีเทา 35% + correlation 25% + เส้นเด่น 40%.

    ค่าเดิมดูเฉพาะความสว่างเฉลี่ย ทำให้พื้นหลังคนละภาพยังได้ราว 0.72 และ
    threshold ต่ำอาจ false-positive ได้ การเพิ่ม normalized correlation และน้ำหนัก
    เฉพาะเส้นเด่นช่วยยืนยันรูปทรงของ Pause โดยยังใช้ Pillow เท่านั้น.
    """
    return image_similarity_details(current, template)["combined"]


def equalize_comparison_size(current: Image.Image, template: Image.Image) -> tuple[Image.Image, Image.Image]:
    """ทำให้ภาพปัจจุบันมีขนาดตรงกับ Template ก่อนคำนวณทุกคะแนน."""
    if current.width <= 0 or current.height <= 0 or template.width <= 0 or template.height <= 0:
        raise SyncError("ภาพสำหรับเปรียบเทียบมีขนาดไม่ถูกต้อง")
    normalized_template = template.convert("RGB")
    normalized_current = current.convert("RGB")
    if normalized_current.size != normalized_template.size:
        normalized_current = normalized_current.resize(normalized_template.size, Image.Resampling.LANCZOS)
    return normalized_current, normalized_template


def _normalized_correlation(current: Image.Image, template: Image.Image) -> float:
    current_pixels = list(ImageOps.autocontrast(current.convert("L").resize((32, 32))).getdata())
    template_pixels = list(ImageOps.autocontrast(template.convert("L").resize((32, 32))).getdata())
    current_mean = sum(current_pixels) / len(current_pixels)
    template_mean = sum(template_pixels) / len(template_pixels)
    current_centered = [value - current_mean for value in current_pixels]
    template_centered = [value - template_mean for value in template_pixels]
    numerator = sum(left * right for left, right in zip(current_centered, template_centered))
    denominator = (
        sum(value * value for value in current_centered)
        * sum(value * value for value in template_centered)
    ) ** 0.5
    if denominator == 0:
        return 1.0 if current_pixels == template_pixels else 0.0
    return max(0.0, min(1.0, (numerator / denominator + 1.0) / 2.0))


def _template_feature_similarity(current: Image.Image, template: Image.Image) -> float:
    """เทียบเฉพาะตำแหน่งเส้น/รูปทรงเด่นของ template เพื่อลดผลจากพื้นหลังด่าน."""
    current_small = ImageOps.autocontrast(current.convert("L").resize((32, 32)))
    template_small = ImageOps.autocontrast(template.convert("L").resize((32, 32)))
    template_edges = template_small.filter(ImageFilter.FIND_EDGES)
    weights = [max(8, value) for value in template_edges.getdata()]
    differences = [
        abs(left - right)
        for left, right in zip(current_small.getdata(), template_small.getdata())
    ]
    weighted_difference = sum(value * weight for value, weight in zip(differences, weights))
    return max(0.0, min(1.0, 1.0 - weighted_difference / (255 * sum(weights))))


def image_similarity_details(current: Image.Image, template: Image.Image) -> dict[str, float]:
    current, template = equalize_comparison_size(current, template)
    current_small = current.convert("L").resize((64, 64))
    template_small = template.convert("L").resize((64, 64))
    difference = ImageChops.difference(current_small, template_small)
    mean_difference = ImageStat.Stat(difference).mean[0]
    luminance = max(0.0, min(1.0, 1.0 - mean_difference / 255.0))
    structure = _normalized_correlation(current, template)
    feature = _template_feature_similarity(current, template)
    combined = max(0.0, min(1.0, luminance * 0.35 + structure * 0.25 + feature * 0.40))
    return {"combined": combined, "luminance": luminance, "structure": structure, "feature": feature}


def decode_png(png_data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(png_data)) as source:
            source.load()
            return source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SyncError("PNG decode ไม่สำเร็จ") from exc


def crop_roi(image: Image.Image, roi: ROI | dict) -> Image.Image:
    parsed = roi if isinstance(roi, ROI) else ROI.from_dict(roi)
    parsed.validate(image.size)
    return image.crop(parsed.box)


class ConsecutiveMatcher:
    def __init__(self, threshold: float, required: int) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold ต้องอยู่ระหว่าง 0–1")
        if required < 1:
            raise ValueError("required ต้องมากกว่า 0")
        self.threshold = threshold
        self.required = required
        self.count = 0

    def add(self, similarity: float) -> bool:
        self.count = self.count + 1 if similarity >= self.threshold else 0
        return self.count >= self.required


def adb_fallback_qualified(
    details: dict[str, float],
    configured_threshold: float,
    feature_threshold: float,
    structure_threshold: float,
) -> bool:
    """ใช้เกณฑ์เดียวกับ Fast ROI; ไม่มี threshold floor ซ่อนจากค่าที่ผู้ใช้ตั้ง."""
    return (
        details["combined"] >= float(configured_threshold)
        and details["feature"] >= float(feature_threshold)
        and details["structure"] >= float(structure_threshold)
    )


def derived_shape_thresholds(threshold: float) -> tuple[float, float]:
    """แปลง Threshold ช่องเดียวเป็นเกณฑ์เส้น/โครงที่สัมพันธ์กันและคาดเดาได้.

    ที่ค่าแนะนำ 0.82 จะได้ feature 0.76 / structure 0.72 เท่าพฤติกรรมเดิม
    แต่เมื่อผู้ใช้ปรับค่า เกณฑ์ย่อยจะขยับตามจริงแทนการค้างค่าซ่อนอยู่.
    """
    value = max(0.5, min(1.0, float(threshold)))
    return max(0.0, value - 0.06), max(0.0, value - 0.10)


def stable_sync_target(
    detected_at: float,
    delay_ms: int | float,
    *,
    now: float | None = None,
) -> tuple[float, float]:
    """คืนจุด Sync คงที่และเวลาที่ต้องรอจากเวลาปัจจุบัน.

    จุด Sync ยึดกับเฟรมแรกที่พบ Pause เสมอ ไม่ยึดกับเวลาที่ Detector
    ประมวลผลเสร็จ จึงไม่บวก capture latency ซ้ำและใช้ความหมายเดียวกันทั้ง
    ตอนอัดกับตอนเล่น Pattern.
    """
    delay_seconds = max(0.0, float(delay_ms)) / 1000.0
    target = float(detected_at) + delay_seconds
    current = time.perf_counter() if now is None else float(now)
    return target, max(0.0, target - current)


def wait_for_sync_with_retries(
    detector_factory: Callable[[], "SyncDetector"],
    stop_event: Event,
    manual_sync_event: Event,
    auto_sync_event: Event | None = None,
    *,
    retry_on_timeout: bool = True,
    on_retry: Callable[["SyncDetector", int], None] | None = None,
) -> tuple[SyncResult, "SyncDetector"]:
    """รอ Sync และ re-arm อัตโนมัติเมื่อหมดรอบตรวจ.

    Timeout เป็นเพียงขอบเขตของ detector หนึ่งรอบ ไม่ควรทำให้ playback แบบ
    loop ค้างรอ F9 ตลอดไป. F8/F9 ยังขัดจังหวะได้ทุกครั้ง.
    """
    attempts = 0
    while True:
        detector = detector_factory()
        result = detector.wait(stop_event, manual_sync_event, auto_sync_event)
        if result != SyncResult.TIMEOUT or not retry_on_timeout:
            return result, detector
        attempts += 1
        if on_retry:
            on_retry(detector, attempts)


def summarize_sync_samples(
    samples: list[dict[str, object]],
    threshold: float,
    feature_threshold: float,
    structure_threshold: float,
) -> dict[str, object]:
    """สรุปหลายเฟรมเพื่อไม่ตัดสิน latency/accuracy จาก screenshot เดียว."""
    if not samples:
        raise ValueError("ต้องมีตัวอย่าง Auto Sync อย่างน้อย 1 เฟรม")
    scores = [float(sample["details"]["combined"]) for sample in samples]  # type: ignore[index]
    structures = [float(sample["details"]["structure"]) for sample in samples]  # type: ignore[index]
    features = [float(sample["details"]["feature"]) for sample in samples]  # type: ignore[index]
    capture_times = [float(sample["capture_ms"]) for sample in samples]
    passed = sum(
        score >= threshold and structure >= structure_threshold and feature >= feature_threshold
        for score, structure, feature in zip(scores, structures, features)
    )
    sources: dict[str, int] = {}
    for sample in samples:
        source = str(sample.get("source", "ไม่ทราบ"))
        sources[source] = sources.get(source, 0) + 1
    return {
        "count": len(samples),
        "passed": passed,
        "combined_min": min(scores),
        "combined_avg": statistics.fmean(scores),
        "combined_max": max(scores),
        "structure_min": min(structures),
        "feature_min": min(features),
        "capture_median_ms": statistics.median(capture_times),
        "capture_max_ms": max(capture_times),
        "sources": sources,
    }


def is_real_fast_roi_source(source: str) -> bool:
    """ภาพทดสอบต้องมาจากหน้าต่าง MuMu จริง ไม่ใช่ sentinel/ADB fallback."""
    return str(source).startswith("MuMu Window ROI")


class SyncDetector:
    def __init__(
        self,
        capture_png: Callable[[], bytes],
        template: Image.Image | str | Path,
        roi: ROI | dict,
        *,
        poll_ms: int = 20,
        threshold: float = 0.82,
        consecutive_matches: int = 2,
        timeout_seconds: float = 25,
        expected_size: tuple[int, int] | None = None,
        on_similarity: Callable[[float, list[float]], None] | None = None,
        capture_roi: Callable[[ROI], Image.Image | tuple[Image.Image, str]] | None = None,
        fallback_capture_roi: Callable[[ROI], Image.Image | tuple[Image.Image, str]] | None = None,
        fallback_grace_ms: int = 80,
        feature_threshold: float | None = None,
        structure_threshold: float | None = None,
    ) -> None:
        self.capture_png = capture_png
        self.roi = roi if isinstance(roi, ROI) else ROI.from_dict(roi)
        self.poll_ms = max(20, min(1000, int(poll_ms)))
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        configured_threshold = float(threshold)
        self.matcher = ConsecutiveMatcher(configured_threshold, int(consecutive_matches))
        self.on_similarity = on_similarity
        self.capture_roi = capture_roi
        self.fallback_capture_roi = fallback_capture_roi
        # ให้ Fast ROI มีช่วงนำสั้นคงที่เพื่อจบได้โดยไม่แย่งทรัพยากร จากนั้น
        # native verification ต้องเริ่มเสมอ แม้ Fast ROI เปิดได้แต่ Match ไม่เจอ.
        self.fallback_grace_ms = max(0, min(5000, int(fallback_grace_ms)))
        self.expected_size = expected_size
        derived_feature, derived_structure = derived_shape_thresholds(configured_threshold)
        self.feature_threshold = derived_feature if feature_threshold is None else max(0.0, min(1.0, float(feature_threshold)))
        self.structure_threshold = derived_structure if structure_threshold is None else max(0.0, min(1.0, float(structure_threshold)))
        self.history: deque[float] = deque(maxlen=20)
        self.last_details = {"combined": 0.0, "luminance": 0.0, "structure": 0.0, "feature": 0.0}
        self.last_capture_ms = 0.0
        self.last_capture_source = "ADB PNG"
        self.last_capture_at: float | None = None
        self.detected_at: float | None = None
        self.confirmation_delay_ms = 0.0
        self.best_score = 0.0
        self.best_details = dict(self.last_details)
        self.best_source = "—"
        if isinstance(template, Image.Image):
            self.template = template.copy()
        else:
            try:
                with Image.open(template) as image:
                    image.load()
                    self.template = image.convert("RGB")
            except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
                raise SyncError(f"เปิด Pause template ไม่สำเร็จ: {exc}") from exc

    def check_once(self) -> float:
        capture_started = time.perf_counter()
        if self.capture_roi:
            captured = self.capture_roi(self.roi)
            if isinstance(captured, tuple):
                current_roi, self.last_capture_source = captured
            else:
                current_roi, self.last_capture_source = captured, "Fast ROI"
        else:
            screenshot = decode_png(self.capture_png())
            if self.expected_size and screenshot.size != self.expected_size:
                raise SyncError(
                    f"Resolution ไม่ตรงกับตอนสร้าง template: คาด {self.expected_size[0]}x{self.expected_size[1]} "
                    f"แต่ภาพปัจจุบันเป็น {screenshot.width}x{screenshot.height}"
                )
            current_roi = crop_roi(screenshot, self.roi)
            self.last_capture_source = "ADB PNG"
        capture_finished = time.perf_counter()
        # ADB can spend hundreds of milliseconds encoding/transferring a screenshot.
        # The frame represents the screen near the start of that operation, so using
        # the finish time would shift every playback event late by the capture cost.
        self.last_capture_at = capture_started
        self.last_capture_ms = (capture_finished - capture_started) * 1000
        if self.last_capture_source.startswith("Fast ROI unavailable"):
            # ภาพนี้เป็น sentinel เพื่อบอกว่า MuMu ไม่ได้อยู่หน้า ไม่ใช่ภาพเกมจริง
            # ห้ามนำไปคิดคะแนน, best score, history หรือแสดงใน UI.
            self.last_details = {"combined": 0.0, "luminance": 0.0, "structure": 0.0, "feature": 0.0}
            return 0.0
        self.last_details = image_similarity_details(current_roi, self.template)
        similarity = self.last_details["combined"]
        if similarity > self.best_score:
            self.best_score = similarity
            self.best_details = dict(self.last_details)
            self.best_source = self.last_capture_source
        self.history.append(similarity)
        if self.on_similarity:
            self.on_similarity(similarity, list(self.history))
        return similarity

    def wait(self, stop_event: Event, manual_sync_event: Event, auto_sync_event: Event | None = None) -> SyncResult:
        deadline = time.perf_counter() + self.timeout_seconds
        first_matching_at: float | None = None
        self.detected_at = None
        self.confirmation_delay_ms = 0.0
        fallback_cancel = Event()
        fallback_matched = Event()
        fallback_result: list[tuple[float, dict[str, float], str, float]] = []
        fallback_thread: Thread | None = None

        def watch_adb_fallback() -> None:
            if fallback_cancel.wait(self.fallback_grace_ms / 1000.0):
                return
            while not fallback_cancel.is_set() and not stop_event.is_set():
                started = time.perf_counter()
                try:
                    captured = self.fallback_capture_roi(self.roi) if self.fallback_capture_roi else None
                    if captured is None:
                        return
                    if isinstance(captured, tuple):
                        image, source = captured
                    else:
                        image, source = captured, "ADB verification"
                    finished = time.perf_counter()
                    details = image_similarity_details(image, self.template)
                    # เก็บหลักฐานของ native path แม้ยังไม่ผ่าน เพื่อให้ข้อความ
                    # retry บอกคะแนนดีที่สุดจากเส้นทางที่ช่วยกู้จริงได้.
                    if details["combined"] > self.best_score:
                        self.best_score = details["combined"]
                        self.best_details = dict(details)
                        self.best_source = source
                    strict_match = adb_fallback_qualified(
                        details,
                        self.matcher.threshold,
                        self.feature_threshold,
                        self.structure_threshold,
                    )
                    if strict_match:
                        fallback_result.append((started, details, source, (finished - started) * 1000))
                        fallback_matched.set()
                        return
                except Exception:
                    pass
                # Capture ADB เองใช้เวลามากกว่ารอบ Fast ROI อยู่แล้ว เริ่มภาพถัดไป
                # ทันทีเพื่อไม่ทิ้ง blind gap ที่อาจพลาด Pause ช่วงสั้น.

        if manual_sync_event.is_set():
            return SyncResult.MANUAL
        if self.fallback_capture_roi is not None:
            # แข่งสองทางเฉพาะช่วง WAITING_FOR_AUTO_SYNC หลัง Fast ROI ได้ช่วงนำ
            # คงที่สั้น ๆ; ADB native ช่วยกู้เมื่อ Window ROI เปิดได้แต่ได้
            # พิกัด/เฟรมผิด โดยไม่ต้องรอสถานะ Fast path ล้ม.
            fallback_thread = Thread(target=watch_adb_fallback, daemon=True)
            fallback_thread.start()

        try:
            while not stop_event.is_set():
                iteration_started = time.perf_counter()
                if manual_sync_event.is_set():
                    return SyncResult.MANUAL
                if fallback_matched.is_set() and fallback_result:
                    captured_at, details, source, capture_ms = fallback_result[0]
                    self.last_details = details
                    self.last_capture_source = source
                    self.last_capture_ms = capture_ms
                    self.last_capture_at = captured_at
                    self.detected_at = captured_at
                    self.confirmation_delay_ms = capture_ms
                    self.best_score = max(self.best_score, details["combined"])
                    self.best_details = dict(details)
                    self.best_source = source
                    if self.on_similarity:
                        self.on_similarity(details["combined"], list(self.history))
                    if auto_sync_event:
                        auto_sync_event.set()
                    return SyncResult.AUTO
                if time.perf_counter() >= deadline:
                    return SyncResult.TIMEOUT
                similarity = self.check_once()
                fast_unavailable = self.last_capture_source.startswith("Fast ROI unavailable")
                shape_ok = (
                    self.last_details["feature"] >= self.feature_threshold
                    and self.last_details["structure"] >= self.structure_threshold
                )
                qualified = not fast_unavailable and shape_ok and similarity >= self.matcher.threshold
                if qualified and self.matcher.count == 0:
                    first_matching_at = self.last_capture_at or time.perf_counter()
                if not qualified:
                    first_matching_at = None
                if self.matcher.add(similarity if qualified else 0.0):
                    confirmed_at = time.perf_counter()
                    self.detected_at = first_matching_at or confirmed_at
                    self.confirmation_delay_ms = max(0.0, (confirmed_at - self.detected_at) * 1000)
                    if auto_sync_event:
                        auto_sync_event.set()
                    return SyncResult.AUTO
                remaining = max(0.0, deadline - time.perf_counter())
                # นับ poll แบบ start-to-start ไม่ใช่ capture + poll และเมื่อเฟรมแรก
                # ผ่านให้ยืนยันเฟรมที่สองแบบ burst 5 ms เพื่อลด confirmation delay.
                target_interval = 0.005 if self.matcher.count else self.poll_ms / 1000
                elapsed = time.perf_counter() - iteration_started
                wait_for = min(max(0.0, target_interval - elapsed), remaining)
                if stop_event.wait(wait_for):
                    return SyncResult.STOPPED
            return SyncResult.STOPPED
        finally:
            fallback_cancel.set()


class ResultDetector:
    """ตรวจหน้า Result จาก XP และยืนยันว่าปุ่ม Pause หายแล้ว.

    ใช้ screenshot เดียวต่อรอบตรวจ, crop เพียงสอง ROI และต้องผ่านหลายเฟรม
    ต่อเนื่อง จึงเบาและลด false-positive จากฉาก/คุกกี้/ข้อความที่เปลี่ยนได้.
    """

    def __init__(
        self,
        capture_png: Callable[[], bytes],
        xp_template: Image.Image | str | Path,
        xp_roi: ROI | dict,
        *,
        pause_template: Image.Image | str | Path | None = None,
        pause_roi: ROI | dict | None = None,
        poll_ms: int = 700,
        threshold: float = 0.86,
        consecutive_matches: int = 3,
        min_gameplay_seconds: float = 15,
        timeout_seconds: float = 45,
        expected_duration: float = 0,
        pause_absent_threshold: float = 0.80,
        expected_size: tuple[int, int] | None = None,
        on_similarity: Callable[[float, float | None, int], None] | None = None,
    ) -> None:
        self.capture_png = capture_png
        self.xp_roi = xp_roi if isinstance(xp_roi, ROI) else ROI.from_dict(xp_roi)
        self.pause_roi = pause_roi if isinstance(pause_roi, ROI) else ROI.from_dict(pause_roi) if pause_roi else None
        self.poll_ms = max(400, min(2000, int(poll_ms)))
        self.min_gameplay_seconds = max(0.0, float(min_gameplay_seconds))
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self.expected_duration = max(0.0, float(expected_duration))
        self.pause_absent_threshold = max(0.30, min(0.95, float(pause_absent_threshold)))
        self.expected_size = expected_size
        self.matcher = ConsecutiveMatcher(float(threshold), int(consecutive_matches))
        self.feature_threshold = 0.72
        self.structure_threshold = 0.70
        self.on_similarity = on_similarity
        self.history: deque[float] = deque(maxlen=20)
        self.last_xp_details = {"combined": 0.0, "luminance": 0.0, "structure": 0.0, "feature": 0.0}
        self.xp_template = self._load_template(xp_template, "XP")
        self.pause_template = self._load_template(pause_template, "Pause") if pause_template is not None else None

    @staticmethod
    def _load_template(template: Image.Image | str | Path, label: str) -> Image.Image:
        if isinstance(template, Image.Image):
            return template.copy()
        try:
            with Image.open(template) as image:
                image.load()
                return image.convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
            raise SyncError(f"เปิด {label} template ไม่สำเร็จ: {exc}") from exc

    @staticmethod
    def _cancelled(stop_event: Event, cancel_event: Event | None) -> bool:
        return stop_event.is_set() or bool(cancel_event and cancel_event.is_set())

    def check_once(self) -> tuple[float, float | None, bool]:
        screenshot = decode_png(self.capture_png())
        return self.check_image(screenshot)

    def check_image(self, screenshot: Image.Image) -> tuple[float, float | None, bool]:
        """Check an already captured frame so an end-screen router can reuse it."""
        screenshot = screenshot.convert("RGB")
        if self.expected_size and screenshot.size != self.expected_size:
            raise SyncError(
                f"Resolution ไม่ตรงกับ Result template: คาด {self.expected_size[0]}x{self.expected_size[1]} "
                f"แต่ภาพปัจจุบันเป็น {screenshot.width}x{screenshot.height}"
            )
        self.last_xp_details = image_similarity_details(crop_roi(screenshot, self.xp_roi), self.xp_template)
        xp_score = self.last_xp_details["combined"]
        pause_score: float | None = None
        pause_absent = True
        if self.pause_template is not None and self.pause_roi is not None:
            pause_score = image_similarity(crop_roi(screenshot, self.pause_roi), self.pause_template)
            pause_absent = pause_score < self.pause_absent_threshold
        matched = (
            xp_score >= self.matcher.threshold
            and self.last_xp_details["feature"] >= self.feature_threshold
            and self.last_xp_details["structure"] >= self.structure_threshold
            and pause_absent
        )
        confirmed = self.matcher.add(xp_score if matched else 0.0)
        self.history.append(xp_score)
        if self.on_similarity:
            self.on_similarity(xp_score, pause_score, self.matcher.count)
        return xp_score, pause_score, confirmed

    def wait(self, stop_event: Event, cancel_event: Event | None = None) -> bool:
        started = time.perf_counter()
        deadline = started + max(
            self.min_gameplay_seconds + self.timeout_seconds,
            self.expected_duration + self.timeout_seconds,
        )
        while not self._cancelled(stop_event, cancel_event):
            elapsed = time.perf_counter() - started
            if elapsed >= deadline - started:
                return False
            if elapsed < self.min_gameplay_seconds:
                wait_for = min(0.25, self.min_gameplay_seconds - elapsed)
            else:
                _xp, _pause, confirmed = self.check_once()
                if confirmed:
                    return True
                wait_for = self.poll_ms / 1000
            if stop_event.wait(wait_for):
                return False
            if cancel_event and cancel_event.is_set():
                return False
        return False
