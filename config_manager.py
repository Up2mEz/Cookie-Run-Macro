from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "adb_path": "",
    "candidate_ports": [16416],
    "selected_serial": "",
    "last_pattern": "",
    "expected_resolution": {"width": 1280, "height": 720},
    "window": {"width": 1180, "height": 820},
}


class ConfigManager:
    def __init__(self, path: str | Path = "config.json") -> None:
        self.path = Path(path)
        self.data = deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> dict[str, Any]:
        self.data = deepcopy(DEFAULT_CONFIG)
        if not self.path.exists():
            return self.data
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.data
        if isinstance(loaded, dict):
            self.data.update(loaded)
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.path)
