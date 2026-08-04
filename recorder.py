from __future__ import annotations

import threading
import time
from copy import deepcopy
from datetime import datetime

from adb_manager import PersistentShell
from event_model import normalize_pattern
from state import AppState, StateMachine


class RecorderError(RuntimeError):
    pass


DOUBLE_JUMP_MAX_GAP_MS = 650


def prepare_recording_candidate(
    recorded_pattern: dict,
    target_name: str,
    *,
    new_pattern: bool,
) -> tuple[dict, list[str]]:
    """Prepare a recording for New vs Overwrite without leaking old Safe Zones.

    Recording starts from the currently selected Pattern so it can reuse device,
    sync, and playback settings.  A genuinely new destination must behave like a
    clean/Set Zero Pattern: it keeps the recorded Events but starts with no Safe
    Zones.  Normalization then restores system-converted Safe Random Events to
    their original Required actions.
    """
    candidate = deepcopy(recorded_pattern)
    candidate["name"] = target_name
    if new_pattern:
        candidate["safe_zones"] = []
    return normalize_pattern(candidate)


class Recorder:
    def __init__(self, state: StateMachine) -> None:
        self.state = state
        self.stop_event = threading.Event()
        self.manual_sync_event = threading.Event()
        self.auto_sync_event = threading.Event()
        self.post_game_event = threading.Event()
        self._lock = threading.RLock()
        self._pattern: dict | None = None
        self._armed_at: float | None = None
        self._anchor: float | None = None
        self._post_game_anchor: float | None = None
        self._pressed: set[str] = set()
        self._slide_started: float | None = None
        self._slide_position: tuple[str, float] | None = None
        self._slide_sent_to_adb = False
        self._shell: PersistentShell | None = None

    def start(self, pattern: dict, shell: PersistentShell | None = None) -> None:
        with self._lock:
            self._pattern = deepcopy(pattern)
            self._pattern["events"] = []
            self._pattern["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._anchor = None
            self._post_game_anchor = None
            self._armed_at = time.perf_counter()
            self._pressed.clear()
            self._slide_started = None
            self._slide_position = None
            self._slide_sent_to_adb = False
            self._shell = shell
            self.stop_event.clear()
            self.manual_sync_event.clear()
            self.auto_sync_event.clear()
            self.post_game_event.clear()
            self.state.transition(AppState.RECORDING_WAITING_SYNC, force=True)

    @property
    def is_synced(self) -> bool:
        return self._anchor is not None

    def sync(self, *, automatic: bool = False, anchor: float | None = None) -> None:
        with self._lock:
            if self._pattern is None:
                raise RecorderError("ยังไม่ได้เริ่มอัด")
            if self._anchor is not None:
                return
            now = time.perf_counter()
            self._anchor = min(now, float(anchor)) if anchor is not None else now
            (self.auto_sync_event if automatic else self.manual_sync_event).set()
            self.state.transition(AppState.RECORDING, force=True)

    def mark_post_game(self, now: float | None = None) -> bool:
        """เปลี่ยน anchor เป็นหน้า Result; หลังจากนี้รับเฉพาะ Tap/Hold."""
        with self._lock:
            if self._pattern is None or self._post_game_anchor is not None:
                return False
            detected_at = now if now is not None else time.perf_counter()
            if self._slide_started is not None:
                self.key_up("k", detected_at)
            self._post_game_anchor = detected_at
            self._pressed.clear()
            self.post_game_event.set()
            return True

    def _at(self, now: float | None = None) -> float:
        if self._anchor is None:
            raise RecorderError("กด F9 เพื่อ Sync ก่อนอัด Jump/Slide")
        return max(0.0, (now if now is not None else time.perf_counter()) - self._anchor)

    def _event_position(self, now: float | None = None) -> tuple[str, float]:
        current = now if now is not None else time.perf_counter()
        if self._post_game_anchor is not None and current >= self._post_game_anchor:
            return "post_game", max(0.0, current - self._post_game_anchor)
        if self._anchor is not None and current >= self._anchor:
            return "synced", max(0.0, current - self._anchor)
        if self._armed_at is None:
            raise RecorderError("ยังไม่ได้เริ่มอัด")
        return "pre_sync", max(0.0, current - self._armed_at)

    def key_down(self, key: str, now: float | None = None, *, send_input: bool = True) -> None:
        key = key.lower()
        if key not in {"j", "k"}:
            return
        with self._lock:
            if self._post_game_anchor is not None:
                return
            if key in self._pressed:
                return
            self._pressed.add(key)
            phase, at = self._event_position(now)
            if not self._pattern:
                return
            if key == "j":
                self._pattern["events"].append(
                    {"id": f"evt_{len(self._pattern['events']) + 1:04d}", "at": at, "phase": phase, "event_class": "required", "type": "action", "action": "jump", "chance": 100, "jitter_ms": 0}
                )
                if self._shell and send_input:
                    point = self._pattern["controls"]["jump"]
                    self._shell.send(f"input tap {int(point['x'])} {int(point['y'])}")
            else:
                self._slide_started = now if now is not None else time.perf_counter()
                self._slide_position = self._event_position(self._slide_started)
                self._slide_sent_to_adb = bool(self._shell and send_input)
                if self._slide_sent_to_adb:
                    point = self._pattern["controls"]["slide"]
                    self._shell.send(f"input motionevent DOWN {int(point['x'])} {int(point['y'])}")

    def key_up(self, key: str, now: float | None = None) -> None:
        key = key.lower()
        with self._lock:
            self._pressed.discard(key)
            if key != "k" or self._slide_started is None or not self._pattern:
                return
            ended = now if now is not None else time.perf_counter()
            duration_ms = max(1, round((ended - self._slide_started) * 1000))
            phase, at = self._slide_position or self._event_position(self._slide_started)
            self._pattern["events"].append(
                {"id": f"evt_{len(self._pattern['events']) + 1:04d}", "at": at, "phase": phase, "event_class": "required", "type": "action", "action": "slide", "duration_ms": duration_ms, "chance": 100, "jitter_ms": 0}
            )
            if self._shell and self._slide_sent_to_adb:
                point = self._pattern["controls"]["slide"]
                self._shell.send(f"input motionevent UP {int(point['x'])} {int(point['y'])}")
            self._slide_started = None
            self._slide_position = None
            self._slide_sent_to_adb = False

    def record_external_tap(
        self,
        action: str,
        *,
        started_at: float | None = None,
        duration_ms: int = 60,
        x: int | None = None,
        y: int | None = None,
    ) -> tuple[dict, bool]:
        """บันทึกการคลิกจาก MuMu/Preview โดยไม่ส่ง ADB ซ้ำ.

        Slide tap สั้นที่อยู่ติดกันจะถูกรวมเป็น hold เดียวเมื่อเปิด rapid_tap_to_hold.
        """
        action = action.lower()
        if action not in {"jump", "slide", "tap", "hold"}:
            raise RecorderError("Touch ต้องเป็น jump, slide, tap หรือ hold")
        with self._lock:
            if not self._pattern:
                raise RecorderError("ยังไม่ได้เริ่มอัด")
            started = started_at if started_at is not None else time.perf_counter()
            phase, at = self._event_position(started)
            duration_ms = max(1, int(duration_ms))
            if phase == "post_game" and action in {"jump", "slide"}:
                action = "hold" if duration_ms >= 250 else "tap"
            if action in {"tap", "hold"} and (x is None or y is None):
                raise RecorderError("Touch event ต้องมีพิกัด X/Y")
            if action in {"slide", "tap", "hold"}:
                settings = self._pattern.get("recording", {})
                enabled = bool(settings.get("rapid_tap_to_hold", True))
                gap_ms = int(settings.get("rapid_tap_gap_ms", 180))
                if enabled and self._pattern["events"]:
                    previous = self._pattern["events"][-1]
                    same_touch = (
                        action in {"tap", "hold"}
                        and previous.get("action") in {"tap", "hold"}
                        and abs(int(previous.get("x", -10000)) - int(x)) <= 24
                        and abs(int(previous.get("y", -10000)) - int(y)) <= 24
                    )
                    same_slide = action == "slide" and previous.get("action") == "slide"
                    if (same_slide or same_touch) and previous.get("phase", "synced") == phase:
                        previous_end = float(previous["at"]) + int(previous.get("duration_ms", 60)) / 1000
                        gap = at - previous_end
                        if -0.05 <= gap <= gap_ms / 1000:
                            current_end = at + duration_ms / 1000
                            previous["duration_ms"] = max(1, round((current_end - float(previous["at"])) * 1000))
                            if same_touch:
                                previous["action"] = "hold"
                            return previous, True
            event = {
                "id": f"evt_{len(self._pattern['events']) + 1:04d}",
                "at": at,
                "phase": phase,
                "event_class": "required",
                "type": "action",
                "action": action,
                "chance": 100,
                "jitter_ms": 0,
            }
            if action in {"tap", "hold"}:
                event["x"] = max(0, int(x))
                event["y"] = max(0, int(y))
            if action in {"slide", "hold"}:
                event["duration_ms"] = max(60, duration_ms)
            self._pattern["events"].append(event)
            return event, False

    def status(self, now: float | None = None) -> dict:
        """คืนข้อมูลสดของการอัดแบบ thread-safe สำหรับแถบสถานะ UI."""
        with self._lock:
            events = list((self._pattern or {}).get("events", []))
            counts = {action: 0 for action in ("jump", "slide", "tap", "hold")}
            for event in events:
                action = str(event.get("action", ""))
                if action in counts:
                    counts[action] += 1
            jump_events = [event for event in events if event.get("action") == "jump"]
            last_jump_kind = "none"
            last_jump_gap_ms = None
            if jump_events:
                last_jump_kind = "single"
            if len(jump_events) >= 2:
                previous, latest = jump_events[-2:]
                if previous.get("phase", "synced") == latest.get("phase", "synced"):
                    gap_ms = round((float(latest.get("at", 0.0)) - float(previous.get("at", 0.0))) * 1000)
                    if 0 <= gap_ms <= DOUBLE_JUMP_MAX_GAP_MS:
                        last_jump_kind = "double"
                        last_jump_gap_ms = gap_ms
            current = now if now is not None else time.perf_counter()
            elapsed = max(0.0, current - self._anchor) if self._anchor is not None else 0.0
            armed_elapsed = max(0.0, current - self._armed_at) if self._armed_at is not None else 0.0
            post_game_elapsed = max(0.0, current - self._post_game_anchor) if self._post_game_anchor is not None else 0.0
            return {
                "active": self._pattern is not None,
                "synced": self._anchor is not None,
                "elapsed_seconds": elapsed,
                "armed_elapsed_seconds": armed_elapsed,
                "post_game": self._post_game_anchor is not None,
                "post_game_elapsed_seconds": post_game_elapsed,
                "event_count": len(events),
                "counts": counts,
                "last_jump_kind": last_jump_kind,
                "last_jump_gap_ms": last_jump_gap_ms,
                "pre_sync_count": sum(1 for event in events if event.get("phase") == "pre_sync"),
                "synced_count": sum(1 for event in events if event.get("phase", "synced") == "synced"),
                "post_game_count": sum(1 for event in events if event.get("phase") == "post_game"),
            }

    def finish(self) -> tuple[dict, list[str]]:
        with self._lock:
            if self._pattern is None:
                raise RecorderError("ยังไม่ได้เริ่มอัด")
            if self._slide_started is not None:
                self.key_up("k")
            result, warnings = normalize_pattern(self._pattern)
            self._pattern = None
            self._armed_at = None
            self._anchor = None
            self._post_game_anchor = None
            self.stop_event.set()
            if self._shell:
                self._shell.close()
                self._shell = None
            self.state.transition(AppState.STOPPED, force=True)
            return result, warnings

    def stop(self) -> None:
        with self._lock:
            self.stop_event.set()
            self.manual_sync_event.set()
            self._pattern = None
            self._armed_at = None
            self._anchor = None
            self._post_game_anchor = None
            self._pressed.clear()
            self._slide_started = None
            self._slide_position = None
            self._slide_sent_to_adb = False
            self.post_game_event.set()
            if self._shell:
                self._shell.close()
                self._shell = None
            self.state.transition(AppState.STOPPED, force=True)
