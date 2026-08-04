from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable


SAFE_ACTIONS = {"jump", "slide"}
VALID_ACTIONS = {"jump", "slide", "tap", "hold"}
ADAPTIVE_WAIT_TYPE = "adaptive_wait"
DEFAULT_SAFE_RANDOM_OPTIONS = {"none": 40, "jump": 40, "slide": 20}
DEFAULT_SYNC = {
    "mode": "manual_f9",
    "profile_name": "",
    "template_path": "",
    "roi": None,
    "poll_ms": 20,
    "threshold": 0.82,
    "consecutive_matches": 2,
    "timeout_seconds": 25,
    "offset_ms": 0,
    "manual_fallback": True,
}
DEFAULT_POST_GAME = {
    "enabled": True,
    "mode": "xp_result",
    "template_path": "templates/default_result_xp_1280x720.png",
    "roi": {"x": 155, "y": 450, "width": 130, "height": 75},
    "poll_ms": 700,
    "threshold": 0.86,
    "consecutive_matches": 3,
    "min_gameplay_seconds": 15,
    "timeout_seconds": 45,
    "pause_absent_threshold": 0.80,
    "challenge_enabled": True,
    "challenge_first_click_delay_ms": 1000,
    "challenge_inter_card_min_ms": 500,
    "challenge_inter_card_max_ms": 900,
    "challenge_next_round_min_ms": 1800,
    "challenge_next_round_max_ms": 2200,
    "challenge_transition_timeout_ms": 6500,
}


class EventValidationError(ValueError):
    pass


EDITOR_VALIDATION_ERROR = "_validation_error"


def editable_pattern_errors(pattern: dict[str, Any] | None) -> list[str]:
    if not isinstance(pattern, dict):
        return ["Pattern ต้องเป็น JSON object"]
    return [
        str(event.get(EDITOR_VALIDATION_ERROR))
        for event in pattern.get("events", [])
        if isinstance(event, dict) and event.get(EDITOR_VALIDATION_ERROR)
    ]


def apply_safe_random_options(
    pattern: dict[str, Any], options: dict[str, Any], event_ids: Iterable[str] | None = None
) -> tuple[dict[str, Any], list[str], int]:
    """Apply one weight set to selected Safe Random events, or all when ids is None."""
    weights = {action: _number(options.get(action, 0), f"Chance {action}") for action in ("none", "jump", "slide")}
    if any(value < 0 for value in weights.values()):
        raise EventValidationError("Chance ต้องไม่ติดลบ")
    if not math.isclose(sum(weights.values()), 100, rel_tol=0.0, abs_tol=1e-9):
        raise EventValidationError("Chance ของ jump, slide และ none ต้องรวมกันเท่ากับ 100%")
    normalized_weights = {key: int(value) if value.is_integer() else value for key, value in weights.items()}
    selected = None if event_ids is None else {str(event_id) for event_id in event_ids}
    candidate = deepcopy(pattern)
    changed = 0
    for event in candidate.get("events", []):
        if event.get("event_class") != "safe_random":
            continue
        if selected is not None and str(event.get("id")) not in selected:
            continue
        event["options"] = deepcopy(normalized_weights)
        changed += 1
    if changed == 0:
        raise EventValidationError("ไม่พบ Safe Random Event ในขอบเขตที่เลือก")
    normalized, warnings = normalize_pattern(candidate)
    return normalized, warnings, changed


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise EventValidationError(f"{field} ต้องเป็นตัวเลข")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"{field} ต้องเป็นตัวเลข") from exc
    if not math.isfinite(result):
        raise EventValidationError(f"{field} ต้องเป็นตัวเลขที่มีขอบเขต")
    return result


