from __future__ import annotations

import base64
import os
from pathlib import Path

from PIL import Image


DEFAULT_RESULT_TEMPLATE = "templates/default_result_xp_1280x720.png"
DEFAULT_RESULT_ROI = {"x": 155, "y": 450, "width": 130, "height": 75}

# Grayscale 32×32 crop ของคำว่า XP จากภาพ Result ที่ผู้ใช้ให้มา เก็บขนาดเล็ก
# และ materialize เป็น PNG ครั้งแรกที่เปิดโปรแกรม ผู้ใช้ Capture ใหม่ต่อ Pattern ได้.
_RESULT_XP_32_BASE64 = (
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/u7u/v7u/u7+7u7+/v7+/v7+/v7+/v7+/v7+/v7+/v7/Px7u/z8fXw"
    "9PPu7+/v7+/v7+/v7+/v7+/v7+/v7+70xL753q/sr4aS1fPu7+/v7+/v7+/v7+/v7+/v7+/v7/FtbvuyR71nQUFp4PLu7+/v7+/v"
    "7+/v7+/v7+/v7+/t9ppKyHth2mZ9j0ez+O3v7+/v7+/v7+/v7+/v7+/v7+7011ZrTqHzXabyXJ/67e/v7+/v7+/v7+/v7+/v7+/v"
    "7+31jUdd2e9gp/BYpfrt7+/v7+/v7+/v7+/v7+/v7+/v7fi/RoT262aIj0jF9u3v7+/v7+/v7+/v7+/v7+/v7+/t955GaePvb0pF"
    "ge3w7+/v7+/v7+/v7+/v7+/v7+/v7vLiY1lMsPhuXZPe8+7v7+/v7+/v7+/v7+/v7+/v7+/t+a5Hr2pv82yW/PDu7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/ua2LzpELIcJT67O/v7+/v7+/v7+/v7+/v7+/v7+/v7/GJn/3UcteJpPjt7+/v7+/v7+/v7+/v7+/v7+/v7+/u"
    "8ujl8e3j8+To8O/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v8PHv7/Hu8fDv7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/u7u/v7u/u7u/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v"
    "7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7w=="
)


def ensure_builtin_assets(project_dir: str | Path) -> Path:
    target = Path(project_dir).resolve() / DEFAULT_RESULT_TEMPLATE
    pixels = base64.b64decode(_RESULT_XP_32_BASE64)
    if len(pixels) != 32 * 32:
        raise RuntimeError("Built-in XP template เสียหาย")
    if target.is_file():
        try:
            with Image.open(target) as existing:
                if existing.convert("L").resize((32, 32)).tobytes() == pixels:
                    return target
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.frombytes("L", (32, 32), pixels)
    temp = target.with_suffix(".png.tmp")
    image.save(temp, format="PNG")
    os.replace(temp, target)
    return target
