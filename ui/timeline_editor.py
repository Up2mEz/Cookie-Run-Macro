from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


ACTION_COLORS = {
    "jump": "#dc2626",
    "slide": "#2563eb",
    "tap": "#d97706",
    "hold": "#7c3aed",
}

PHASE_LANES = {
    "pre_sync": (34, 88),
    "synced": (90, 150),
    "post_game": (152, 220),
}


def pattern_duration(pattern: dict) -> float:
    event_end = max(
        (float(event.get("at", 0)) + int(event.get("duration_ms", 0)) / 1000 for event in pattern.get("events", [])),
        default=0.0,
    )
    zone_end = max((float(zone.get("end", 0)) for zone in pattern.get("safe_zones", [])), default=0.0)
    return max(event_end, zone_end)


def phase_duration(pattern: dict, phase: str) -> float:
    return max(
        (
            float(event.get("at", 0)) + int(event.get("duration_ms", 0)) / 1000
            for event in pattern.get("events", [])
            if event.get("phase", "synced") == phase
        ),
        default=0.0,
    )


def playhead_scroll_fraction(
    playhead_x: float,
    viewport_width: float,
    scroll_width: float,
    *,
    margin: float = 120.0,
) -> float:
    """Return an xview fraction that keeps the playhead near the right margin."""
    if scroll_width <= viewport_width or playhead_x <= viewport_width - margin:
        return 0.0
    maximum_left = max(0.0, scroll_width - viewport_width)
    desired_left = min(maximum_left, max(0.0, playhead_x - viewport_width + margin))
    return desired_left / scroll_width


def events_in_range(pattern: dict, start: float, end: float, *, phase: str = "synced") -> list[dict]:
    low, high = sorted((float(start), float(end)))
    return [
        event for event in pattern.get("events", [])
        if event.get("phase", "synced") == phase and low <= float(event.get("at", 0)) <= high
    ]


def range_summary(pattern: dict, start: float, end: float, *, max_items: int = 4) -> tuple[int, str]:
    """Return a compact, bounded summary so timeline actions never get pushed off-screen."""
    events = events_in_range(pattern, start, end)
    visible = events[:max(0, int(max_items))]
    detail = ", ".join(
        f"{event.get('action', 'random')}@{float(event.get('at', 0)):.3f}s"
        for event in visible
    ) or "ไม่มี Event"
    hidden = len(events) - len(visible)
    if hidden > 0:
        detail += f" และอีก {hidden}"
    return len(events), detail


