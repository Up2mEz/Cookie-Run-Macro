from __future__ import annotations

import random
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageChops, ImageStat

from adb_manager import ADBError, ADBManager, PersistentShell
from event_model import ADAPTIVE_WAIT_TYPE, normalize_pattern
from state import AppState, StateMachine
from sync_detector import stable_sync_target


@dataclass(frozen=True)
class ScheduledAction:
    at: float
    action: str
    duration_ms: int = 0
    source_id: str = ""
    x: int | None = None
    y: int | None = None
    phase: str = "synced"
    event_type: str = "action"
    adaptive: dict | None = None


def choose_pre_sync_delay_ms(
    playback: dict, history: list[int], rng: random.Random | None = None,
) -> int:
    """Select a per-round delay, excluding values too close to recent rounds."""
    if str(playback.get("pre_sync_delay_mode", "fixed")).lower() != "random":
        return max(0, int(playback.get("pre_sync_extra_ms", 0)))
    low = max(0, int(playback.get("pre_sync_random_min_ms", 0)))
    high = max(low, int(playback.get("pre_sync_random_max_ms", low)))
    min_delta = max(0, int(playback.get("pre_sync_random_min_delta_ms", 50)))
    history_size = max(1, int(playback.get("pre_sync_random_history", 3)))
    recent = [int(value) for value in history[-history_size:]]
    random_source = rng or random.Random()
    if low == high:
        return low
    if not min_delta:
        return random_source.randint(low, high)
    # A spaced bag prevents arbitrary early draws from covering the entire
    # range later. With 300–500 / 50 ms this produces
    # 300,350,400,450,500 in shuffled order and never 300 -> 310.
    candidates = list(range(low, high + 1, min_delta))
    if candidates[-1] != high and high - candidates[-1] >= min_delta:
        candidates.append(high)
    available = [
        value for value in candidates
        if all(abs(value - previous) >= min_delta for previous in recent)
    ]
    if not available:
        raise ValueError(
            "ช่วง Random ก่อน Sync แคบเกินกว่าจะรักษาระยะห่างจากค่าที่ยังจำอยู่"
        )
    return random_source.choice(available)


def prepare_round_events(pattern: dict, rng: random.Random | None = None) -> list[ScheduledAction]:
    normalized, _ = normalize_pattern(pattern)
    random_source = rng or random.Random()
    scheduled: list[ScheduledAction] = []
    for event in normalized["events"]:
        action: str | None = None
        effective_at = float(event["at"])
        if event["event_class"] == "required":
            action = event["action"]
        else:
            jitter = int(event.get("jitter_ms", 0))
            if jitter:
                effective_at += random_source.uniform(-jitter, jitter) / 1000
            if event["type"] == "optional_action":
                if random_source.uniform(0, 100) < event["chance"]:
                    action = event["action"]
            else:
                options = event["options"]
                keys = ["none", "jump", "slide"]
                action = random_source.choices(keys, weights=[options[key] for key in keys], k=1)[0]
                if action == "none":
                    action = None
        if action:
            scheduled.append(
                ScheduledAction(
                    max(0.0, effective_at),
                    action,
                    int(event.get("duration_ms", 0)),
                    str(event["id"]),
                    int(event["x"]) if "x" in event else None,
                    int(event["y"]) if "y" in event else None,
                    str(event.get("phase", "synced")),
                    str(event.get("type", "action")),
                    ({
                        key: event[key]
                        for key in (
                            "detect_x", "detect_y", "detect_radius", "change_threshold",
                            "stable_frames", "poll_ms", "arm_delay_ms", "success_delay_ms",
                            "timeout_seconds",
                        )
                    } if event.get("type") == ADAPTIVE_WAIT_TYPE else None),
                )
            )
    return sorted(scheduled, key=lambda item: (item.at, item.source_id))


