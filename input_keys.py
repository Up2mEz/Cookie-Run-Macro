from __future__ import annotations


VK_TO_ACTION_KEY = {0x4A: "j", 0x4B: "k"}


def recorder_key_name(key: object) -> str | None:
    """อ่าน J/K ได้ทั้ง key.char และ Windows virtual-key ที่ MuMu มักส่งมา."""
    char = getattr(key, "char", None)
    if isinstance(char, str) and char.lower() in {"j", "k"}:
        return char.lower()
    vk = getattr(key, "vk", None)
    try:
        return VK_TO_ACTION_KEY.get(int(vk))
    except (TypeError, ValueError):
        return None