def normalize_safe_zones(safe_zones: Iterable[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for index, raw_zone in enumerate(safe_zones or [], start=1):
        if not isinstance(raw_zone, dict):
            raise EventValidationError(f"Safe Zone ลำดับ {index} ต้องเป็น object")
        zone = deepcopy(raw_zone)
        zone_id = str(zone.get("id") or f"zone_{index:03d}").strip()
        if zone_id in seen_ids:
            raise EventValidationError(f"Safe Zone ID ซ้ำ: {zone_id}")
        start = _number(zone.get("start"), f"Safe Zone {zone_id}: start")
        end = _number(zone.get("end"), f"Safe Zone {zone_id}: end")
        if start < 0:
            raise EventValidationError(f"Safe Zone {zone_id}: เวลาเริ่มต้องไม่ติดลบ")
        if end <= start:
            raise EventValidationError(f"Safe Zone {zone_id}: เวลาจบต้องมากกว่าเวลาเริ่ม")
        zone = {"id": zone_id, "start": round(start, 6), "end": round(end, 6), "label": str(zone.get("label", ""))}
        seen_ids.add(zone_id)
        normalized.append(zone)

    normalized.sort(key=lambda item: (item["start"], item["end"]))
    for previous, current in zip(normalized, normalized[1:]):
        if current["start"] <= previous["end"]:
            warnings.append(
                f"Safe Zone {previous['id']} และ {current['id']} ซ้อนกัน; ระบบจะมองช่วงรวมเป็นพื้นที่ปลอดภัย"
            )
    return normalized, warnings


def merged_safe_zone_ranges(safe_zones: Iterable[dict[str, Any]]) -> list[tuple[float, float]]:
    ranges: list[list[float]] = []
    for zone in sorted(safe_zones, key=lambda item: (item["start"], item["end"])):
        start, end = float(zone["start"]), float(zone["end"])
        if not ranges or start > ranges[-1][1]:
            ranges.append([start, end])
        else:
            ranges[-1][1] = max(ranges[-1][1], end)
    return [(start, end) for start, end in ranges]


def merge_safe_zones(safe_zones: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """รวมช่วงที่ซ้อนกัน พร้อมคืน alias เพื่อย้าย event ไปยัง zone หลักอย่างปลอดภัย."""
    merged: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    warnings: list[str] = []
    for zone in safe_zones:
        if not merged or zone["start"] > merged[-1]["end"]:
            merged.append(deepcopy(zone))
            aliases[zone["id"]] = zone["id"]
            continue
        target = merged[-1]
        aliases[zone["id"]] = target["id"]
        target["end"] = max(target["end"], zone["end"])
        if zone["label"] and zone["label"] not in target["label"]:
            target["label"] = " / ".join(filter(None, (target["label"], zone["label"])))
        warnings.append(f"รวม Safe Zone {zone['id']} เข้ากับ {target['id']} เพราะช่วงเวลาซ้อนกัน")
    return merged, aliases, warnings


def remove_safe_zone(
    pattern: dict[str, Any],
    zone_id: str,
    *,
    remove_linked_events: bool = False,
) -> tuple[dict[str, Any], list[str], int]:
    """ลบ Zone แบบไม่แก้ object ต้นฉบับ และบังคับตัดสินใจเมื่อมี Event อ้างอิงอยู่."""
    candidate = deepcopy(pattern)
    zones = candidate.get("safe_zones", [])
    if not any(str(zone.get("id")) == zone_id for zone in zones):
        raise EventValidationError(f"ไม่พบ Safe Zone {zone_id}")
    linked = [event for event in candidate.get("events", []) if event.get("safe_zone_id") == zone_id]
    if linked and not remove_linked_events:
        raise EventValidationError(f"Safe Zone {zone_id} มี Safe Random {len(linked)} Event")
    candidate["safe_zones"] = [zone for zone in zones if str(zone.get("id")) != zone_id]
    if linked:
        linked_ids = {str(event.get("id")) for event in linked}
        candidate["events"] = [event for event in candidate.get("events", []) if str(event.get("id")) not in linked_ids]
    normalized, warnings = normalize_pattern(candidate)
    return normalized, warnings, len(linked)


def split_final_tap_for_adaptive(
    events: list[dict[str, Any]], updated_event: dict[str, Any], old_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """When a recorded final Play tap is changed to Adaptive, preserve the tap as the deadline event."""
    remaining = [deepcopy(event) for event in events if str(event.get("id")) != str(old_id)]
    original = next((event for event in events if str(event.get("id")) == str(old_id)), None)
    updated = deepcopy(updated_event)
    if not (
        original
        and original.get("type", "action") == "action"
        and original.get("action") == "tap"
        and original.get("phase") == "pre_sync"
        and updated.get("type") == ADAPTIVE_WAIT_TYPE
    ):
        return remaining, updated, False
    pre_sync = sorted(
        (event for event in events if event.get("phase") == "pre_sync"),
        key=lambda event: (float(event.get("at", 0)), str(event.get("id", ""))),
    )
    if not pre_sync or str(pre_sync[-1].get("id")) != str(old_id):
        return remaining, updated, False

    deadline = float(original["at"])
    previous = pre_sync[-2] if len(pre_sync) >= 2 else None
    previous_at = float(previous["at"]) if previous else 0.0
    requested_at = float(updated.get("at", deadline))
    if not previous_at < requested_at < deadline:
        requested_at = previous_at + min(0.25, max(0.001, (deadline - previous_at) / 2))
    updated["at"] = round(requested_at, 6)

    used_ids = {str(event.get("id")) for event in events}
    base_id = f"{old_id}_play" if old_id else "adaptive_play"
    final_id = base_id
    suffix = 2
    while final_id in used_ids:
        final_id = f"{base_id}_{suffix}"
        suffix += 1
    final_play = deepcopy(original)
    final_play["id"] = final_id
    remaining.append(final_play)
    return remaining, updated, True


def _normalize_required(event: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    original_type = event.get("type")
    action = str(event.get("action", "")).lower()
    if original_type == ADAPTIVE_WAIT_TYPE:
        if event.get("phase") != "pre_sync":
            raise EventValidationError("รอสุ่มเสร็จ → กด Play ใช้ได้เฉพาะ phase pre_sync")
        event["event_class"] = "required"
        event["type"] = ADAPTIVE_WAIT_TYPE
        event["action"] = "tap"
        event["chance"] = 100
        event["jitter_ms"] = 0
        event.pop("options", None)
        event.pop("safe_zone_id", None)
        for axis in ("detect_x", "detect_y"):
            coordinate = int(round(_number(event.get(axis), axis)))
            if coordinate < 0:
                raise EventValidationError(f"{axis} ต้องไม่ติดลบ")
            event[axis] = coordinate
        limits = {
            "detect_radius": (3, 120, 24),
            "stable_frames": (1, 10, 2),
            "poll_ms": (80, 2000, 250),
            "arm_delay_ms": (0, 5000, 150),
            "success_delay_ms": (0, 5000, 250),
            "timeout_seconds": (0, 3600, 0),
        }
        for field, (minimum, maximum, default) in limits.items():
            value = int(round(_number(event.get(field, default), field)))
            if not minimum <= value <= maximum:
                raise EventValidationError(f"{field} ต้องอยู่ระหว่าง {minimum}–{maximum}")
            event[field] = value
        threshold = _number(event.get("change_threshold", 0.12), "change_threshold")
        if not 0.03 <= threshold <= 1.0:
            raise EventValidationError("change_threshold ต้องอยู่ระหว่าง 0.03–1.00")
        event["change_threshold"] = threshold
        event.pop("x", None)
        event.pop("y", None)
        event.pop("duration_ms", None)
        return event
    if action not in VALID_ACTIONS and original_type == "choice" and isinstance(event.get("options"), dict):
        choices = [(str(key).lower(), _number(value, f"weight {key}")) for key, value in event["options"].items()]
        choices = [(key, value) for key, value in choices if key in SAFE_ACTIONS and value > 0]
        if choices:
            action = max(choices, key=lambda item: item[1])[0]
            warnings.append("Required แบบ choice ถูกแปลงเป็น action ที่มีน้ำหนักสูงสุด")
    if action not in VALID_ACTIONS:
        raise EventValidationError("Required event ต้องมี action เป็น jump, slide, tap หรือ hold")
    if event.get("phase") == "post_game" and action not in {"tap", "hold"}:
        raise EventValidationError("Event หลังจบเกมรองรับเฉพาะ tap หรือ hold เพื่อไม่ให้ Jump/Slide หลุดหลังพบ XP")
    if original_type != "action":
        warnings.append("Required event ถูกบังคับ type เป็น action")
    if event.get("chance") != 100:
        warnings.append("Required event ถูกบังคับ Chance เป็น 100%")
    if event.get("jitter_ms") not in (None, 0, 0.0):
        warnings.append("Required event ถูกบังคับ Jitter เป็น 0 ms")
    event["event_class"] = "required"
    event["type"] = "action"
    event["action"] = action
    event["chance"] = 100
    event["jitter_ms"] = 0
    event.pop("options", None)
    event.pop("safe_zone_id", None)
    if action in {"tap", "hold"}:
        for axis in ("x", "y"):
            coordinate = int(round(_number(event.get(axis), f"Touch {axis.upper()}")))
            if coordinate < 0:
                raise EventValidationError(f"Touch {axis.upper()} ต้องไม่ติดลบ")
            event[axis] = coordinate
    else:
        event.pop("x", None)
        event.pop("y", None)
    if action in {"slide", "hold"}:
        duration = int(round(_number(event.get("duration_ms", 400), "Slide duration")))
        if duration <= 0:
            raise EventValidationError("ระยะเวลากดค้างต้องมากกว่า 0 ms")
        event["duration_ms"] = duration
    else:
        event.pop("duration_ms", None)
    return event


def _find_zone(event: dict[str, Any], zones: list[dict[str, Any]], at: float, warnings: list[str]) -> dict[str, Any]:
    zone_id = str(event.get("safe_zone_id", "")).strip()
    zone = next((item for item in zones if item["id"] == zone_id), None)
    if zone is None and not zone_id:
        matches = [item for item in zones if item["start"] <= at <= item["end"]]
        if len(matches) == 1:
            zone = matches[0]
            event["safe_zone_id"] = zone["id"]
            warnings.append(f"กำหนด Safe Zone {zone['id']} ให้อัตโนมัติ")
    if zone is None:
        raise EventValidationError("Safe Random ต้องอ้างถึง Safe Zone ที่มีอยู่")
    if not zone["start"] <= at <= zone["end"]:
        raise EventValidationError(f"Safe Random เวลา {at:.3f}s อยู่นอก Safe Zone {zone['id']}")
    return zone


def _convert_covered_required_event(
    event: dict[str, Any], zones: list[dict[str, Any]], warnings: list[str]
) -> dict[str, Any]:
    """Turn a synced obstacle covered by a Safe Zone into an editable random choice."""
    if str(event.get("event_class") or "required").lower() != "required":
        return event
    if str(event.get("phase", "synced")).lower() != "synced":
        return event
    if str(event.get("action", "")).lower() not in SAFE_ACTIONS:
        return event
    try:
        at = _number(event.get("at"), f"Event {event.get('id', '')}: at")
    except EventValidationError:
        return event
    zone = next((item for item in zones if item["start"] <= at <= item["end"]), None)
    if zone is None:
        return event

    converted = deepcopy(event)
    converted["event_class"] = "safe_random"
    converted["type"] = "choice"
    converted["options"] = deepcopy(DEFAULT_SAFE_RANDOM_OPTIONS)
    converted["safe_zone_id"] = zone["id"]
    converted["safe_zone_auto"] = True
    converted["safe_zone_source_action"] = str(event.get("action", "jump")).lower()
    if "duration_ms" in event:
        converted["safe_zone_source_duration_ms"] = int(event["duration_ms"])
    converted["jitter_ms"] = 0
    converted.setdefault("duration_ms", 400)
    converted.pop("action", None)
    converted.pop("chance", None)
    warnings.append(f"{converted.get('id', 'Event')}: แปลง Event ใน Safe Zone {zone['id']} เป็น Safe Random อัตโนมัติ")
    return converted


def _restore_uncovered_auto_safe_random(
    event: dict[str, Any], zones: list[dict[str, Any]], warnings: list[str],
) -> dict[str, Any]:
    """Restore only system-converted Safe Random events when no Zone covers them anymore."""
    if not event.get("safe_zone_auto"):
        return event
    try:
        at = _number(event.get("at"), f"Event {event.get('id', '')}: at")
    except EventValidationError:
        return event
    matching_zone = next((zone for zone in zones if zone["start"] <= at <= zone["end"]), None)
    if matching_zone is not None:
        event["safe_zone_id"] = matching_zone["id"]
        return event

    action = str(event.get("safe_zone_source_action", "")).lower()
    if action not in SAFE_ACTIONS:
        return event
    restored = deepcopy(event)
    restored["event_class"] = "required"
    restored["type"] = "action"
    restored["action"] = action
    restored["chance"] = 100
    restored["jitter_ms"] = 0
    source_duration = restored.get("safe_zone_source_duration_ms")
    if action == "slide" and source_duration is not None:
        restored["duration_ms"] = int(source_duration)
    else:
        restored.pop("duration_ms", None)
    for field in (
        "options", "safe_zone_id", "safe_zone_auto", "safe_zone_source_action",
        "safe_zone_source_duration_ms",
    ):
        restored.pop(field, None)
    warnings.append(f"{restored.get('id', 'Event')}: อยู่นอก Safe Zone แล้ว จึงกลับเป็น Required {action} อัตโนมัติ")
    return restored


def _expand_zones_around_covered_events(
    events: list[Any], zones: list[dict[str, Any]], min_gap_ms: int, warnings: list[str]
) -> None:
    """Absorb obstacle events at a zone edge so auto-random conversion stays valid."""
    obstacles: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("phase", "synced")).lower() != "synced":
            continue
        event_class = str(event.get("event_class") or "required").lower()
        action = str(event.get("action", "")).lower()
        if event_class == "required" and action in SAFE_ACTIONS:
            try:
                at = _number(event.get("at"), f"Event {event.get('id', '')}: at")
            except EventValidationError:
                continue
            obstacles.append({"event": event, "at": at, "duration_ms": int(event.get("duration_ms", 0) or 0)})

    for zone in zones:
        original_start, original_end = float(zone["start"]), float(zone["end"])
        included = [item for item in obstacles if original_start <= item["at"] <= original_end]
        if not included:
            continue
        changed = True
        while changed:
            changed = False
            # Random choices always allow Slide. Reserve the recorded duration,
            # or the 400 ms default used by converted Jump events.
            required_end = max(
                item["at"] + max(item["duration_ms"], 400 if str(item["event"].get("action", "")).lower() == "jump" else 0) / 1000
                for item in included
            )
            if required_end > zone["end"]:
                zone["end"] = round(required_end, 6)
                changed = True
            for item in obstacles:
                if item in included:
                    continue
                inside_expanded_zone = float(zone["start"]) <= item["at"] <= float(zone["end"])
                if inside_expanded_zone:
                    included.append(item)
                    zone["start"] = round(min(float(zone["start"]), item["at"]), 6)
                    zone["end"] = round(max(float(zone["end"]), item["at"]), 6)
                    changed = True
        if zone["start"] != original_start or zone["end"] != original_end:
            warnings.append(
                f"ขยาย Safe Zone {zone['id']} จาก {original_start:.3f}–{original_end:.3f}s "
                f"เป็น {zone['start']:.3f}–{zone['end']:.3f}s เพื่อรวม Event ที่ติดขอบอย่างปลอดภัย"
            )


def _normalize_safe_random(
    event: dict[str, Any],
    zones: list[dict[str, Any]],
    required_events: list[dict[str, Any]],
    min_gap_ms: int,
    warnings: list[str],
) -> dict[str, Any]:
    if event.get("phase", "synced") != "synced":
        raise EventValidationError("Safe Random ใช้ได้เฉพาะช่วงหลัง Sync")
    if not zones:
        raise EventValidationError("ยังไม่มี Safe Zone จึงสร้าง Safe Random ไม่ได้")
    at = float(event["at"])
    zone = _find_zone(event, zones, at, warnings)
    event_type = str(event.get("type", "choice")).lower()
    if event_type not in {"optional_action", "choice"}:
        raise EventValidationError("Safe Random ต้องใช้กฎสุ่ม jump, slide และ none")
    event["event_class"] = "safe_random"
    if event_type == "optional_action":
        # แปลงรูปแบบเก่าที่สุ่ม action เดียวตาม Chance ให้เป็นกฎสุ่มชุดใหม่
        # โดยผลลัพธ์ยังเหมือนเดิม: action = Chance, none = 100 - Chance.
        action = str(event.get("action", "")).lower()
        if action not in SAFE_ACTIONS:
            raise EventValidationError("Optional action ต้องเป็น jump หรือ slide")
        chance = int(round(_number(event.get("chance", 0), "Chance")))
        if not 0 <= chance <= 100:
            raise EventValidationError("Chance ต้องอยู่ระหว่าง 0–100")
        raw_options = {"none": 100 - chance, "jump": 0, "slide": 0}
        raw_options[action] = chance
        warnings.append("Safe Random แบบ Chance เดิมถูกแปลงเป็นกฎสุ่ม jump/slide/none")
    else:
        raw_options = event.get("options")
    if not isinstance(raw_options, dict):
        raise EventValidationError("Safe Random ต้องมีค่า chance สำหรับ jump, slide และ none")
    options: dict[str, float] = {}
    for action in ("none", "jump", "slide"):
        chance = _number(raw_options.get(action, 0), f"Chance {action}")
        if chance < 0:
            raise EventValidationError(f"Chance {action} ต้องไม่ติดลบ")
        options[action] = int(chance) if chance.is_integer() else chance
    unknown = set(raw_options) - set(options)
    if unknown:
        warnings.append(f"ตัดตัวเลือกที่ไม่รองรับ: {', '.join(sorted(map(str, unknown)))}")
    total_weight = sum(options.values())
    if total_weight <= 0:
        raise EventValidationError("Chance ของ jump, slide และ none ต้องมีอย่างน้อยหนึ่งค่าที่มากกว่า 0")
    if not math.isclose(total_weight, 100, rel_tol=0.0, abs_tol=1e-9):
        options = {
            action: round(weight * 100 / total_weight, 6)
            for action, weight in options.items()
        }
        # Make the displayed percentages add up to exactly 100 even after rounding.
        options["none"] = round(options["none"] + (100 - sum(options.values())), 6)
        warnings.append("ปรับ Chance แบบน้ำหนักให้เป็นเปอร์เซ็นต์รวม 100% อัตโนมัติ")
    event["type"] = "choice"
    event["options"] = options
    event.pop("action", None)
    event.pop("chance", None)
    slide_possible = options["slide"] > 0

    if slide_possible:
        duration = int(round(_number(event.get("duration_ms", 400), "Slide duration")))
        if duration <= 0:
            raise EventValidationError("Slide duration ต้องมากกว่า 0 ms")
        event["duration_ms"] = duration
    else:
        event.pop("duration_ms", None)

    jitter = int(round(_number(event.get("jitter_ms", 0), "Jitter")))
    if jitter < 0:
        raise EventValidationError("Jitter ต้องไม่ติดลบ")
    action_duration = duration / 1000 if slide_possible else 0.0
    if at + action_duration > zone["end"] + 1e-9:
        raise EventValidationError(
            f"Safe Random ที่ {at:.3f}s มี Slide ยาว {int(duration)} ms ซึ่งสิ้นสุดนอก Safe Zone {zone['id']}"
        )
    # Jitter is symmetric. Reserve enough room on the right for the complete
    # slide so a randomized action can never continue beyond the safe range.
    max_zone_jitter = max(
        0,
        int(math.floor(min(at - zone["start"], zone["end"] - at - action_duration) * 1000 + 1e-9)),
    )
    if jitter > max_zone_jitter:
        warnings.append(f"ลด Jitter จาก {jitter} เป็น {max_zone_jitter} ms เพื่อไม่ให้ออกนอก Safe Zone")
        jitter = max_zone_jitter
    event["jitter_ms"] = jitter
    event["safe_zone_id"] = zone["id"]

    effective_start = at - jitter / 1000
    effective_end = at + jitter / 1000
    gap_seconds = min_gap_ms / 1000
    for required in required_events:
        if required.get("phase", "synced") != "synced":
            continue
        required_at = float(required["at"])
        if not zone["start"] <= required_at <= zone["end"]:
            continue
        if effective_start - gap_seconds < required_at < effective_end + gap_seconds:
            raise EventValidationError(
                f"Safe Random ใกล้ Required ที่ {required_at:.3f}s เกินไป (ต้องห่างอย่างน้อย {min_gap_ms} ms)"
            )
    return event


def normalize_event(
    event: dict[str, Any],
    safe_zones: list[dict[str, Any]],
    required_events: list[dict[str, Any]],
    min_gap_ms: int,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(event, dict):
        raise EventValidationError("Event ต้องเป็น object")
    normalized = deepcopy(event)
    normalized.pop(EDITOR_VALIDATION_ERROR, None)
    warnings: list[str] = []
    normalized["id"] = str(normalized.get("id") or "event").strip()
    if "at" not in normalized:
        if "time" in normalized:
            normalized["at"] = normalized.pop("time")
            warnings.append("ย้ายฟิลด์ time เดิมมาเป็น at")
        elif "timestamp" in normalized:
            normalized["at"] = normalized.pop("timestamp")
            warnings.append("ย้ายฟิลด์ timestamp เดิมมาเป็น at")
    if str(normalized.get("type", "")).lower() in VALID_ACTIONS and not normalized.get("action"):
        normalized["action"] = str(normalized["type"]).lower()
        normalized["type"] = "action"
        warnings.append("แปลง type แบบเก่าเป็น action")
    at = _number(normalized.get("at"), f"Event {normalized['id']}: at")
    if at < 0:
        raise EventValidationError("เวลา Event ต้องไม่ติดลบ")
    normalized["at"] = round(at, 6)
    phase = str(normalized.get("phase", "synced")).lower()
    if phase not in {"pre_sync", "synced", "post_game"}:
        raise EventValidationError("phase ต้องเป็น pre_sync, synced หรือ post_game")
    normalized["phase"] = phase
    event_class = str(normalized.get("event_class") or "required").lower()
    if event_class == "required":
        return _normalize_required(normalized, warnings), warnings
    if event_class == "safe_random":
        return _normalize_safe_random(normalized, safe_zones, required_events, int(min_gap_ms), warnings), warnings
    raise EventValidationError("event_class ต้องเป็น required หรือ safe_random")


def normalize_sync(sync: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    result = deepcopy(DEFAULT_SYNC)
    result.update(deepcopy(sync or {}))
    warnings: list[str] = []
    mode = str(result.get("mode", "manual_f9"))
    mode = {"manual": "manual_f9", "auto": "auto_pause_icon", "pause_icon": "auto_pause_icon"}.get(mode, mode)
    if mode not in {"manual_f9", "auto_pause_icon"}:
        raise EventValidationError("Sync mode ไม่รองรับ")
    result["mode"] = mode
    limits = {
        "poll_ms": (20, 1000),
        "consecutive_matches": (1, 5),
        "timeout_seconds": (5, 120),
        # รุ่นเก่าเคยรับค่าติดลบ แม้ runtime จะทำย้อนหลังไม่ได้ คงช่วงรับไว้
        # เพื่อเปิดไฟล์เก่าได้ แล้ว normalize เป็น 0 ด้านล่าง.
        "offset_ms": (-500, 3000),
    }
    for field, (minimum, maximum) in limits.items():
        value = int(round(_number(result[field], field)))
        if value < minimum or value > maximum:
            raise EventValidationError(f"{field} ต้องอยู่ระหว่าง {minimum}–{maximum}")
        result[field] = value
    threshold = _number(result["threshold"], "threshold")
    if not 0.5 <= threshold <= 1:
        raise EventValidationError("threshold ต้องอยู่ระหว่าง 0.50–1.00")
    result["threshold"] = threshold
    result["manual_fallback"] = bool(result.get("manual_fallback", True))
    result["profile_name"] = str(result.get("profile_name", "")).strip()
    if result["offset_ms"] < 0:
        warnings.append("Delay รุ่นเก่าที่ติดลบถูกปรับเป็น 0 ms เพราะเริ่มย้อนหลังไม่ได้")
        result["offset_ms"] = 0
    roi = result.get("roi")
    if roi is not None:
        if not isinstance(roi, dict):
            raise EventValidationError("ROI ต้องเป็น object")
        result["roi"] = {key: int(round(_number(roi.get(key), f"ROI {key}"))) for key in ("x", "y", "width", "height")}
        if result["roi"]["width"] < 12 or result["roi"]["height"] < 12:
            raise EventValidationError("ROI ต้องมีขนาดอย่างน้อย 12×12 px")
    return result, warnings


def normalize_post_game(post_game: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    result = deepcopy(DEFAULT_POST_GAME)
    result.update(deepcopy(post_game or {}))
    warnings: list[str] = []
    result["enabled"] = bool(result.get("enabled", True))
    result["challenge_enabled"] = bool(result.get("challenge_enabled", True))
    if str(result.get("mode", "xp_result")) != "xp_result":
        raise EventValidationError("โหมดตรวจจบเกมรองรับ xp_result เท่านั้น")
    result["mode"] = "xp_result"
    result["template_path"] = str(result.get("template_path", "")).strip()
    limits = {
        "poll_ms": (400, 2000),
        "consecutive_matches": (1, 5),
        "min_gameplay_seconds": (0, 600),
        "timeout_seconds": (5, 300),
    }
    for field, (minimum, maximum) in limits.items():
        value = int(round(_number(result[field], f"post_game {field}")))
        if value < minimum or value > maximum:
            raise EventValidationError(f"post_game {field} ต้องอยู่ระหว่าง {minimum}–{maximum}")
        result[field] = value
    challenge_limits = {
        "challenge_first_click_delay_ms": (500, 5000),
        "challenge_inter_card_min_ms": (250, 5000),
        "challenge_inter_card_max_ms": (250, 5000),
        "challenge_next_round_min_ms": (1000, 10_000),
        "challenge_next_round_max_ms": (1000, 10_000),
        "challenge_transition_timeout_ms": (3000, 30_000),
    }
    for field, (minimum, maximum) in challenge_limits.items():
        value = int(round(_number(result[field], f"post_game {field}")))
        if value < minimum or value > maximum:
            raise EventValidationError(f"post_game {field} ต้องอยู่ระหว่าง {minimum}–{maximum}")
        result[field] = value
    if result["challenge_inter_card_min_ms"] > result["challenge_inter_card_max_ms"]:
        raise EventValidationError("ดีเลย์ระหว่างการ์ด Min ต้องไม่เกิน Max")
    if result["challenge_next_round_min_ms"] > result["challenge_next_round_max_ms"]:
        raise EventValidationError("ดีเลย์ก่อนรอบการ์ดถัดไป Min ต้องไม่เกิน Max")
    for field, minimum, maximum in (
        ("threshold", 0.5, 1.0),
        ("pause_absent_threshold", 0.3, 0.95),
    ):
        value = _number(result[field], f"post_game {field}")
        if not minimum <= value <= maximum:
            raise EventValidationError(f"post_game {field} ต้องอยู่ระหว่าง {minimum:.2f}–{maximum:.2f}")
        result[field] = value
    roi = result.get("roi")
    if not isinstance(roi, dict):
        raise EventValidationError("post_game ROI ต้องเป็น object")
    result["roi"] = {
        key: int(round(_number(roi.get(key), f"post_game ROI {key}")))
        for key in ("x", "y", "width", "height")
    }
    if result["roi"]["width"] < 12 or result["roi"]["height"] < 12:
        raise EventValidationError("post_game ROI ต้องมีขนาดอย่างน้อย 12×12 px")
    if result["consecutive_matches"] < 2:
        warnings.append("ตรวจ XP เพียง 1 เฟรมเสี่ยง false-positive; แนะนำ 3 เฟรม")
    return result, warnings


def normalize_pattern(pattern: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(pattern, dict):
        raise EventValidationError("Pattern ต้องเป็น JSON object")
    result = deepcopy(pattern)
    warnings: list[str] = []
    old_version = int(result.get("schema_version", 1))
    if old_version < 2:
        warnings.append(f"อัปเกรด schema v{old_version} เป็น v2")
    result["schema_version"] = 2
    result["name"] = str(result.get("name") or "untitled").strip()
    result.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    if not isinstance(result.get("device"), dict):
        result["device"] = {}
    result["device"].setdefault("preferred_serial", "")
    if not isinstance(result["device"].get("resolution"), dict):
        result["device"]["resolution"] = {}
    result["device"]["resolution"].setdefault("width", 1280)
    result["device"]["resolution"].setdefault("height", 720)
    if not isinstance(result.get("controls"), dict):
        result["controls"] = {}
    for action, default in (("jump", {"x": 160, "y": 635}), ("slide", {"x": 1115, "y": 635})):
        if not isinstance(result["controls"].get(action), dict):
            result["controls"][action] = {}
        result["controls"][action].setdefault("x", default["x"])
        result["controls"][action].setdefault("y", default["y"])
    if not isinstance(result.get("safety"), dict):
        result["safety"] = {}
    min_gap = int(round(_number(result["safety"].get("safe_random_min_gap_ms", 250), "safe_random_min_gap_ms")))
    if min_gap < 0:
        raise EventValidationError("safe_random_min_gap_ms ต้องไม่ติดลบ")
    result["safety"]["safe_random_min_gap_ms"] = min_gap
    if not isinstance(result.get("stats"), dict):
        result["stats"] = {}
    play_count = int(round(_number(result["stats"].get("play_count", 0), "play_count")))
    if play_count < 0:
        play_count = 0
        warnings.append("แก้ play_count ที่ติดลบเป็น 0")
    result["stats"]["play_count"] = play_count
    result["stats"]["last_played_at"] = str(result["stats"].get("last_played_at", ""))
    if not isinstance(result.get("recording"), dict):
        result["recording"] = {}
    result["recording"]["rapid_tap_to_hold"] = bool(result["recording"].get("rapid_tap_to_hold", True))
    rapid_gap = int(round(_number(result["recording"].get("rapid_tap_gap_ms", 180), "rapid_tap_gap_ms")))
    result["recording"]["rapid_tap_gap_ms"] = max(50, min(1000, rapid_gap))
    if not isinstance(result.get("playback"), dict):
        result["playback"] = {}
    repeat_count = int(round(_number(result["playback"].get("repeat_count", 1), "repeat_count")))
    loop_interval = int(round(_number(result["playback"].get("loop_interval_ms", 1000), "loop_interval_ms")))
    pre_sync_extra = int(round(_number(result["playback"].get("pre_sync_extra_ms", 0), "pre_sync_extra_ms")))
    result["playback"]["repeat_count"] = max(1, min(999, repeat_count))
    result["playback"]["loop_interval_ms"] = max(0, min(600_000, loop_interval))
    result["playback"]["pre_sync_extra_ms"] = max(0, min(600_000, pre_sync_extra))
    delay_mode = str(result["playback"].get("pre_sync_delay_mode", "fixed")).strip().lower()
    if delay_mode not in {"fixed", "random"}:
        raise EventValidationError("โหมดเวลาก่อน Sync ต้องเป็น fixed หรือ random")
    result["playback"]["pre_sync_delay_mode"] = delay_mode
    random_min = int(round(_number(result["playback"].get("pre_sync_random_min_ms", 300), "pre_sync_random_min_ms")))
    random_max = int(round(_number(result["playback"].get("pre_sync_random_max_ms", 500), "pre_sync_random_max_ms")))
    random_delta = int(round(_number(result["playback"].get("pre_sync_random_min_delta_ms", 50), "pre_sync_random_min_delta_ms")))
    random_history = int(round(_number(result["playback"].get("pre_sync_random_history", 3), "pre_sync_random_history")))
    if not 0 <= random_min <= random_max <= 600_000:
        raise EventValidationError("ช่วง Random ก่อน Sync ต้องเป็น 0–600000 ms และ Min ต้องไม่เกิน Max")
    if not 0 <= random_delta <= 60_000:
        raise EventValidationError("ระยะห่าง Random ก่อน Sync ต้องอยู่ระหว่าง 0–60000 ms")
    if not 1 <= random_history <= 20:
        raise EventValidationError("จำนวนค่าก่อนหน้าที่ใช้กันซ้ำต้องอยู่ระหว่าง 1–20")
    if delay_mode == "random" and random_min == random_max:
        warnings.append("ช่วง Random ก่อน Sync มีค่าเดียว จึงทำงานเหมือน Fixed")
    if (
        delay_mode == "random" and random_min != random_max and random_delta > 0
        and random_max - random_min < random_delta * random_history
    ):
        raise EventValidationError(
            "ช่วง Random ก่อน Sync แคบเกินไป: Max-Min ต้องไม่น้อยกว่า ระยะห่าง × จำนวนย้อนหลัง"
        )
    result["playback"]["pre_sync_random_min_ms"] = random_min
    result["playback"]["pre_sync_random_max_ms"] = random_max
    result["playback"]["pre_sync_random_min_delta_ms"] = random_delta
    result["playback"]["pre_sync_random_history"] = random_history
    result["playback"]["loop_forever"] = bool(result["playback"].get("loop_forever", False))
    result["playback"]["sync_each_loop"] = bool(result["playback"].get("sync_each_loop", True))
    zones, zone_warnings = normalize_safe_zones(result.get("safe_zones", []))
    zones, zone_aliases, merge_warnings = merge_safe_zones(zones)
    result["safe_zones"] = zones
    warnings.extend(zone_warnings)
    warnings.extend(merge_warnings)
    result["sync"], sync_warnings = normalize_sync(result.get("sync"))
    warnings.extend(sync_warnings)
    result["post_game"], post_game_warnings = normalize_post_game(result.get("post_game"))
    warnings.extend(post_game_warnings)

    raw_events = result.get("events", [])
    if not isinstance(raw_events, list):
        raise EventValidationError("events ต้องเป็น list")
    prepared_events: list[Any] = []
    for index, raw_event in enumerate(raw_events, start=1):
        if not isinstance(raw_event, dict):
            prepared_events.append(raw_event)
            continue
        prepared = deepcopy(raw_event)
        prepared.setdefault("id", f"evt_{index:04d}")
        if prepared.get("safe_zone_id") in zone_aliases:
            prepared["safe_zone_id"] = zone_aliases[prepared["safe_zone_id"]]
        prepared_events.append(prepared)
    prepared_events = [
        _restore_uncovered_auto_safe_random(event, zones, warnings)
        if isinstance(event, dict) else event
        for event in prepared_events
    ]
    _expand_zones_around_covered_events(prepared_events, zones, min_gap, warnings)
    prepared_events = [_convert_covered_required_event(event, zones, warnings) for event in prepared_events]
    required: list[dict[str, Any]] = []
    safe_random_raw: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw_event in prepared_events:
        event_class = str(raw_event.get("event_class") or "required").lower() if isinstance(raw_event, dict) else "required"
        if event_class == "required":
            normalized, event_warnings = normalize_event(raw_event, zones, [], min_gap)
            if normalized["id"] in ids:
                raise EventValidationError(f"Event ID ซ้ำ: {normalized['id']}")
            ids.add(normalized["id"])
            required.append(normalized)
            warnings.extend(f"{normalized['id']}: {message}" for message in event_warnings)
        else:
            safe_random_raw.append(raw_event)
    normalized_events = required[:]
    for raw_event in safe_random_raw:
        normalized, event_warnings = normalize_event(raw_event, zones, required, min_gap)
        if normalized["id"] in ids:
            raise EventValidationError(f"Event ID ซ้ำ: {normalized['id']}")
        ids.add(normalized["id"])
        normalized_events.append(normalized)
        warnings.extend(f"{normalized['id']}: {message}" for message in event_warnings)
    result["events"] = sorted(normalized_events, key=lambda item: (item["at"], item["id"]))
    adaptive_events = [event for event in result["events"] if event.get("type") == ADAPTIVE_WAIT_TYPE]
    if len(adaptive_events) > 1:
        raise EventValidationError("หนึ่ง Pattern มี Event รอสุ่มเสร็จ → กด Play ได้เพียง 1 Event")
    if adaptive_events:
        adaptive = adaptive_events[0]
        pre_sync_events = [
            event for event in result["events"]
            if event.get("phase") == "pre_sync"
        ]
        adaptive_index = pre_sync_events.index(adaptive)
        following = pre_sync_events[adaptive_index + 1:]
        if len(following) != 1:
            raise EventValidationError(
                "หลัง Event Adaptive ต้องเหลือ Pre Sync อีก 1 Event เท่านั้น คือ Tap กด Play ตัวสุดท้าย"
            )
        final_play = following[0]
        if final_play.get("type") != "action" or final_play.get("action") != "tap":
            raise EventValidationError("Event สุดท้ายหลัง Adaptive ต้องเป็น Required Tap กด Play")
        if float(final_play["at"]) <= float(adaptive["at"]):
            raise EventValidationError("Event กด Play ต้องอยู่หลังเวลาเริ่ม Adaptive")
    return result, warnings


def normalize_pattern_for_editing(pattern: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Load a structurally valid pattern even when individual events need repair.

    Playback and saving still use :func:`normalize_pattern`. This editor-only
    path keeps invalid events visible so the user can fix or delete them.
    """
    strict_message = ""
    try:
        return normalize_pattern(pattern)
    except EventValidationError as exc:
        strict_message = str(exc)

    if not isinstance(pattern, dict):
        raise EventValidationError("Pattern ต้องเป็น JSON object")
    raw_events = pattern.get("events", [])
    if not isinstance(raw_events, list):
        raise EventValidationError("events ต้องเป็น list")

    base = deepcopy(pattern)
    base["events"] = []
    editable, warnings = normalize_pattern(base)
    zones = editable["safe_zones"]
    min_gap = int(editable["safety"]["safe_random_min_gap_ms"])
    used_ids: set[str] = set()
    prepared: list[dict[str, Any]] = []

    for index, raw_event in enumerate(raw_events, start=1):
        if isinstance(raw_event, dict):
            event = deepcopy(raw_event)
        else:
            event = {"at": 0, "phase": "synced", "event_class": "required", "type": "action", "action": "jump"}
            event[EDITOR_VALIDATION_ERROR] = f"Event ลำดับ {index} ต้องเป็น object"
        event_id = str(event.get("id") or f"evt_{index:04d}").strip()
        if event_id in used_ids:
            original_id = event_id
            suffix = 2
            while f"{original_id}__ซ้ำ_{suffix}" in used_ids:
                suffix += 1
            event_id = f"{original_id}__ซ้ำ_{suffix}"
            event[EDITOR_VALIDATION_ERROR] = f"Event ID ซ้ำ: {original_id} • กรุณาตั้ง ID ใหม่"
        event["id"] = event_id
        used_ids.add(event_id)
        event.setdefault("at", 0)
        event.setdefault("phase", "synced")
        event.setdefault("event_class", "required")
        event.setdefault("type", "action" if event.get("event_class") == "required" else "choice")
        prepared.append(event)

    # The strict path performs these reversible Safe Zone transitions before
    # validating individual events.  Repair mode must do the same; otherwise a
    # single unrelated invalid event leaves auto-converted choices stranded as
    # invalid Safe Random events after their Zone is moved away.
    prepared = [
        _restore_uncovered_auto_safe_random(event, zones, warnings)
        for event in prepared
    ]
    _expand_zones_around_covered_events(prepared, zones, min_gap, warnings)
    prepared = [
        _convert_covered_required_event(event, zones, warnings)
        for event in prepared
    ]

    valid_required: list[dict[str, Any]] = []
    editable_events: list[dict[str, Any] | None] = [None] * len(prepared)
    for index, event in enumerate(prepared):
        if str(event.get("event_class", "required")).lower() != "required":
            continue
        existing_error = str(event.get(EDITOR_VALIDATION_ERROR, "")).strip()
        try:
            normalized, event_warnings = normalize_event(event, zones, [], min_gap)
        except EventValidationError as exc:
            event[EDITOR_VALIDATION_ERROR] = existing_error or str(exc)
            editable_events[index] = event
        else:
            editable_events[index] = normalized
            valid_required.append(normalized)
            warnings.extend(f"{normalized['id']}: {message}" for message in event_warnings)

    for index, event in enumerate(prepared):
        if editable_events[index] is not None:
            continue
        existing_error = str(event.get(EDITOR_VALIDATION_ERROR, "")).strip()
        try:
            normalized, event_warnings = normalize_event(event, zones, valid_required, min_gap)
        except EventValidationError as exc:
            event[EDITOR_VALIDATION_ERROR] = existing_error or str(exc)
            editable_events[index] = event
        else:
            editable_events[index] = normalized
            warnings.extend(f"{normalized['id']}: {message}" for message in event_warnings)

    editable["events"] = [event for event in editable_events if event is not None]
    if (
        strict_message.startswith("Event รอสุ่มเสร็จ")
        or strict_message.startswith("หนึ่ง Pattern มี Event รอสุ่มเสร็จ")
        or strict_message.startswith("หลัง Event Adaptive")
        or strict_message.startswith("Event สุดท้ายหลัง Adaptive")
        or strict_message.startswith("Event กด Play ต้องอยู่หลัง")
    ):
        target = next(
            (event for event in editable["events"] if event.get("type") == ADAPTIVE_WAIT_TYPE),
            None,
        )
        if target is not None and not target.get(EDITOR_VALIDATION_ERROR):
            target[EDITOR_VALIDATION_ERROR] = strict_message
    errors = editable_pattern_errors(editable)
    warnings.insert(0, f"เปิดโหมดซ่อม Pattern: พบ Event ต้องแก้ {len(errors)} จุด • {strict_message}")
    return editable, warnings
