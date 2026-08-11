from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from PIL import Image, UnidentifiedImageError

from sync_detector import crop_roi, image_similarity


def default_popup_cleanup_config() -> dict:
    """Return the safe, optional popup rules used between loop rounds.

    The Send Life rule deliberately contains only the Cancel tap.  Confirm is
    never a valid action for that popup.
    """
    return {
        "enabled": True,
        "poll_ms": 250,
        "timeout_ms": 5000,
        "threshold": 0.82,
        "consecutive_matches": 2,
        "level_up": {
            "template_path": "templates/popup_level_up_title.png",
            "roi": {"x": 470, "y": 22, "width": 350, "height": 100},
            "tap": {"x": 640, "y": 627},
        },
        "send_life": {
            "template_path": "templates/popup_send_life_text.png",
            "roi": {"x": 500, "y": 290, "width": 290, "height": 70},
            "tap": {"x": 480, "y": 452},
        },
    }


def merge_popup_cleanup_config(overrides: dict | None) -> dict:
    config = default_popup_cleanup_config()
    if not isinstance(overrides, dict):
        return config
    for key, value in overrides.items():
        if key in {"level_up", "send_life"} and isinstance(value, dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


@dataclass(frozen=True)
class PopupRule:
    name: str
    template: Image.Image
    roi: dict
    tap: tuple[int, int]
    threshold: float
    consecutive_matches: int


@dataclass(frozen=True)
class PopupCleanupResult:
    actions: tuple[str, ...] = ()
    error: str | None = None


class PopupCleanup:
    """Detect and clear independent optional popups.

    A rule can fire at most once per cleanup window. This is intentional: a
    persistent or animated popup cannot cause repeated taps. Rules are checked
    independently on every frame, so either popup can appear first, alone, or
    at the same time. If both match one frame, only one tap is sent before the
    next frame is captured; this gives the UI time to transition safely.
    """

    def __init__(
        self,
        capture_image: Callable[[], Image.Image | tuple[Image.Image, str]],
        tap: Callable[[int, int], None],
        rules: list[PopupRule],
        *,
        poll_ms: int = 250,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.capture_image = capture_image
        self.tap = tap
        self.rules = list(rules)
        self.poll_seconds = max(0.02, min(2.0, int(poll_ms) / 1000))
        self.on_status = on_status

    @classmethod
    def from_config(
        cls,
        project_dir: str | Path,
        capture_image: Callable[[], Image.Image | tuple[Image.Image, str]],
        tap: Callable[[int, int], None],
        config: dict | None = None,
        *,
        on_status: Callable[[str], None] | None = None,
    ) -> "PopupCleanup":
        merged = merge_popup_cleanup_config(config)
        rules: list[PopupRule] = []
        project = Path(project_dir)
        try:
            threshold = max(0.5, min(1.0, float(merged.get("threshold", 0.82))))
        except (TypeError, ValueError):
            threshold = 0.82
        try:
            consecutive = max(1, min(5, int(merged.get("consecutive_matches", 2))))
        except (TypeError, ValueError):
            consecutive = 2
        for name in ("level_up", "send_life"):
            raw = merged.get(name)
            if not isinstance(raw, dict):
                continue
            template_path = str(raw.get("template_path", "")).strip()
            if not template_path:
                continue
            path = (project / template_path).resolve()
            try:
                path.relative_to(project.resolve())
                with Image.open(path) as image:
                    template = image.convert("RGB")
                tap_data = raw.get("tap") or {}
                rules.append(PopupRule(
                    name=name,
                    template=template,
                    roi=dict(raw.get("roi") or {}),
                    tap=(int(tap_data["x"]), int(tap_data["y"])),
                    threshold=threshold,
                    consecutive_matches=consecutive,
                ))
            except (KeyError, TypeError, ValueError, FileNotFoundError, UnidentifiedImageError, OSError) as exc:
                if on_status:
                    on_status(f"Popup {name}: ปิดการตรวจชั่วคราว ({exc})")
        try:
            poll_ms = int(merged.get("poll_ms", 250))
        except (TypeError, ValueError):
            poll_ms = 250
        return cls(
            capture_image,
            tap,
            rules,
            poll_ms=poll_ms,
            on_status=on_status,
        )

    def _report(self, message: str) -> None:
        if self.on_status:
            self.on_status(message)

    def run(self, stop_event: Event, budget_seconds: float) -> PopupCleanupResult:
        if not self.rules or budget_seconds <= 0:
            return PopupCleanupResult()
        deadline = time.perf_counter() + max(0.0, float(budget_seconds))
        matchers = [rule.consecutive_matches for rule in self.rules]
        counts = [0 for _ in self.rules]
        handled: set[int] = set()
        actions: list[str] = []
        while not stop_event.is_set() and len(handled) < len(self.rules):
            if time.perf_counter() >= deadline:
                break
            try:
                captured = self.capture_image()
                screenshot = captured[0] if isinstance(captured, tuple) else captured
                for index, rule in enumerate(self.rules):
                    if index in handled:
                        continue
                    score = image_similarity(crop_roi(screenshot, rule.roi), rule.template)
                    counts[index] = counts[index] + 1 if score >= rule.threshold else 0
                    if counts[index] >= matchers[index]:
                        self.tap(*rule.tap)
                        actions.append(rule.name)
                        handled.add(index)
                        counts[index] = 0
                        self._report(
                            "Popup cleanup: กด Confirm ของ Level Up แล้ว"
                            if rule.name == "level_up"
                            else "Popup cleanup: กด Cancel ของ Send Life แล้ว (ไม่ส่ง Life)"
                        )
                        break
            except Exception as exc:
                # A malformed/mismatched screenshot must never turn into a
                # guessed tap.  The normal loop delay still continues in Player.
                return PopupCleanupResult(tuple(actions), str(exc))
            if len(handled) >= len(self.rules):
                break
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                stop_event.wait(min(self.poll_seconds, remaining))
        return PopupCleanupResult(tuple(actions))
