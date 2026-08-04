from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from copy import deepcopy
from pathlib import Path, PurePath
from typing import Any

from event_model import EDITOR_VALIDATION_ERROR, EventValidationError, editable_pattern_errors, normalize_pattern, normalize_pattern_for_editing


class PatternStoreError(RuntimeError):
    pass


def safe_pattern_filename(name: str) -> str:
    value = re.sub(r"[^\w\-. ]+", "_", name.strip(), flags=re.UNICODE)
    value = value.rstrip(". ").replace(" ", "_")
    return value or "untitled"


class PatternStore:
    def __init__(self, project_dir: str | Path = ".") -> None:
        self.project_dir = Path(project_dir).resolve()
        self.patterns_dir = self.project_dir / "patterns"
        self.templates_dir = self.project_dir / "templates"
        self.patterns_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def list_patterns(self) -> list[str]:
        return sorted(path.stem for path in self.patterns_dir.glob("*.json") if path.is_file())

    def _available_name(self, requested: str) -> str:
        base = safe_pattern_filename(requested)
        if not self.path_for(base).exists():
            return base
        index = 2
        while self.path_for(f"{base}_{index}").exists():
            index += 1
        return f"{base}_{index}"

    def path_for(self, name: str) -> Path:
        return self.patterns_dir / f"{safe_pattern_filename(name)}.json"

    def _template_paths(self, pattern: dict[str, Any]) -> list[tuple[str, str]]:
        return [
            ("Pause", str(pattern.get("sync", {}).get("template_path", ""))),
            ("Result XP", str(pattern.get("post_game", {}).get("template_path", ""))),
        ]

    def _validate_template_path(self, pattern: dict[str, Any]) -> None:
        for label, template in self._template_paths(pattern):
            if not template:
                continue
            pure = PurePath(template)
            if pure.is_absolute() or re.match(r"^[A-Za-z]:", template) or template.startswith(("/", "\\")):
                raise PatternStoreError(f"{label} template_path ต้องเป็น relative path จากโฟลเดอร์โปรเจกต์")
            resolved = (self.project_dir / template).resolve()
            try:
                resolved.relative_to(self.project_dir)
            except ValueError as exc:
                raise PatternStoreError(f"{label} template_path ต้องอยู่ภายในโฟลเดอร์โปรเจกต์") from exc

    def _read_pattern_file(self, name_or_path: str | Path) -> tuple[Path, Any]:
        candidate = Path(name_or_path)
        path = candidate if candidate.suffix.lower() == ".json" else self.path_for(str(name_or_path))
        if not path.is_absolute():
            path = self.project_dir / path
        if not path.is_file():
            raise PatternStoreError(f"ไม่พบ Pattern: {path.name}")
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PatternStoreError(f"ไฟล์ JSON เสียหายที่บรรทัด {exc.lineno}: {exc.msg}") from exc
        except OSError as exc:
            raise PatternStoreError(f"อ่าน Pattern ไม่สำเร็จ: {exc}") from exc

    def load(self, name_or_path: str | Path, *, migrate: bool = True) -> tuple[dict[str, Any], list[str]]:
        path, raw = self._read_pattern_file(name_or_path)
        old_version = int(raw.get("schema_version", 1)) if isinstance(raw, dict) else 1
        try:
            normalized, warnings = normalize_pattern(raw)
        except (EventValidationError, TypeError, ValueError) as exc:
            raise PatternStoreError(f"Pattern ไม่ถูกต้อง: {exc}") from exc
        self._validate_template_path(normalized)
        if migrate and old_version < 2:
            backup_path = path.with_suffix(path.suffix + ".bak")
            if not backup_path.exists():
                shutil.copy2(path, backup_path)
            self._atomic_write(path, normalized)
            warnings.append(f"สำรองไฟล์เดิมไว้ที่ {backup_path.name}")
        return normalized, warnings

    def load_for_editing(self, name_or_path: str | Path) -> tuple[dict[str, Any], list[str]]:
        """Open event-level validation failures without weakening strict save/play."""
        path, raw = self._read_pattern_file(name_or_path)
        try:
            editable, warnings = normalize_pattern_for_editing(raw)
        except (EventValidationError, TypeError, ValueError) as exc:
            raise PatternStoreError(f"Pattern เปิดเพื่อแก้ไขไม่ได้: {exc}") from exc
        self._validate_template_path(editable)
        if not editable_pattern_errors(editable):
            return self.load(name_or_path)
        return editable, warnings

    def save(self, pattern: dict[str, Any], name: str | None = None) -> tuple[Path, list[str]]:
        try:
            normalized, warnings = normalize_pattern(pattern)
        except (EventValidationError, TypeError, ValueError) as exc:
            raise PatternStoreError(f"บันทึกไม่ได้: {exc}") from exc
        self._validate_template_path(normalized)
        if name:
            normalized["name"] = name.strip() or normalized["name"]
        path = self.path_for(normalized["name"])
        self._atomic_write(path, normalized)
        return path, warnings

    def save_for_editing(self, pattern: dict[str, Any], name: str | None = None) -> tuple[Path, list[str]]:
        """Atomically save repair progress while strict playback remains blocked."""
        editable, warnings = normalize_pattern_for_editing(pattern)
        errors = editable_pattern_errors(editable)
        candidate = deepcopy(editable)
        for event in candidate.get("events", []):
            if isinstance(event, dict):
                event.pop(EDITOR_VALIDATION_ERROR, None)
        self._validate_template_path(candidate)
        if name:
            candidate["name"] = name.strip() or candidate["name"]
        path = self.path_for(candidate["name"])
        backup_path = path.with_suffix(path.suffix + ".repair.bak")
        if path.exists() and not backup_path.exists():
            try:
                shutil.copy2(path, backup_path)
            except OSError as exc:
                raise PatternStoreError(f"สำรอง Pattern ก่อนซ่อมไม่สำเร็จ: {exc}") from exc
            warnings.append(f"สำรองไฟล์ก่อนซ่อมไว้ที่ {backup_path.name}")
        self._atomic_write(path, candidate)
        if errors:
            warnings.append(f"บันทึกความคืบหน้าโหมดซ่อมแล้ว • ยังเล่นไม่ได้จนกว่าจะแก้ครบ {len(errors)} Event")
        return path, warnings

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PatternStoreError(f"บันทึก Pattern แบบ atomic ไม่สำเร็จ: {exc}") from exc

    def delete(self, name: str) -> None:
        path = self.path_for(name)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise PatternStoreError(f"ไม่พบ Pattern: {name}") from exc

    def duplicate(self, source_name: str, new_name: str) -> Path:
        pattern, _ = self.load(source_name)
        duplicate = deepcopy(pattern)
        duplicate["name"] = new_name
        return self.save(duplicate)[0]

    def export_pattern(self, name: str, destination: str | Path) -> Path:
        pattern, _ = self.load(name)
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.suffix.lower() == ".json":
            self._atomic_write(destination_path, pattern)
            return destination_path
        if destination_path.suffix.lower() != ".zip":
            destination_path = destination_path.with_suffix(".zip")
        temp_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
        try:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("pattern.json", json.dumps(pattern, ensure_ascii=False, indent=2) + "\n")
                for label, template in self._template_paths(pattern):
                    if not template:
                        continue
                    template_path = (self.project_dir / template).resolve()
                    if template_path.is_file():
                        bundle.write(template_path, f"templates/{template_path.name}")
            os.replace(temp_path, destination_path)
        except (OSError, zipfile.BadZipFile) as exc:
            temp_path.unlink(missing_ok=True)
            raise PatternStoreError(f"Export Pattern ไม่สำเร็จ: {exc}") from exc
        return destination_path

    def import_pattern(self, source: str | Path) -> tuple[Path, list[str]]:
        source_path = Path(source)
        if not source_path.is_file():
            raise PatternStoreError(f"ไม่พบไฟล์ Import: {source_path}")
        warnings: list[str] = []
        bundled_templates: dict[str, bytes] = {}
        try:
            if source_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(source_path, "r") as bundle:
                    names = bundle.namelist()
                    pattern_entry = next((item for item in names if PurePath(item).name == "pattern.json"), None)
                    if not pattern_entry:
                        raise PatternStoreError("ZIP ไม่มี pattern.json")
                    raw = json.loads(bundle.read(pattern_entry).decode("utf-8"))
                    for template_entry in (
                        item for item in names if item.startswith("templates/") and PurePath(item).suffix.lower() == ".png"
                    ):
                        bundled_templates[PurePath(template_entry).name] = bundle.read(template_entry)
            elif source_path.suffix.lower() == ".json":
                raw = json.loads(source_path.read_text(encoding="utf-8"))
            else:
                raise PatternStoreError("รองรับ Import เฉพาะ .json และ .zip")
        except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile, OSError) as exc:
            raise PatternStoreError(f"Import Pattern ไม่สำเร็จ: {exc}") from exc
        try:
            pattern, normalize_warnings = normalize_pattern(raw)
        except (EventValidationError, TypeError, ValueError) as exc:
            raise PatternStoreError(f"Pattern ที่ Import ไม่ถูกต้อง: {exc}") from exc
        warnings.extend(normalize_warnings)
        original_name = pattern["name"]
        pattern["name"] = self._available_name(original_name)
        if pattern["name"] != original_name:
            warnings.append(f"ชื่อซ้ำ จึงบันทึกเป็น {pattern['name']}")
        for section, suffix in (("sync", "pause"), ("post_game", "result_xp")):
            source_name = PurePath(str(pattern.get(section, {}).get("template_path", ""))).name
            bundled_template = bundled_templates.get(source_name)
            if bundled_template is None:
                continue
            target_name = f"{safe_pattern_filename(pattern['name'])}_{suffix}.png"
            target = self.templates_dir / target_name
            temp = target.with_suffix(".png.tmp")
            try:
                with temp.open("wb") as handle:
                    handle.write(bundled_template)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, target)
            except OSError as exc:
                temp.unlink(missing_ok=True)
                raise PatternStoreError(f"บันทึก {suffix} template จาก ZIP ไม่สำเร็จ: {exc}") from exc
            pattern[section]["template_path"] = f"templates/{target_name}"
        path, save_warnings = self.save(pattern)
        warnings.extend(save_warnings)
        return path, warnings

    def rename(self, old_name: str, new_name: str) -> Path:
        pattern, _ = self.load(old_name)
        old_path = self.path_for(old_name)
        pattern["name"] = new_name
        new_path, _ = self.save(pattern)
        if old_path.resolve() != new_path.resolve():
            old_path.unlink(missing_ok=True)
        return new_path