class Player:
    def __init__(
        self,
        adb: ADBManager,
        state: StateMachine,
        *,
        on_round: Callable[[int, int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> None:
        self.adb = adb
        self.state = state
        self.on_round = on_round
        self.on_error = on_error
        self.on_progress = on_progress
        self.stop_event = threading.Event()
        self.manual_sync_event = threading.Event()
        self.auto_sync_event = threading.Event()
        self._shell: PersistentShell | None = None

    def _emit_progress(
        self,
        phase: str,
        *,
        elapsed: float = 0.0,
        duration: float = 0.0,
        event_index: int = 0,
        event_total: int = 0,
        round_number: int = 1,
        round_total: int = 1,
        pre_sync_delay_ms: int | None = None,
    ) -> None:
        if self.on_progress:
            snapshot = {
                "phase": phase,
                "elapsed": max(0.0, float(elapsed)),
                "duration": max(0.0, float(duration)),
                "event_index": max(0, int(event_index)),
                "event_total": max(0, int(event_total)),
                "round_number": max(1, int(round_number)),
                "round_total": max(0, int(round_total)),
                "reported_at": time.perf_counter(),
            }
            if pre_sync_delay_ms is not None:
                snapshot["pre_sync_delay_ms"] = max(0, int(pre_sync_delay_ms))
            self.on_progress(snapshot)

    def _wait_on_timeline(
        self,
        started_at: float,
        target_seconds: float,
        *,
        phase: str,
        duration: float,
        event_index: int,
        event_total: int,
        round_number: int,
        round_total: int,
        interrupt_event: threading.Event | None = None,
    ) -> bool:
        while not self.stop_event.is_set():
            if interrupt_event and interrupt_event.is_set():
                return False
            elapsed = max(0.0, time.perf_counter() - started_at)
            self._emit_progress(
                phase,
                elapsed=elapsed,
                duration=duration,
                event_index=event_index,
                event_total=event_total,
                round_number=round_number,
                round_total=round_total,
            )
            remaining = target_seconds - elapsed
            if remaining <= 0:
                return True
            if self.stop_event.wait(min(0.1, remaining)):
                return False
        return False

    def request_manual_sync(self) -> None:
        self.manual_sync_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.manual_sync_event.set()
        try:
            self.state.transition(AppState.STOPPING, force=True)
        except Exception:
            pass
        if self._shell:
            self._shell.close()
            self._shell = None

    @staticmethod
    def _command(action: ScheduledAction, controls: dict) -> str:
        if action.action in {"tap", "hold"}:
            if action.x is None or action.y is None:
                raise ValueError("Touch event ไม่มีพิกัด X/Y")
            x, y = action.x, action.y
        else:
            point = controls[action.action]
            x, y = int(point["x"]), int(point["y"])
        if action.action in {"slide", "hold"}:
            duration = max(1, int(action.duration_ms or 400))
            return f"input swipe {x} {y} {x} {y} {duration}"
        return f"input tap {x} {y}"

    @staticmethod
    def _adaptive_patch(image: Image.Image, settings: dict) -> Image.Image:
        x, y = int(settings["detect_x"]), int(settings["detect_y"])
        radius = int(settings["detect_radius"])
        left, top = max(0, x - radius), max(0, y - radius)
        right, bottom = min(image.width, x + radius + 1), min(image.height, y + radius + 1)
        if left >= right or top >= bottom or not (0 <= x < image.width and 0 <= y < image.height):
            raise ValueError(
                f"จุดตรวจ Stop ({x},{y}) อยู่นอกภาพ MuMu {image.width}×{image.height}"
            )
        return image.convert("RGB").crop((left, top, right, bottom))

    @staticmethod
    def _adaptive_change_score(reference: Image.Image, current: Image.Image) -> float:
        if current.size != reference.size:
            current = current.resize(reference.size)
        means = ImageStat.Stat(ImageChops.difference(reference, current)).mean
        return sum(means) / (len(means) * 255.0)

    def _arm_adaptive_after_trigger(
        self,
        action: ScheduledAction,
        serial: str,
        ready_reference: Image.Image,
    ) -> Image.Image | None:
        """Capture the temporary Stop state immediately after random boost starts."""
        settings = action.adaptive or {}
        threshold = float(settings.get("change_threshold", 0.12))
        poll_seconds = min(0.05, int(settings.get("poll_ms", 250)) / 1000)
        arm_seconds = int(settings.get("arm_delay_ms", 500)) / 1000
        deadline = time.perf_counter() + max(0.25, arm_seconds)
        while not self.stop_event.is_set() and time.perf_counter() < deadline:
            if self.stop_event.wait(poll_seconds):
                return None
            current_image, _source = self.adb.capture_image(serial)
            current = self._adaptive_patch(current_image, settings)
            if self._adaptive_change_score(ready_reference, current) >= threshold:
                return current
        return None

    def _wait_for_adaptive_release(
        self,
        action: ScheduledAction,
        serial: str,
        *,
        round_number: int,
        round_total: int,
        event_index: int,
        event_total: int,
        armed_reference: Image.Image | None = None,
    ) -> bool:
        settings = action.adaptive or {}
        if armed_reference is None:
            if self.stop_event.wait(int(settings.get("arm_delay_ms", 500)) / 1000):
                return False
            reference_image, _source = self.adb.capture_image(serial)
            reference = self._adaptive_patch(reference_image, settings)
        else:
            # A fast random-boost roll can finish before the Adaptive event's
            # recorded timestamp. Use the Stop-button patch captured shortly
            # after the trigger tap so Play never becomes a stale reference.
            reference = armed_reference
        threshold = float(settings.get("change_threshold", 0.12))
        required_stable = int(settings.get("stable_frames", 2))
        poll_seconds = int(settings.get("poll_ms", 250)) / 1000
        timeout_seconds = int(settings.get("timeout_seconds", 0))
        started = time.perf_counter()
        stable = 0
        while not self.stop_event.is_set():
            elapsed = time.perf_counter() - started
            self._emit_progress(
                "waiting_pre_sync_condition", elapsed=elapsed, duration=float(timeout_seconds),
                event_index=event_index - 1, event_total=event_total,
                round_number=round_number, round_total=round_total,
            )
            if timeout_seconds and elapsed >= timeout_seconds:
                raise TimeoutError(
                    "รอสุ่มทักษะเกินเวลาที่กำหนด — ยังไม่กด Play เพื่อความปลอดภัย"
                )
            if self.stop_event.wait(poll_seconds):
                return False
            current_image, _source = self.adb.capture_image(serial)
            current = self._adaptive_patch(current_image, settings)
            score = self._adaptive_change_score(reference, current)
            stable = stable + 1 if score >= threshold else 0
            if stable >= required_stable:
                break
        if self.stop_event.is_set():
            return False
        if self.stop_event.wait(int(settings.get("success_delay_ms", 250)) / 1000):
            return False
        return True

    def play(
        self,
        pattern: dict,
        serial: str,
        *,
        repeat_count: int = 1,
        loop_interval_ms: int = 0,
        sync_each_loop: bool = True,
        sync_waiter: Callable[[threading.Event, threading.Event, threading.Event], bool | float] | None = None,
        result_waiter: Callable[[threading.Event, threading.Event, threading.Event, float], bool] | None = None,
        popup_cleanup_waiter: Callable[[threading.Event, float], None] | None = None,
    ) -> None:
        normalized, _ = normalize_pattern(pattern)
        if not normalized["events"]:
            raise ValueError("Pattern นี้มี 0 Events จึงยังเล่นไม่ได้ — กรุณาอัดใหม่และตรวจว่า Event เพิ่มขึ้น")
        self.stop_event.clear()
        self.manual_sync_event.clear()
        self.auto_sync_event.clear()
        repeat_count = int(repeat_count)
        loop_forever = repeat_count <= 0
        repeat_count = max(1, repeat_count)
        loop_interval = max(0, int(loop_interval_ms)) / 1000
        try:
            self.state.transition(AppState.CONNECTING, force=True)
            self.adb.probe_device(serial)
            self._shell = self.adb.open_persistent_shell(serial)
            round_number = 1
            first_sync_complete = False
            result_failed = False
            pre_sync_delay_history: list[int] = []
            delay_rng = random.Random()
            while loop_forever or round_number <= repeat_count:
                if self.stop_event.is_set():
                    break
                self.manual_sync_event.clear()
                self.auto_sync_event.clear()
                if self.on_round:
                    self.on_round(round_number, 0 if loop_forever else repeat_count)
                all_actions = prepare_round_events(normalized)
                pre_actions = [action for action in all_actions if action.phase == "pre_sync"]
                synced_actions = [action for action in all_actions if action.phase == "synced"]
                post_actions = [action for action in all_actions if action.phase == "post_game"]
                displayed_round_total = 0 if loop_forever else repeat_count
                needs_sync = not first_sync_complete or sync_each_loop
                selected_pre_sync_delay_ms = (
                    choose_pre_sync_delay_ms(normalized.get("playback", {}), pre_sync_delay_history, delay_rng)
                    if needs_sync else 0
                )
                if needs_sync:
                    pre_sync_delay_history.append(selected_pre_sync_delay_ms)
                pre_sync_extra = selected_pre_sync_delay_ms / 1000
                if pre_actions or pre_sync_extra:
                    self.state.transition(AppState.PLAYING, force=True)
                    pre_start = time.perf_counter()
                    pre_event_duration = max((action.at + action.duration_ms / 1000 for action in pre_actions), default=0.0)
                    pre_duration = pre_event_duration + pre_sync_extra
                    self._emit_progress(
                        "pre_sync", duration=pre_duration,
                        round_number=round_number, round_total=displayed_round_total,
                        pre_sync_delay_ms=selected_pre_sync_delay_ms,
                    )
                    skipped_pre_ids: set[str] = set()
                    adaptive_completed_at: float | None = None
                    armed_adaptive_references: dict[str, Image.Image] = {}
                    for event_index, action in enumerate(pre_actions, start=1):
                        if action.source_id in skipped_pre_ids:
                            continue
                        if not self._wait_on_timeline(
                            pre_start, action.at, phase="pre_sync", duration=pre_duration,
                            event_index=event_index - 1, event_total=len(pre_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        ):
                            break
                        if self.stop_event.is_set():
                            break
                        if action.event_type == ADAPTIVE_WAIT_TYPE:
                            final_play = pre_actions[-1]
                            if not self._wait_for_adaptive_release(
                                action, serial, round_number=round_number,
                                round_total=displayed_round_total, event_index=event_index,
                                event_total=len(pre_actions),
                                armed_reference=armed_adaptive_references.pop(action.source_id, None),
                            ):
                                break
                            if not self._shell:
                                raise ADBError("Persistent ADB shell ไม่พร้อมใช้งาน")
                            self._shell.send(self._command(final_play, normalized["controls"]))
                            skipped_pre_ids.add(final_play.source_id)
                            adaptive_completed_at = time.perf_counter()
                            self._emit_progress(
                                "pre_sync", elapsed=final_play.at, duration=pre_duration,
                                event_index=len(pre_actions), event_total=len(pre_actions),
                                round_number=round_number, round_total=displayed_round_total,
                            )
                            continue
                        else:
                            if not self._shell:
                                raise ADBError("Persistent ADB shell ไม่พร้อมใช้งาน")
                            next_action = pre_actions[event_index] if event_index < len(pre_actions) else None
                            ready_reference: Image.Image | None = None
                            if next_action is not None and next_action.event_type == ADAPTIVE_WAIT_TYPE:
                                ready_image, _source = self.adb.capture_image(serial)
                                ready_reference = self._adaptive_patch(ready_image, next_action.adaptive or {})
                            self._shell.send(self._command(action, normalized["controls"]))
                            if next_action is not None and ready_reference is not None:
                                armed_reference = self._arm_adaptive_after_trigger(
                                    next_action, serial, ready_reference,
                                )
                                if armed_reference is not None:
                                    armed_adaptive_references[next_action.source_id] = armed_reference
                        self._emit_progress(
                            "pre_sync", elapsed=action.at, duration=pre_duration,
                            event_index=event_index, event_total=len(pre_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        )
                    if self.stop_event.is_set():
                        break
                    if adaptive_completed_at is not None:
                        if pre_sync_extra and not self._wait_on_timeline(
                            adaptive_completed_at, pre_sync_extra, phase="pre_sync", duration=pre_sync_extra,
                            event_index=len(pre_actions), event_total=len(pre_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        ):
                            break
                    elif not self._wait_on_timeline(
                            pre_start, pre_duration, phase="pre_sync", duration=pre_duration,
                            event_index=len(pre_actions), event_total=len(pre_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        ):
                            break
                synced = True
                sync_anchor: float | None = None
                if needs_sync:
                    self._emit_progress(
                        "waiting_sync", round_number=round_number, round_total=displayed_round_total,
                    )
                if sync_waiter and needs_sync:
                    sync_outcome = sync_waiter(self.stop_event, self.manual_sync_event, self.auto_sync_event)
                    synced = bool(sync_outcome)
                    if isinstance(sync_outcome, (int, float)) and not isinstance(sync_outcome, bool):
                        sync_anchor = min(time.perf_counter(), float(sync_outcome))
                elif needs_sync:
                    self.state.transition(AppState.WAITING_FOR_MANUAL_SYNC, force=True)
                    while not self.stop_event.is_set() and not self.manual_sync_event.wait(0.25):
                        pass
                    synced = self.manual_sync_event.is_set() and not self.stop_event.is_set()
                    if synced:
                        sync_anchor = time.perf_counter()
                if not synced or self.stop_event.is_set():
                    break
                first_sync_complete = True
                self.state.transition(AppState.SYNCED, force=True)
                detected_at = sync_anchor or time.perf_counter()
                round_start, remaining_offset = stable_sync_target(
                    detected_at, int(normalized["sync"].get("offset_ms", 0)),
                )
                if remaining_offset > 0 and self.stop_event.wait(remaining_offset):
                    break
                self.state.transition(AppState.PLAYING, force=True)
                synced_duration = max((action.at + action.duration_ms / 1000 for action in synced_actions), default=0.0)
                result_detected = threading.Event()
                gameplay_end_detected = threading.Event()
                result_watch_done = threading.Event()
                result_watch_cancel = threading.Event()
                result_detected_at: list[float] = []
                post_game_enabled = bool(normalized.get("post_game", {}).get("enabled", True) and result_waiter)
                watcher: threading.Thread | None = None
                if post_game_enabled:
                    def watch_result() -> None:
                        try:
                            if result_waiter and result_waiter(
                                self.stop_event, result_watch_cancel, gameplay_end_detected, synced_duration,
                            ):
                                result_detected_at.append(time.perf_counter())
                                gameplay_end_detected.set()
                                result_detected.set()
                        finally:
                            result_watch_done.set()

                    watcher = threading.Thread(target=watch_result, daemon=True)
                    watcher.start()
                for event_index, action in enumerate(synced_actions, start=1):
                    if not self._wait_on_timeline(
                        round_start, action.at, phase="synced", duration=synced_duration,
                        event_index=event_index - 1, event_total=len(synced_actions),
                        round_number=round_number, round_total=displayed_round_total,
                        interrupt_event=gameplay_end_detected,
                    ):
                        break
                    if self.stop_event.is_set() or gameplay_end_detected.is_set():
                        break
                    if not self._shell:
                        raise ADBError("Persistent ADB shell ไม่พร้อมใช้งาน")
                    self._shell.send(self._command(action, normalized["controls"]))
                    self._emit_progress(
                        "synced", elapsed=action.at, duration=synced_duration,
                        event_index=event_index, event_total=len(synced_actions),
                        round_number=round_number, round_total=displayed_round_total,
                    )
                if self.stop_event.is_set():
                    result_watch_cancel.set()
                    break
                if not gameplay_end_detected.is_set():
                    timeline_finished = self._wait_on_timeline(
                        round_start, synced_duration, phase="synced", duration=synced_duration,
                        event_index=len(synced_actions), event_total=len(synced_actions),
                        round_number=round_number, round_total=displayed_round_total,
                        interrupt_event=gameplay_end_detected,
                    )
                    if not timeline_finished and self.stop_event.is_set():
                        result_watch_cancel.set()
                        break

                if post_actions and post_game_enabled:
                    waiting_started = time.perf_counter()
                    while not self.stop_event.is_set() and not result_detected.is_set() and not result_watch_done.is_set():
                        self._emit_progress(
                            "waiting_result", elapsed=time.perf_counter() - waiting_started,
                            duration=float(normalized["post_game"].get("timeout_seconds", 45)),
                            event_index=0, event_total=len(post_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        )
                        self.stop_event.wait(0.1)
                    if self.stop_event.is_set():
                        result_watch_cancel.set()
                        break
                    if not result_detected.is_set():
                        result_failed = True
                        self._emit_progress(
                            "result_timeout", round_number=round_number, round_total=displayed_round_total,
                            event_total=len(post_actions),
                        )
                        result_watch_cancel.set()
                        break
                    post_start = result_detected_at[0]
                    post_duration = max((action.at + action.duration_ms / 1000 for action in post_actions), default=0.0)
                    for event_index, action in enumerate(post_actions, start=1):
                        if not self._wait_on_timeline(
                            post_start, action.at, phase="post_game", duration=post_duration,
                            event_index=event_index - 1, event_total=len(post_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        ):
                            break
                        if self.stop_event.is_set():
                            break
                        if action.action not in {"tap", "hold"}:
                            continue
                        if not self._shell:
                            raise ADBError("Persistent ADB shell ไม่พร้อมใช้งาน")
                        self._shell.send(self._command(action, normalized["controls"]))
                        self._emit_progress(
                            "post_game", elapsed=action.at, duration=post_duration,
                            event_index=event_index, event_total=len(post_actions),
                            round_number=round_number, round_total=displayed_round_total,
                        )
                    if self.stop_event.is_set():
                        result_watch_cancel.set()
                        break
                    if not self._wait_on_timeline(
                        post_start, post_duration, phase="post_game", duration=post_duration,
                        event_index=len(post_actions), event_total=len(post_actions),
                        round_number=round_number, round_total=displayed_round_total,
                    ):
                        result_watch_cancel.set()
                        break
                result_watch_cancel.set()
                if watcher:
                    watcher.join(timeout=0.2)
                if (loop_forever or round_number < repeat_count) and loop_interval:
                    delay_start = time.perf_counter()
                    if popup_cleanup_waiter and not self.stop_event.is_set():
                        popup_cleanup_waiter(self.stop_event, loop_interval)
                    if not self._wait_on_timeline(
                        delay_start, loop_interval, phase="loop_delay", duration=loop_interval,
                        event_index=0, event_total=0,
                        round_number=round_number, round_total=displayed_round_total,
                    ):
                        break
                round_number += 1
            if result_failed:
                self._emit_progress(
                    "result_timeout", round_number=max(1, round_number),
                    round_total=0 if loop_forever else repeat_count,
                )
                self.state.transition(AppState.STOPPED, force=True)
                return
            self._emit_progress(
                "stopped" if self.stop_event.is_set() else "completed", elapsed=1.0, duration=1.0,
                round_number=max(1, round_number - 1), round_total=0 if loop_forever else repeat_count,
            )
            self.state.transition(AppState.STOPPED, force=True)
        except Exception as exc:
            self._emit_progress("error")
            if self.stop_event.is_set():
                self.state.transition(AppState.STOPPED, force=True)
            else:
                self.state.transition(AppState.ERROR, force=True)
                if self.on_error:
                    self.on_error(str(exc))
                else:
                    raise
        finally:
            if self._shell:
                self._shell.close()
                self._shell = None