class TimelineEditor(ttk.LabelFrame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_create: Callable[[float, float], None],
        on_update: Callable[[str, float, float], None],
        on_select: Callable[[str | None], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent, text="Timeline — ลากช่วงหลัง Sync เพื่อสร้าง Safe Zone", padding=7)
        self.on_create = on_create
        self.on_update = on_update
        self.on_select = on_select
        self.on_delete = on_delete
        self.pattern: dict = {"events": [], "safe_zones": []}
        self.pixels_per_second = 70.0
        self.selection_start: float | None = None
        self.selection_end: float | None = None
        self.selected_zone_id: str | None = None
        self.dragging = False
        self.resize_edge: str | None = None
        self.playhead_phase: str | None = None
        self.playhead_at = 0.0

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 5))
        ttk.Label(toolbar, text="สี: Jump แดง • Slide น้ำเงิน • Tap ส้ม • Hold ม่วง", style="Hint.TLabel").pack(side="left")
        ttk.Button(toolbar, text="− Zoom", width=8, command=lambda: self._zoom(0.75)).pack(side="right")
        ttk.Button(toolbar, text="+ Zoom", width=8, command=lambda: self._zoom(1.35)).pack(side="right", padx=4)

        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, height=232, background="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1")
        scrollbar = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=scrollbar.set)
        self.canvas.pack(fill="both", expand=True)
        scrollbar.pack(fill="x")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Configure>", lambda _event: self._draw())

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(5, 0))
        self.info_var = tk.StringVar(value="ลากเมาส์ในเลน ‘หลัง Sync’ เพื่อเลือกช่วง")
        self.info_label = ttk.Label(bottom, textvariable=self.info_var, justify="left", wraplength=620)
        self.info_label.pack(fill="x")
        bottom.bind(
            "<Configure>",
            lambda event: self.info_label.configure(wraplength=max(260, event.width - 16)),
        )

        actions = ttk.Frame(bottom)
        actions.pack(fill="x", pady=(5, 0))
        self.create_button = ttk.Button(
            actions,
            text="สร้าง Safe Zone",
            command=self._create_zone,
            state="disabled",
        )
        self.create_button.pack(side="left")
        ttk.Label(actions, text="ลากครอบกี่ Event ก็ได้", style="Hint.TLabel").pack(side="left", padx=8)
        self.update_button = ttk.Button(actions, text="อัปเดต Zone ที่เลือก", command=self._update_zone, state="disabled")
        self.update_button.pack(side="right")
        self.delete_button = ttk.Button(
            actions, text="ลบ Zone ที่เลือก", command=self._delete_zone, state="disabled",
        )
        self.delete_button.pack(side="right", padx=(0, 6))

    def set_pattern(self, pattern: dict | None) -> None:
        self.pattern = pattern or {"events": [], "safe_zones": []}
        self.selection_start = None
        self.selection_end = None
        self.selected_zone_id = None
        self.create_button.configure(state="disabled", text="สร้าง Safe Zone")
        self.update_button.configure(state="disabled")
        self.delete_button.configure(state="disabled")
        self._draw()
        self._update_info()

    def set_playhead(self, phase: str | None, elapsed: float = 0.0, *, follow: bool = True) -> None:
        self.playhead_phase = phase if phase in PHASE_LANES else None
        self.playhead_at = max(0.0, float(elapsed))
        self._draw()
        if follow and self.playhead_phase:
            self._follow_playhead()

    def update_playback(self, phase: str, elapsed: float) -> None:
        """Map runtime states to the phase-local timeline shown in the editor."""
        if phase == "waiting_sync":
            self.set_playhead("pre_sync", phase_duration(self.pattern, "pre_sync"))
        elif phase == "waiting_result":
            self.set_playhead("synced", phase_duration(self.pattern, "synced"))
        elif phase in PHASE_LANES:
            self.set_playhead(phase, elapsed)
        elif phase in {"stopped", "error"}:
            self.set_playhead(None, 0.0, follow=False)

    def _follow_playhead(self) -> None:
        self.canvas.update_idletasks()
        region = self.canvas.cget("scrollregion").split()
        if len(region) != 4:
            return
        scroll_width = float(region[2]) - float(region[0])
        viewport_width = max(1.0, float(self.canvas.winfo_width()))
        fraction = playhead_scroll_fraction(
            self._time_to_x(self.playhead_at), viewport_width, scroll_width,
        )
        current_left = self.canvas.canvasx(0)
        x = self._time_to_x(self.playhead_at)
        if x < current_left + 80 or x > current_left + viewport_width - 120:
            self.canvas.xview_moveto(fraction)

    def select_zone(self, zone_id: str | None) -> None:
        zone = next((item for item in self.pattern.get("safe_zones", []) if str(item.get("id")) == zone_id), None)
        self.selected_zone_id = str(zone["id"]) if zone else None
        self.selection_start = float(zone["start"]) if zone else None
        self.selection_end = float(zone["end"]) if zone else None
        self._draw()
        self._update_info()

    def _zoom(self, factor: float) -> None:
        self.pixels_per_second = max(25.0, min(320.0, self.pixels_per_second * factor))
        self._draw()

    def _time_to_x(self, seconds: float) -> float:
        return 105 + seconds * self.pixels_per_second

    def _x_to_time(self, x: float) -> float:
        return max(0.0, (x - 105) / self.pixels_per_second)

    def _draw(self) -> None:
        if not self.canvas.winfo_exists():
            return
        self.canvas.delete("all")
        duration = max(10.0, pattern_duration(self.pattern) + 2.0)
        width = max(self.canvas.winfo_width(), int(self._time_to_x(duration) + 40))
        self.canvas.configure(scrollregion=(0, 0, width, 232))
        self.canvas.create_rectangle(0, 34, width, 88, fill="#fff7ed", outline="")
        self.canvas.create_rectangle(0, 90, width, 150, fill="#eff6ff", outline="")
        self.canvas.create_rectangle(0, 152, width, 220, fill="#ecfdf5", outline="")
        self.canvas.create_text(8, 61, text="ก่อน Sync", anchor="w", fill="#9a3412", font=("Segoe UI", 9, "bold"))
        self.canvas.create_text(8, 120, text="หลัง Sync", anchor="w", fill="#1e40af", font=("Segoe UI", 9, "bold"))
        self.canvas.create_text(8, 186, text="หลังจบเกม", anchor="w", fill="#047857", font=("Segoe UI", 9, "bold"))

        tick_step = 1 if self.pixels_per_second >= 55 else 2
        for second in range(0, int(duration) + 1, tick_step):
            x = self._time_to_x(second)
            self.canvas.create_line(x, 20, x, 220, fill="#dbe2ea")
            self.canvas.create_text(x + 2, 17, text=f"{second}s", anchor="sw", fill="#475569", font=("Segoe UI", 8))

        for zone in self.pattern.get("safe_zones", []):
            x1, x2 = self._time_to_x(float(zone["start"])), self._time_to_x(float(zone["end"]))
            selected = zone.get("id") == self.selected_zone_id
            self.canvas.create_rectangle(x1, 95, x2, 146, fill="#86efac", outline="#15803d", width=3 if selected else 1, tags=(f"zone:{zone['id']}", "zone"))
            self.canvas.create_text((x1 + x2) / 2, 140, text=str(zone["id"]), fill="#14532d", font=("Segoe UI", 8, "bold"), tags=(f"zone:{zone['id']}", "zone"))

        for event in self.pattern.get("events", []):
            phase = event.get("phase", "synced")
            if phase == "pre_sync":
                y1, y2 = 40, 82
            elif phase == "post_game":
                y1, y2 = 160, 212
            else:
                y1, y2 = 97, 142
            x = self._time_to_x(float(event.get("at", 0)))
            action = str(event.get("action", "random"))
            color = ACTION_COLORS.get(action, "#475569")
            self.canvas.create_line(x, y1, x, y2, fill=color, width=3)
            self.canvas.create_polygon(x - 4, y1, x + 4, y1, x, y1 + 7, fill=color, outline="")

        if self.selection_start is not None and self.selection_end is not None:
            x1 = self._time_to_x(min(self.selection_start, self.selection_end))
            x2 = self._time_to_x(max(self.selection_start, self.selection_end))
            self.canvas.create_rectangle(x1, 92, x2, 148, fill="#facc15", stipple="gray50", outline="#ca8a04", width=2, tags="selection")

        if self.playhead_phase in PHASE_LANES:
            y1, y2 = PHASE_LANES[self.playhead_phase]
            x = self._time_to_x(self.playhead_at)
            self.canvas.create_line(x, y1 + 2, x, y2 - 2, fill="#111827", width=3, tags="playhead")
            self.canvas.create_polygon(
                x - 6, y1 + 2, x + 6, y1 + 2, x, y1 + 10,
                fill="#111827", outline="", tags="playhead",
            )
            self.canvas.create_text(
                x + 7, y1 + 4, text=f"{self.playhead_at:.2f}s", anchor="nw",
                fill="#111827", font=("Segoe UI", 8, "bold"), tags="playhead",
            )

    def _zone_at(self, canvas_x: float, canvas_y: float) -> dict | None:
        if not 92 <= canvas_y <= 150:
            return None
        at = self._x_to_time(canvas_x)
        return next((zone for zone in self.pattern.get("safe_zones", []) if float(zone["start"]) <= at <= float(zone["end"])), None)

    def _press(self, event: tk.Event) -> None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        zone = self._zone_at(x, y)
        if zone:
            self.selected_zone_id = str(zone["id"])
            self.selection_start = float(zone["start"])
            self.selection_end = float(zone["end"])
            self.update_button.configure(state="normal")
            start_x = self._time_to_x(self.selection_start)
            end_x = self._time_to_x(self.selection_end)
            self.resize_edge = "start" if abs(x - start_x) <= 9 else "end" if abs(x - end_x) <= 9 else None
            self.dragging = self.resize_edge is not None
            if self.on_select:
                self.on_select(self.selected_zone_id)
        elif 90 <= y <= 150:
            self.selected_zone_id = None
            self.update_button.configure(state="disabled")
            self.selection_start = self._x_to_time(x)
            self.selection_end = self.selection_start
            self.dragging = True
            self.resize_edge = None
            if self.on_select:
                self.on_select(None)
        self._draw()
        self._update_info()

    def _motion(self, event: tk.Event) -> None:
        if not self.dragging or self.selection_start is None:
            return
        value = self._x_to_time(self.canvas.canvasx(event.x))
        if self.resize_edge == "start":
            self.selection_start = value
        else:
            self.selection_end = value
        self._draw()
        self._update_info()

    def _release(self, event: tk.Event) -> None:
        if self.dragging and self.selection_start is not None:
            value = self._x_to_time(self.canvas.canvasx(event.x))
            if self.resize_edge == "start":
                self.selection_start = value
            else:
                self.selection_end = value
        self.dragging = False
        self.resize_edge = None
        self._draw()
        self._update_info()

    def _selection(self) -> tuple[float, float] | None:
        if self.selection_start is None or self.selection_end is None:
            return None
        start, end = sorted((self.selection_start, self.selection_end))
        return (round(start, 3), round(end, 3)) if end - start >= 0.05 else None

    def _update_info(self) -> None:
        selection = self._selection()
        if not selection:
            self.create_button.configure(state="disabled", text="สร้าง Safe Zone")
            self.update_button.configure(state="normal" if self.selected_zone_id else "disabled")
            self.delete_button.configure(state="normal" if self.selected_zone_id else "disabled")
            self.info_var.set("ลากในเลน ‘หลัง Sync’ อย่างน้อย 0.05 วินาที • คลิก Zone แล้วลากขอบซ้าย/ขวาเพื่อแก้ช่วง")
            return
        start, end = selection
        count, detail = range_summary(self.pattern, start, end)
        prefix = f"Zone {self.selected_zone_id} • " if self.selected_zone_id else ""
        self.info_var.set(f"{prefix}{start:.3f}–{end:.3f}s ({end-start:.3f}s) • {count} Events: {detail}")
        self.create_button.configure(
            state="disabled" if self.selected_zone_id else "normal",
            text=f"สร้าง Safe Zone ({count} Events)",
        )
        self.update_button.configure(state="normal" if self.selected_zone_id else "disabled")
        self.delete_button.configure(state="normal" if self.selected_zone_id else "disabled")

    def _create_zone(self) -> None:
        selection = self._selection()
        if selection:
            self.on_create(*selection)

    def _update_zone(self) -> None:
        selection = self._selection()
        if selection and self.selected_zone_id:
            self.on_update(self.selected_zone_id, *selection)

    def _delete_zone(self) -> None:
        if self.selected_zone_id and self.on_delete:
            self.on_delete(self.selected_zone_id)
