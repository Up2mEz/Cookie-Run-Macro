from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path, PurePath
from typing import Any


PAUSE_TEMPLATE_SIZE = (62, 62)
DEFAULT_PAUSE_PROFILE = "ค่าเริ่มต้น 1280×720"
DEFAULT_PAUSE_TEMPLATE = "templates/default_pause_1280x720.png"
DEFAULT_PAUSE_ROI = {"x": 1165, "y": 5, "width": 62, "height": 62}


class PauseProfileError(RuntimeError):
    pass


class PauseProfileStore:
    """คลัง Pause template ที่แชร์ระหว่าง Pattern แต่เลือกแยกตาม Stage ได้."""

    def __init__(self, project_dir: str | Path = ".") -> None:
        self.project_dir = Path(project_dir).resolve()
        self.path = self.project_dir / "pause_profiles.json"

    @staticmethod
    def default_profile() -> dict[str, Any]:
        return {
            "name": DEFAULT_PAUSE_PROFILE,
            "template_path": DEFAULT_PAUSE_TEMPLATE,
            "roi": deepcopy(DEFAULT_PAUSE_ROI),
            "resolution": {"width": 1280, "height": 720},
        }

    def load(self) -> list[dict[str, Any]]:
        profiles = [self.default_profile()]
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PauseProfileError(f"อ่านคลัง Pause Stage ไม่สำเร็จ: {exc}") from exc
            entries = raw.get("profiles", []) if isinstance(raw, dict) else []
            if not isinstance(entries, list):
                raise PauseProfileError("pause_profiles.json ต้องมี profiles เป็นรายการ")
            profiles.extend(entries)
        result: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            normalized = self._normalize(profile)
            result[normalized["name"]] = normalized
        # Default ในตัวโปรแกรมต้องกู้คืนได้เสมอ แม้ไฟล์ภายนอกชื่อชนกัน
        result[DEFAULT_PAUSE_PROFILE] = self.default_profile()
        return sorted(result.values(), key=lambda item: (item["name"] != DEFAULT_PAUSE_PROFILE, item["name"].casefold()))

    def names(self) -> list[str]:
        return [profile["name"] for profile in self.load()]

    def get(self, name: str) -> dict[str, Any]:
        for profile in self.load():
            if profile["name"] == name:
                return deepcopy(profile)
        raise PauseProfileError(f"ไม่พบ Pause Stage: {name}")

    def upsert(self, profile: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(profile)
        if normalized["name"] == DEFAULT_PAUSE_PROFILE:
            raise PauseProfileError("Template ค่าเริ่มต้นแก้ทับไม่ได้ กรุณาตั้งชื่อ Stage ใหม่")
        profiles = {
            item["name"]: item for item in self.load()
            if item["name"] != DEFAULT_PAUSE_PROFILE
        }
        profiles[normalized["name"]] = normalized
        payload = {"schema_version": 1, "profiles": sorted(profiles.values(), key=lambda item: item["name"].casefold())}
        temp = self.path.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise PauseProfileError(f"บันทึกคลัง Pause Stage ไม่สำเร็จ: {exc}") from exc
        return deepcopy(normalized)

    def _normalize(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile, dict):
            raise PauseProfileError("ข้อมูล Pause Stage ต้องเป็น object")
        name = str(profile.get("name", "")).strip()
        if not name:
            raise PauseProfileError("กรุณาตั้งชื่อ Stage")
        template_path = str(profile.get("template_path", "")).strip().replace("\\", "/")
        pure = PurePath(template_path)
        if not template_path or pure.is_absolute() or ".." in pure.parts:
            raise PauseProfileError("Pause template ต้องเป็น path ภายในโปรเจกต์")
        roi = profile.get("roi")
        if not isinstance(roi, dict):
            raise PauseProfileError("Pause Stage ต้องมี ROI")
        normalized_roi = {key: int(roi.get(key, -1)) for key in ("x", "y", "width", "height")}
        if (normalized_roi["width"], normalized_roi["height"]) != PAUSE_TEMPLATE_SIZE:
            raise PauseProfileError("Pause Template ทุก Stage ต้องมีขนาด 62×62 px")
        if normalized_roi["x"] < 0 or normalized_roi["y"] < 0:
            raise PauseProfileError("Pause ROI อยู่นอกภาพ")
        resolution = profile.get("resolution", {})
        width, height = int(resolution.get("width", 1280)), int(resolution.get("height", 720))
        if width <= 0 or height <= 0 or normalized_roi["x"] + 62 > width or normalized_roi["y"] + 62 > height:
            raise PauseProfileError("Pause ROI เกินความละเอียดของ Stage")
        return {
            "name": name,
            "template_path": template_path,
            "roi": normalized_roi,
            "resolution": {"width": width, "height": height},
        }
