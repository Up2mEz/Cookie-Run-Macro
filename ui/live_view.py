from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk

from adb_manager import ADBError, ADBManager, PersistentShell


class LiveView(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        adb_path: str,
        serial: str,
        *,
        on_interaction: Callable[[int, int, float, int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        poll_ms: int = 160,
    ) -> None:
        super().__init__(parent)
        self.title(f"MuMu Live — {serial}")
        self.geometry("1000x650")
        self.minsize(720, 480)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.adb = ADBManager(adb_path)
        self.serial = serial
        self.on_interaction = on_interaction
        self.on_error = on_error
        self.poll_ms = max(120, int(poll_ms))
        self.stop_event = threading.Event()
        self.shell: PersistentShell | None = None
        self.last_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.display_box = (0, 0, 1, 1)
        self.press_native: tuple[int, int] | None = None
        self.press_time = 0.0
        self.measured_fps = 0.0
        self._last_frame_at = 0.0

        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="MuMu Preview (ตั้งพิกัด/ตรวจภาพ)", font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Label(
            toolbar,
            text="คลิก = Tap • กดค้าง = Hold • ลาก = Swipe",
            foreground="#4b5563",
        ).pack(side="left", padx=16)
        ttk.Button(toolbar, text="ปิด Preview", command=self.close).pack(side="right")
        self.live_var = tk.StringVar(value="กำลังเชื่อมต่อ…")
        self.status_var = tk.StringVar(value="พร้อมรับ cursor")
        ttk.Label(toolbar, textvariable=self.live_var).pack(side="right", padx=12)

        self.canvas = tk.Canvas(self, background="#111827", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.canvas.bind("<Configure>", lambda _event: self._render())
        self.canvas.bind("<ButtonPress-1>", self._mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self._mouse_up)
        self.canvas.bind("<Motion>", self._mouse_move)
        ttk.Label(
            self,
            textvariable=self.status_var,
            foreground="#1f2937",
            padding=(10, 4),
        ).pack(fill="x")

        try:
            self.shell = self.adb.open_persistent_shell(serial)
        except ADBError as exc:
            self._report_error(str(exc))
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _capture_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                image, source = self.adb.capture_image(self.serial)
            except Exception as exc:
                try:
                    image, source = self.adb.capture_image(self.serial)
                except Exception:
                    if not self.stop_event.is_set():
                        self.after(0, lambda error=exc: self._report_error(str(error)))
                    return
            captured_at = time.perf_counter()
            self.after(0, lambda current=image, current_source=source, at=captured_at: self._set_image(current, current_source, at))
            elapsed = time.perf_counter() - started
            self.stop_event.wait(max(0.01, self.poll_ms / 1000 - elapsed))

    def _set_image(self, image: Image.Image, source: str, captured_at: float) -> None:
        if self.stop_event.is_set():
            return
        if self._last_frame_at:
            instant_fps = 1 / max(0.001, captured_at - self._last_frame_at)
            self.measured_fps = instant_fps if not self.measured_fps else self.measured_fps * 0.85 + instant_fps * 0.15
        self._last_frame_at = captured_at
        self.last_image = image
        fps_text = f"{self.measured_fps:.1f}" if self.measured_fps else "…"
        self.live_var.set(f"PREVIEW {image.width}×{image.height} • {fps_text} FPS • {source}")
        self._render()

    def _render(self) -> None:
        if self.last_image is None or not self.winfo_exists():
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        scale = min(width / self.last_image.width, height / self.last_image.height)
        display_width = max(1, round(self.last_image.width * scale))
        display_height = max(1, round(self.last_image.height * scale))
        left = (width - display_width) // 2
        top = (height - display_height) // 2
        preview = self.last_image.resize((display_width, display_height), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("frame")
        self.canvas.create_image(left, top, anchor="nw", image=self.photo, tags="frame")
        self.canvas.tag_raise("cursor_mark")
        self.display_box = (left, top, display_width, display_height)

    def _to_native(self, canvas_x: int, canvas_y: int) -> tuple[int, int] | None:
        if self.last_image is None:
            return None
        left, top, width, height = self.display_box
        if not (left <= canvas_x < left + width and top <= canvas_y < top + height):
            return None
        native_x = round((canvas_x - left) * self.last_image.width / width)
        native_y = round((canvas_y - top) * self.last_image.height / height)
        return (
            max(0, min(self.last_image.width - 1, native_x)),
            max(0, min(self.last_image.height - 1, native_y)),
        )

    def _mouse_move(self, event: tk.Event) -> None:
        point = self._to_native(int(event.x), int(event.y))
        if point:
            self.status_var.set(f"LIVE • cursor ({point[0]},{point[1]})")

    def _mouse_down(self, event: tk.Event) -> None:
        self.press_native = self._to_native(int(event.x), int(event.y))
        self.press_time = time.perf_counter()

    def _mouse_up(self, event: tk.Event) -> None:
        end = self._to_native(int(event.x), int(event.y))
        start = self.press_native
        started_at = self.press_time
        self.press_native = None
        if start is None or end is None or not self.shell:
            return
        duration_ms = max(1, round((time.perf_counter() - started_at) * 1000))
        distance = abs(start[0] - end[0]) + abs(start[1] - end[1])
        try:
            if distance > 12:
                self.shell.send(f"input swipe {start[0]} {start[1]} {end[0]} {end[1]} {duration_ms}")
                self.status_var.set(f"SWIPE ({start[0]},{start[1]}) → ({end[0]},{end[1]})")
            elif duration_ms >= 250:
                self.shell.send(f"input swipe {start[0]} {start[1]} {start[0]} {start[1]} {duration_ms}")
                self.status_var.set(f"HOLD ({start[0]},{start[1]}) {duration_ms} ms")
            else:
                self.shell.send(f"input tap {start[0]} {start[1]}")
                self.status_var.set(f"TAP ({start[0]},{start[1]})")
        except ADBError as exc:
            self._report_error(str(exc))
            return
        self.canvas.delete("cursor_mark")
        left, top, width, height = self.display_box
        marker_x = left + start[0] * width / max(1, self.last_image.width if self.last_image else 1)
        marker_y = top + start[1] * height / max(1, self.last_image.height if self.last_image else 1)
        self.canvas.create_oval(marker_x - 8, marker_y - 8, marker_x + 8, marker_y + 8, outline="#fbbf24", width=3, tags="cursor_mark")
        if self.on_interaction and distance <= 12:
            self.on_interaction(start[0], start[1], started_at, duration_ms)

    def _report_error(self, message: str) -> None:
        self.live_var.set("DISCONNECTED")
        self.status_var.set(f"ERROR: {message}")
        if self.on_error:
            self.on_error(message)

    def close(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.adb.cancel_all()
        if self.shell:
            self.shell.close()
            self.shell = None
        self.destroy()
