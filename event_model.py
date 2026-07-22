from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable


SAFE_ACTIONS = {"jump", "slide"}
VALID_ACTIONS = {"jump", "slide", "tap", "hold"}
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
}


class EventValidationError(ValueError):
    pass


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


def _normalize_required(event: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    original_type = event.get("type")
    action = str(event.get("action", "")).lower()
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
    return result, warnings
