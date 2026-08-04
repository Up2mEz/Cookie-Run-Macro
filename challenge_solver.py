from __future__ import annotations

import random
import statistics
import time
from itertools import combinations
from dataclasses import dataclass
from threading import Event
from typing import Callable

from PIL import Image, ImageChops, ImageStat


REFERENCE_SIZE = (1280, 720)
# Interior areas of the 3x2 card grid at the app's validated 1280x720 capture
# resolution.  Keeping the dark card border outside the crop makes comparison
# depend on the character pose, not on tiny border alignment differences.
CARD_INTERIORS = (
    (370, 190, 505, 405),
    (563, 190, 698, 405),
    (757, 190, 892, 405),
    (370, 445, 505, 660),
    (563, 445, 698, 660),
    (757, 445, 892, 660),
)
CARD_CENTERS = tuple(((left + right) // 2, (top + bottom) // 2) for left, top, right, bottom in CARD_INTERIORS)
ROUND_MARKER_ROI = (395, 112, 675, 170)
SIX_CARD_MIN_MARGIN = 1.8
FIVE_CARD_MIN_SCORE = 8.0
FIVE_CARD_MIN_MARGIN = 1.0


class ChallengeSolveError(RuntimeError):
    pass


@dataclass(frozen=True)
class CardGridAnalysis:
    kind: str
    source_size: tuple[int, int]
    present_slots: tuple[int, ...]
    target_slots: tuple[int, ...]
    scores: tuple[float, ...]
    confidence_margin: float
    confident: bool
    vector: tuple[int, ...]
    round_marker: tuple[int, ...]

    @property
    def target_points(self) -> tuple[tuple[int, int], ...]:
        width, height = self.source_size
        return tuple(
            (
                round(CARD_CENTERS[index][0] * width / REFERENCE_SIZE[0]),
                round(CARD_CENTERS[index][1] * height / REFERENCE_SIZE[1]),
            )
            for index in self.target_slots
        )

    @property
    def target_score_floor(self) -> float:
        score_by_slot = dict(zip(self.present_slots, self.scores))
        return min((score_by_slot[slot] for slot in self.target_slots), default=0.0)


@dataclass(frozen=True)
class ChallengeTiming:
    first_click_delay_ms: int = 1000
    inter_card_min_ms: int = 500
    inter_card_max_ms: int = 900
    next_round_min_ms: int = 1800
    next_round_max_ms: int = 2200
    same_slot_cooldown_ms: int = 1800
    transition_timeout_ms: int = 6500
    consensus_gap_ms: int = 250


def _scaled(image: Image.Image) -> Image.Image:
    converted = image.convert("RGB")
    if converted.size == REFERENCE_SIZE:
        return converted
    return converted.resize(REFERENCE_SIZE, Image.Resampling.BILINEAR)


def _card_background(crop: Image.Image) -> tuple[int, int, int]:
    samples = [
        crop.getpixel((5, 5)), crop.getpixel((crop.width - 6, 5)),
        crop.getpixel((5, crop.height - 6)), crop.getpixel((crop.width - 6, crop.height - 6)),
    ]
    return tuple(int(statistics.median(sample[channel] for sample in samples)) for channel in range(3))


def _is_card_interior(crop: Image.Image) -> bool:
    background = _card_background(crop)
    red, green, blue = background
    if red < 235 or red - green < 8 or green - blue < 12:
        return False
    sample = crop.resize((32, 48), Image.Resampling.BILINEAR)
    close = 0
    for pixel in sample.getdata():
        if sum(abs(int(pixel[channel]) - background[channel]) for channel in range(3)) < 45:
            close += 1
    return close / (sample.width * sample.height) >= 0.60


def _normalized_card(crop: Image.Image) -> Image.Image:
    background = _card_background(crop)
    resized = crop.resize((64, 96), Image.Resampling.BILINEAR)
    pixels: list[tuple[int, int, int]] = []
    for pixel in resized.getdata():
        distance = sum(abs(int(pixel[channel]) - background[channel]) for channel in range(3))
        pixels.append((255, 255, 255) if distance < 42 else tuple(int(value) for value in pixel))
    normalized = Image.new("RGB", resized.size)
    normalized.putdata(pixels)
    return normalized


def _image_distance(first: Image.Image, second: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(first, second)).mean) / 3


def _grid_vector(cards: list[Image.Image]) -> tuple[int, ...]:
    values: list[int] = []
    for card in cards:
        values.extend(card.convert("L").resize((12, 18), Image.Resampling.BILINEAR).getdata())
    return tuple(values)


def grid_change_score(first: CardGridAnalysis, second: CardGridAnalysis) -> float:
    if first.kind != second.kind or len(first.vector) != len(second.vector):
        return 255.0
    card_score = (
        sum(abs(a - b) for a, b in zip(first.vector, second.vector)) / len(first.vector)
        if first.vector else 0.0
    )
    marker_score = (
        sum(abs(a - b) for a, b in zip(first.round_marker, second.round_marker)) / len(first.round_marker)
        if first.round_marker and len(first.round_marker) == len(second.round_marker) else 0.0
    )
    return max(card_score, marker_score)


def round_marker_change_score(first: CardGridAnalysis, second: CardGridAnalysis) -> float:
    if not first.round_marker or len(first.round_marker) != len(second.round_marker):
        return 255.0
    return sum(abs(a - b) for a, b in zip(first.round_marker, second.round_marker)) / len(first.round_marker)


def _card_grid_confident(kind: str, selected_floor: float, margin: float) -> bool:
    if kind == "six_cards":
        return margin >= SIX_CARD_MIN_MARGIN
    return selected_floor >= FIVE_CARD_MIN_SCORE and margin >= FIVE_CARD_MIN_MARGIN


def _six_card_clusters(cards: list[Image.Image]) -> tuple[tuple[int, int], float]:
    distances = {
        (first, second): _image_distance(cards[first], cards[second])
        for first, second in combinations(range(6), 2)
    }

    def distance(first: int, second: int) -> float:
        return distances[tuple(sorted((first, second)))]

    candidates: list[tuple[float, tuple[int, int]]] = []
    for pair in combinations(range(6), 2):
        majority = tuple(index for index in range(6) if index not in pair)
        pair_within = distance(*pair)
        majority_within = statistics.median(
            distance(first, second) for first, second in combinations(majority, 2)
        )
        cross_group = statistics.median(
            distance(target, normal) for target in pair for normal in majority
        )
        separation = cross_group - max(pair_within, majority_within)
        candidates.append((separation, pair))
    separation, pair = max(candidates, key=lambda item: (item[0], tuple(-slot for slot in item[1])))
    return pair, separation


def analyze_card_grid(image: Image.Image) -> CardGridAnalysis | None:
    source_size = image.size
    screenshot = _scaled(image)
    crops = [screenshot.crop(rect) for rect in CARD_INTERIORS]
    present_slots = tuple(index for index, crop in enumerate(crops) if _is_card_interior(crop))
    if len(present_slots) == 6:
        # Both "sliding" and "jumping" titles can use this 6-card / 2-target
        # layout. Real animated characters can yield only ~2 margin even when
        # the same two slots remain stable. The relaxed per-frame gate is still
        # protected by two-frame target consensus before every tap.
        kind, target_count = "six_cards", 2
    elif present_slots == (0, 1, 3, 4, 5):
        kind, target_count = "five_cards", 1
    else:
        return None
    normalized_cards = [_normalized_card(crops[index]) for index in present_slots]
    score_by_slot: dict[int, float] = {}
    for position, slot in enumerate(present_slots):
        distances = [
            _image_distance(normalized_cards[position], other)
            for other_position, other in enumerate(normalized_cards)
            if other_position != position
        ]
        score_by_slot[slot] = float(statistics.median(distances))
    if kind == "six_cards":
        pair, margin = _six_card_clusters(normalized_cards)
        targets = tuple(present_slots[position] for position in pair)
        selected_floor = min(score_by_slot[slot] for slot in targets)
    else:
        ranked = sorted(present_slots, key=lambda slot: (score_by_slot[slot], -slot), reverse=True)
        targets = tuple(sorted(ranked[:target_count]))
        selected_floor = min(score_by_slot[slot] for slot in targets)
        next_score = score_by_slot[ranked[target_count]]
        margin = selected_floor - next_score
    return CardGridAnalysis(
        kind=kind,
        source_size=source_size,
        present_slots=present_slots,
        target_slots=targets,
        scores=tuple(round(score_by_slot[slot], 4) for slot in present_slots),
        confidence_margin=round(margin, 4),
        confident=_card_grid_confident(kind, selected_floor, margin),
        vector=_grid_vector(normalized_cards),
        round_marker=tuple(
            screenshot.crop(ROUND_MARKER_ROI).convert("L").resize((32, 8), Image.Resampling.BILINEAR).getdata()
        ),
    )


class CardChallengeSolver:
    """Solve a confirmed 3-round card challenge with guarded sequential taps."""

    def __init__(
        self,
        capture_image: Callable[[], Image.Image],
        tap: Callable[[int, int], None],
        stop_event: Event,
        *,
        timing: ChallengeTiming | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.perf_counter,
        wait: Callable[[float], bool] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.capture_image = capture_image
        self.tap = tap
        self.stop_event = stop_event
        self.timing = timing or ChallengeTiming()
        self.rng = rng or random.Random()
        self.clock = clock
        self.wait = wait or stop_event.wait
        self.on_status = on_status
        self._last_tap_by_slot: dict[int, float] = {}

    def _status(self, message: str) -> None:
        if self.on_status:
            self.on_status(message)

    def _wait_ms(self, milliseconds: int) -> bool:
        return bool(self.wait(max(0, milliseconds) / 1000))

    def _capture_confident(
        self,
        expected_kind: str | None = None,
        seed: CardGridAnalysis | None = None,
    ) -> CardGridAnalysis | None:
        candidate_key = (seed.kind, seed.target_slots) if seed is not None else None
        stable_observations = 1 if seed is not None else 0
        confident_anchor = seed if seed is not None and seed.confident else None
        # At least one observation must pass the 1.8 margin gate, while three
        # total observations must keep the exact same target slots. This avoids
        # animation dips without accepting a target set that ever changes.
        for attempt in range(4):
            analysis = analyze_card_grid(self.capture_image())
            if analysis is None or (expected_kind and analysis.kind != expected_kind):
                return None
            key = (analysis.kind, analysis.target_slots)
            if candidate_key is None:
                candidate_key = key
            elif key != candidate_key:
                return None
            stable_observations += 1
            if analysis.confident:
                confident_anchor = analysis
            if stable_observations >= 3 and confident_anchor is not None:
                return confident_anchor
            if attempt < 3 and self._wait_ms(self.timing.consensus_gap_ms):
                return None
        return None

    def _tap_targets(self, analysis: CardGridAnalysis) -> None:
        slots = list(analysis.target_slots)
        self.rng.shuffle(slots)
        for index, slot in enumerate(slots):
            previous = self._last_tap_by_slot.get(slot)
            if previous is not None:
                remaining = self.timing.same_slot_cooldown_ms / 1000 - (self.clock() - previous)
                if remaining > 0 and self.wait(remaining):
                    raise ChallengeSolveError("ผู้ใช้หยุดระหว่างรอป้องกันการคลิกช่องเดิมซ้ำ")
            target_index = analysis.target_slots.index(slot)
            x, y = analysis.target_points[target_index]
            self.tap(x, y)
            self._last_tap_by_slot[slot] = self.clock()
            if index + 1 < len(slots):
                delay = self.rng.randint(self.timing.inter_card_min_ms, self.timing.inter_card_max_ms)
                if self._wait_ms(delay):
                    raise ChallengeSolveError("ผู้ใช้หยุดระหว่างหน่วงการคลิกการ์ด")

    def _await_confirmed_targets(self, expected: CardGridAnalysis, timeout_ms: int = 3500) -> CardGridAnalysis | None:
        deadline = self.clock() + timeout_ms / 1000
        while self.clock() < deadline and not self.stop_event.is_set():
            confirmed = self._capture_confident(expected.kind, seed=expected)
            if confirmed is not None and confirmed.target_slots == expected.target_slots:
                return confirmed
            if self._wait_ms(250):
                break
        return None

    def solve_three_rounds(self, initial: CardGridAnalysis) -> int:
        if not initial.confident:
            raise ChallengeSolveError("พบเกมการ์ดแต่คะแนนแยกคำตอบยังไม่มั่นใจ จึงไม่คลิก")
        current = initial
        if self._wait_ms(self.timing.first_click_delay_ms):
            return 0
        rounds_solved = 0
        while rounds_solved < 3 and not self.stop_event.is_set():
            confirmed = self._await_confirmed_targets(current)
            if confirmed is None:
                raise ChallengeSolveError("คำตอบเกมการ์ดไม่ตรงกัน 2 เฟรม จึงหยุดก่อนคลิก")
            self._status(f"เกมการ์ดรอบ {rounds_solved + 1}/3 • จะคลิก {len(confirmed.target_slots)} ใบแบบเรียงลำดับ")
            self._tap_targets(confirmed)
            rounds_solved += 1
            if rounds_solved >= 3:
                break
            round_delay = self.rng.randint(self.timing.next_round_min_ms, self.timing.next_round_max_ms)
            if self._wait_ms(round_delay):
                break
            deadline = self.clock() + self.timing.transition_timeout_ms / 1000
            next_round: CardGridAnalysis | None = None
            while self.clock() < deadline and not self.stop_event.is_set():
                candidate = self._capture_confident(current.kind)
                round_changed = (
                    candidate is not None
                    and (
                        candidate.target_slots != current.target_slots
                        or round_marker_change_score(current, candidate) >= 0.25
                    )
                )
                if round_changed:
                    next_round = candidate
                    break
                # Same screen means the game has not accepted both answers yet.
                # Never repeat a click here; only observe until transition/timeout.
                if self._wait_ms(250):
                    break
            if next_round is None:
                raise ChallengeSolveError("หน้าการ์ดไม่เปลี่ยนหลังคลิก จึงไม่คลิกซ้ำและหยุดอย่างปลอดภัย")
            current = next_round
        return rounds_solved
