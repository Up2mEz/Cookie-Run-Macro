from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from PIL import Image, ImageTk

from sync_detector import ROI, SyncError


def fixed_roi_from_center(
    center_x: int,
    center_y: int,
    image_size: tuple[int, int],
    fixed_size: tuple[int, int],
) -> ROI:
    width, height = map(int, fixed_size)
    image_width, image_height = map(int, image_size)
    if width <= 0 or height <= 0 or width > image_width or height > image_height:
        raise SyncError("ขนาดกรอบคงที่ไม่พอดีกับภาพ")
    x = max(0, min(image_width - width, int(center_x) - width // 2))
    y = max(0, min(image_height - height, int(center_y) - height // 2))
    return ROI(x, y, width, height)


class ROISelector(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        screenshot: Image.Image,
        on_confirm: Callable[[ROI], None],
        *,
        fixed_size: tuple[int, int] | None = None,
        title: str = "ตั้งค่ากรอบปุ่ม Pause",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.screenshot = screenshot.copy()
        self.on_confirm = on_confirm
        self.fixed_size = fixed_size
        max_width, max_height = 1000, 650
        self.scale = min(max_width / screenshot.width, max_height / screenshot.height, 1.0)
        display_size = (max(1, round(screenshot.width * self.scale)), max(1, round(screenshot.height * self.scale)))
        self.display_image = screenshot.resize(display_size, Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.display_image)
        self.start: tuple[int, int] | None = None
        self.rectangle: int | None = None
        self.selection: tuple[int, int, int, int] | None = None

        instruction = (
            f"เลื่อนกรอบคงที่ {fixed_size[0]}×{fixed_size[1]} px ให้ครอบปุ่ม Pause — ทุก Stage ใช้ขนาดเท่ากัน"
            if fixed_size else
            "ลากกรอบให้ครอบเฉพาะปุ่ม Pause และขอบเล็กน้อย (อย่างน้อย 12×12 px)"
        )
        ttk.Label(
            self,
            text=instruction,
            padding=10,
        ).pack(anchor="w")
        self.canvas = tk.Canvas(self, width=display_size[0], height=display_size[1], cursor="cross", highlightthickness=1)
        self.canvas.pack(padx=10, fill="both", expand=True)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        self.info = ttk.Label(bottom, text="ยังไม่ได้เลือกกรอบ")
        self.info.pack(side="left")
        ttk.Button(bottom, text="ยกเลิก", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="ใช้กรอบนี้", command=self._confirm).pack(side="right", padx=8)

    def _clamp(self, event: tk.Event) -> tuple[int, int]:
        return (
            max(0, min(int(event.x), self.display_image.width)),
            max(0, min(int(event.y), self.display_image.height)),
        )

    def _press(self, event: tk.Event) -> None:
        if self.fixed_size:
            self._set_fixed_selection(event)
            return
        self.start = self._clamp(event)
        if self.rectangle:
            self.canvas.delete(self.rectangle)
        self.rectangle = self.canvas.create_rectangle(*self.start, *self.start, outline="#ffb000", width=3)

    def _drag(self, event: tk.Event) -> None:
        if self.fixed_size:
            self._set_fixed_selection(event)
            return
        if self.start and self.rectangle:
            self.canvas.coords(self.rectangle, *self.start, *self._clamp(event))

    def _release(self, event: tk.Event) -> None:
        if self.fixed_size:
            self._set_fixed_selection(event)
            return
        if not self.start:
            return
        end = self._clamp(event)
        x1, x2 = sorted((self.start[0], end[0]))
        y1, y2 = sorted((self.start[1], end[1]))
        self.selection = (x1, y1, x2, y2)
        native = self._native_roi()
        self.info.configure(text=f"ROI จริง: x={native.x}, y={native.y}, {native.width}×{native.height} px")

    def _native_roi(self) -> ROI:
        if self.fixed_size and hasattr(self, "_fixed_native_roi"):
            return self._fixed_native_roi
        if not self.selection:
            raise SyncError("กรุณาลากกรอบปุ่ม Pause ก่อน")
        x1, y1, x2, y2 = self.selection
        x = round(x1 / self.scale)
        y = round(y1 / self.scale)
        right = round(x2 / self.scale)
        bottom = round(y2 / self.scale)
        return ROI(x, y, right - x, bottom - y)

    def _set_fixed_selection(self, event: tk.Event) -> None:
        if not self.fixed_size:
            return
        display_x, display_y = self._clamp(event)
        center_x = round(display_x / self.scale)
        center_y = round(display_y / self.scale)
        roi = fixed_roi_from_center(center_x, center_y, self.screenshot.size, self.fixed_size)
        x, y, width, height = roi.x, roi.y, roi.width, roi.height
        right, bottom = x + width, y + height
        self.selection = (
            round(x * self.scale), round(y * self.scale),
            round(right * self.scale), round(bottom * self.scale),
        )
        # เก็บ native โดยตรง ป้องกัน round-trip จาก scale ทำให้ 62×62 กลายเป็น 61×62
        self._fixed_native_roi = roi
        if self.rectangle:
            self.canvas.delete(self.rectangle)
        self.rectangle = self.canvas.create_rectangle(*self.selection, outline="#ffb000", width=3)
        self.info.configure(text=f"ROI จริง: x={x}, y={y}, {width}×{height} px (ขนาดล็อกแล้ว)")

    def _confirm(self) -> None:
        try:
            roi = self._native_roi()
            roi.validate(self.screenshot.size)
        except SyncError as exc:
            messagebox.showwarning("กรอบยังใช้ไม่ได้", str(exc), parent=self)
            return
        if roi.width > 160 or roi.height > 160:
            if not messagebox.askyesno(
                "กรอบค่อนข้างใหญ่",
                "แนะนำให้ใช้กรอบไม่เกิน 160×160 px เพื่อหลีกเลี่ยง UI ที่เปลี่ยนตลอดเวลา\nยังต้องการใช้กรอบนี้หรือไม่?",
                parent=self,
            ):
                return
        self.on_confirm(roi)
        self.destroy()
