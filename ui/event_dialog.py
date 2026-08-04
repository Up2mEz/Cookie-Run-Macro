from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


def wheel_scroll_units(delta: int) -> int:
    """Normalize Windows mouse wheels and high-resolution touchpads."""
    if delta == 0:
        return 0
    magnitude = max(1, abs(int(delta)) // 120)
    return -magnitude if delta > 0 else magnitude


class DialogScroller(ttk.Frame):
    """Scrollable body whose wheel binding works over every child widget."""

    def __init__(self, parent: tk.Toplevel, *, padding: int = 14) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, padding=padding)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        # Child widgets include the Toplevel path in their bind tags, so these
        # bindings work while the pointer is over Entries, Buttons and Labels.
        parent.bind("<MouseWheel>", self._mousewheel, add="+")
        parent.bind("<Button-4>", lambda _event: self._scroll(-1), add="+")
        parent.bind("<Button-5>", lambda _event: self._scroll(1), add="+")

    def _content_changed(self, _event: tk.Event | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_changed(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=max(1, int(event.width)))

    def _scroll(self, units: int) -> str:
        if units:
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _mousewheel(self, event: tk.Event) -> str:
        return self._scroll(wheel_scroll_units(int(getattr(event, "delta", 0))))


def fit_scrollable_dialog(window: tk.Toplevel, *, minimum_width: int = 400, minimum_height: int = 260) -> None:
    """Keep modal dialogs usable on short screens while allowing resize."""
    window.update_idletasks()
    maximum_height = max(minimum_height, window.winfo_screenheight() - 120)
    width = max(minimum_width, window.winfo_reqwidth())
    height = min(max(minimum_height, window.winfo_reqheight()), maximum_height)
    window.geometry(f"{width}x{height}")
    window.minsize(minimum_width, minimum_height)
    window.resizable(True, True)


class BulkSafeRandomDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, selected_count: int, on_submit: Callable[[dict, bool], None]) -> None:
        super().__init__(parent)
        self.title("ปรับ Chance หลาย Event")
        self.transient(parent)
        self.grab_set()
        self.on_submit = on_submit
        self.scope = tk.StringVar(value="selected" if selected_count else "all")
        self.vars = {key: tk.StringVar(value=value) for key, value in (("jump", "40"), ("slide", "20"), ("none", "40"))}
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ใช้ Chance ชุดเดียวกับ", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        selected_radio = ttk.Radiobutton(frame, text=f"Safe Random ที่เลือก ({selected_count} Event)", variable=self.scope, value="selected")
        selected_radio.pack(anchor="w", pady=(6, 2))
        if not selected_count:
            selected_radio.configure(state="disabled")
        ttk.Radiobutton(frame, text="Safe Random ทั้งหมด", variable=self.scope, value="all").pack(anchor="w")
        weights = ttk.LabelFrame(frame, text="Chance (รวมต้อง 100%)", padding=10)
        weights.pack(fill="x", pady=12)
        for column, key in enumerate(("jump", "slide", "none")):
            ttk.Label(weights, text=key).grid(row=0, column=column, padx=4)
            ttk.Entry(weights, textvariable=self.vars[key], width=9).grid(row=1, column=column, padx=4)
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        ttk.Button(actions, text="ยกเลิก", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="นำไปใช้", style="Primary.TButton", command=self._submit).pack(side="right", padx=6)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._submit())
        fit_scrollable_dialog(self, minimum_width=390, minimum_height=275)

    def _submit(self) -> None:
        try:
            options = {key: float(value.get()) for key, value in self.vars.items()}
        except ValueError:
            messagebox.showerror("Chance ไม่ถูกต้อง", "กรอก Chance เป็นตัวเลข", parent=self)
            return
        if any(value < 0 for value in options.values()) or not math.isclose(sum(options.values()), 100, rel_tol=0.0, abs_tol=1e-9):
            messagebox.showerror("Chance รวมไม่ครบ", "Chance ต้องไม่ติดลบและรวมกันเท่ากับ 100%", parent=self)
            return
        self.on_submit(options, self.scope.get() == "all")
        self.destroy()


class EventDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        event: dict | None,
        safe_zones: list[dict],
        on_submit: Callable[[dict], None],
    ) -> None:
        super().__init__(parent)
        self.title("แก้ไข Event" if event else "เพิ่ม Event")
        self.transient(parent)
        self.grab_set()
        self.on_submit = on_submit
        self.safe_zones = safe_zones
        source = event or {}
        self.vars = {
            "id": tk.StringVar(value=str(source.get("id", ""))),
            "at": tk.StringVar(value=str(source.get("at", "0.000"))),
            "phase": tk.StringVar(value=str(source.get("phase", "synced"))),
            "event_class": tk.StringVar(value=str(source.get("event_class", "required"))),
            "type": tk.StringVar(value=str(source.get("type", "action"))),
            "action": tk.StringVar(value=str(source.get("action", "jump"))),
            "chance": tk.StringVar(value=str(source.get("chance", 100))),
            "jitter_ms": tk.StringVar(value=str(source.get("jitter_ms", 0))),
            "duration_ms": tk.StringVar(value=str(source.get("duration_ms", 400))),
            "x": tk.StringVar(value=str(source.get("x", 640))),
            "y": tk.StringVar(value=str(source.get("y", 360))),
            "safe_zone_id": tk.StringVar(value=str(source.get("safe_zone_id", safe_zones[0]["id"] if safe_zones else ""))),
            "none": tk.StringVar(value=str(source.get("options", {}).get("none", 40))),
            "jump": tk.StringVar(value=str(source.get("options", {}).get("jump", 40))),
            "slide": tk.StringVar(value=str(source.get("options", {}).get("slide", 20))),
            "detect_x": tk.StringVar(value=str(source.get("detect_x", 1040))),
            "detect_y": tk.StringVar(value=str(source.get("detect_y", 672))),
            "detect_radius": tk.StringVar(value=str(source.get("detect_radius", 24))),
            "change_threshold": tk.StringVar(value=str(source.get("change_threshold", 0.12))),
            "stable_frames": tk.StringVar(value=str(source.get("stable_frames", 2))),
            "poll_ms": tk.StringVar(value=str(source.get("poll_ms", 250))),
            "arm_delay_ms": tk.StringVar(value=str(source.get("arm_delay_ms", 150))),
            "success_delay_ms": tk.StringVar(value=str(source.get("success_delay_ms", 250))),
            "timeout_seconds": tk.StringVar(value=str(source.get("timeout_seconds", 0))),
        }
        self.scroller = DialogScroller(self, padding=14)
        self.scroller.pack(fill="both", expand=True)
        frame = self.scroller.content
        self.widgets: dict[str, tk.Widget] = {}
        self._row(frame, 0, "ID", "id")
        self._row(frame, 1, "เวลา (วินาที)", "at")
        ttk.Label(frame, text="Class").grid(row=2, column=0, sticky="w", pady=4)
        class_box = ttk.Combobox(frame, textvariable=self.vars["event_class"], state="readonly", values=("required", "safe_random"))
        class_box.grid(row=2, column=1, sticky="ew", pady=4)
        class_box.bind("<<ComboboxSelected>>", lambda _event: self._class_changed())
        self.widgets["event_class"] = class_box
        ttk.Label(frame, text="Type").grid(row=3, column=0, sticky="w", pady=4)
        type_box = ttk.Combobox(frame, textvariable=self.vars["type"], state="readonly", values=("action", "adaptive_wait", "optional_action", "choice"))
        type_box.grid(row=3, column=1, sticky="ew", pady=4)
        type_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        self.widgets["type"] = type_box
        ttk.Label(frame, text="Action").grid(row=4, column=0, sticky="w", pady=4)
        action_box = ttk.Combobox(frame, textvariable=self.vars["action"], state="readonly", values=("jump", "slide", "tap", "hold"))
        action_box.grid(row=4, column=1, sticky="ew", pady=4)
        action_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        self.widgets["action"] = action_box
        self._row(frame, 5, "Chance (%)", "chance")
        self._row(frame, 6, "Jitter (ms)", "jitter_ms")
        self._row(frame, 7, "Slide duration (ms)", "duration_ms")
        ttk.Label(frame, text="Safe Zone").grid(row=8, column=0, sticky="w", pady=4)
        zone_box = ttk.Combobox(frame, textvariable=self.vars["safe_zone_id"], state="readonly", values=[zone["id"] for zone in safe_zones])
        zone_box.grid(row=8, column=1, sticky="ew", pady=4)
        self.widgets["safe_zone_id"] = zone_box
        weights = ttk.LabelFrame(frame, text="Random chance (รวมต้อง 100%)", padding=8)
        weights.grid(row=9, column=0, columnspan=2, sticky="ew", pady=8)
        self.weights = weights
        for column, key in enumerate(("jump", "slide", "none")):
            ttk.Label(weights, text=key).grid(row=0, column=column, padx=3)
            entry = ttk.Entry(weights, textvariable=self.vars[key], width=8)
            entry.grid(row=1, column=column, padx=3)
            self.widgets[key] = entry
        self.required_note = ttk.Label(frame, text="Required: บังคับทำ 100% — ห้ามสุ่ม", foreground="#9b2c2c")
        ttk.Label(frame, text="Phase").grid(row=10, column=0, sticky="w", pady=4)
        phase_box = ttk.Combobox(frame, textvariable=self.vars["phase"], state="readonly", values=("pre_sync", "synced", "post_game"))
        phase_box.grid(row=10, column=1, sticky="ew", pady=4)
        phase_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        self.widgets["phase"] = phase_box
        self._row(frame, 11, "Touch X", "x")
        self._row(frame, 12, "Touch Y", "y")
        self.required_note.grid(row=13, column=0, columnspan=2, sticky="w", pady=6)
        self.adaptive_frame = ttk.LabelFrame(frame, text="Adaptive: เริ่มเฝ้าปุ่ม Stop (พิกัด ADB 1280×720)", padding=8)
        self.adaptive_frame.grid(row=14, column=0, columnspan=2, sticky="ew", pady=8)
        adaptive_fields = (
            ("จุดตรวจ Stop X", "detect_x"), ("จุดตรวจ Stop Y", "detect_y"),
            ("รัศมีจุดตรวจ (px)", "detect_radius"), ("ความต่างภาพ 0.03–1.00", "change_threshold"),
            ("ต้องเปลี่ยนติดกัน (เฟรม)", "stable_frames"), ("ตรวจทุก (ms)", "poll_ms"),
            ("รอก่อนจำภาพ Stop (ms)", "arm_delay_ms"), ("รอก่อนกด Play (ms)", "success_delay_ms"),
            ("หมดเวลา (วินาที, 0=ไม่จำกัด)", "timeout_seconds"),
        )
        for row, (label, key) in enumerate(adaptive_fields):
            ttk.Label(self.adaptive_frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(self.adaptive_frame, textvariable=self.vars[key])
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            self.widgets[key] = entry
        ttk.Label(
            self.adaptive_frame,
            text="Event ถัดไปและตัวสุดท้ายต้องเป็น Tap กด Play • ได้เร็วจะกดเร็ว • ได้ช้าจะรอต่อ • F8 หยุดได้",
            foreground="#9b2c2c", wraplength=430,
        ).grid(row=len(adaptive_fields), column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.adaptive_frame.columnconfigure(1, weight=1)
        buttons = ttk.Frame(frame)
        buttons.grid(row=15, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="ยกเลิก", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="บันทึก Event", command=self._submit).pack(side="right", padx=8)
        frame.columnconfigure(1, weight=1)
        self.vars["at"].trace_add("write", self._time_changed)
        self._refresh()
        self.after_idle(lambda: fit_scrollable_dialog(self, minimum_width=500, minimum_height=360))

    def _row(self, frame: ttk.Frame, row: int, label: str, key: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(frame, textvariable=self.vars[key])
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        self.widgets[key] = entry

    def _set_state(self, keys: tuple[str, ...], enabled: bool) -> None:
        for key in keys:
            widget = self.widgets[key]
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                widget.configure(state="normal" if enabled else "disabled")

    def _safe_zone_at_time(self) -> dict | None:
        try:
            at = float(self.vars["at"].get())
        except ValueError:
            return None
        return next(
            (
                zone
                for zone in self.safe_zones
                if float(zone["start"]) <= at <= float(zone["end"])
            ),
            None,
        )

    def _suggest_zone_for_random(self) -> dict | None:
        """Select a matching zone without changing a Required event's meaning."""
        zone = self._safe_zone_at_time()
        if zone and self.vars["event_class"].get() == "safe_random":
            self.vars["safe_zone_id"].set(str(zone["id"]))
        return zone

    def _time_changed(self, *_args: object) -> None:
        self._suggest_zone_for_random()
        self._refresh()

    def _class_changed(self) -> None:
        self._suggest_zone_for_random()
        self._refresh()

    def _refresh(self) -> None:
        in_safe_zone = self._safe_zone_at_time() is not None
        post_game = self.vars["phase"].get() == "post_game"
        adaptive = self.vars["type"].get() == "adaptive_wait"
        if adaptive:
            self.vars["event_class"].set("required")
            self.vars["phase"].set("pre_sync")
            self.vars["action"].set("tap")
        if post_game:
            self.vars["event_class"].set("required")
            if self.vars["action"].get() not in {"tap", "hold"}:
                self.vars["action"].set("tap")
        required = self.vars["event_class"].get() == "required"
        if required:
            if self.vars["type"].get() not in {"action", "adaptive_wait"}:
                self.vars["type"].set("action")
            self.vars["chance"].set("100")
            self.vars["jitter_ms"].set("0")
        else:
            self.vars["phase"].set("synced")
            self.vars["type"].set("choice")
        choice = not required
        adaptive = required and self.vars["type"].get() == "adaptive_wait"
        touch = required and not adaptive and self.vars["action"].get() in {"tap", "hold"}
        self._set_state(("chance",), False)
        self._set_state(("jitter_ms", "safe_zone_id"), not required)
        self._set_state(("type",), required and not post_game)
        self._set_state(("action",), required and not adaptive)
        self._set_state(("none", "jump", "slide"), choice)
        self._set_state(("phase",), required and not adaptive)
        self._set_state(("event_class",), not post_game and not adaptive)
        self._set_state(("x", "y"), touch)
        self.adaptive_frame.grid() if adaptive else self.adaptive_frame.grid_remove()
        self.required_note.configure(
            text=(
                "เริ่มเฝ้า Stop ตั้งแต่เวลานี้; เมื่อหายจะยิง Tap Play ตัวสุดท้ายทันที"
                if adaptive else
                "หลังจบเกม: บันทึกเฉพาะ Tap/Hold"
                if post_game else
                ("อยู่ใน Safe Zone: Jump/Slide จะถูกแปลงเป็น Safe Random อัตโนมัติ" if in_safe_zone else "Required: บังคับทำ 100% — ห้ามสุ่ม")
            )
        )
        self.required_note.grid() if required else self.required_note.grid_remove()

    def _submit(self) -> None:
        try:
            event = {
                "id": self.vars["id"].get().strip(),
                "at": float(self.vars["at"].get()),
                "phase": self.vars["phase"].get(),
                "event_class": self.vars["event_class"].get(),
                "type": self.vars["type"].get(),
                "action": self.vars["action"].get(),
                "chance": int(self.vars["chance"].get()),
                "jitter_ms": int(self.vars["jitter_ms"].get()),
                "duration_ms": int(self.vars["duration_ms"].get()),
            }
            if event["action"] in {"tap", "hold"} and event["type"] != "adaptive_wait":
                event["x"] = int(self.vars["x"].get())
                event["y"] = int(self.vars["y"].get())
            if event["type"] == "adaptive_wait":
                for key in ("detect_x", "detect_y", "detect_radius", "stable_frames", "poll_ms", "arm_delay_ms", "success_delay_ms", "timeout_seconds"):
                    event[key] = int(self.vars[key].get())
                event["change_threshold"] = float(self.vars["change_threshold"].get())
            if event["event_class"] == "safe_random":
                event["safe_zone_id"] = self.vars["safe_zone_id"].get()
                if event["type"] == "choice":
                    event["options"] = {key: float(self.vars[key].get()) for key in ("none", "jump", "slide")}
                    if not math.isclose(sum(event["options"].values()), 100, rel_tol=0.0, abs_tol=1e-9):
                        messagebox.showerror(
                            "Chance รวมไม่ครบ",
                            "Chance ของ jump, slide และ none ต้องรวมกันเท่ากับ 100%",
                            parent=self,
                        )
                        return
        except ValueError:
            messagebox.showerror("ข้อมูลไม่ถูกต้อง", "เวลา, Chance, Jitter, Duration และ Weight ต้องเป็นตัวเลข", parent=self)
            return
        self.on_submit(event)
        self.destroy()


class SafeZoneDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, zone: dict | None, on_submit: Callable[[dict], None]) -> None:
        super().__init__(parent)
        self.title("แก้ไข Safe Zone" if zone else "เพิ่ม Safe Zone")
        self.transient(parent)
        self.grab_set()
        source = zone or {}
        self.on_submit = on_submit
        self.values = {
            "id": tk.StringVar(value=str(source.get("id", ""))),
            "start": tk.StringVar(value=str(source.get("start", "0.000"))),
            "end": tk.StringVar(value=str(source.get("end", "1.000"))),
            "label": tk.StringVar(value=str(source.get("label", "ช่วงปลอดภัย"))),
        }
        self.scroller = DialogScroller(self, padding=14)
        self.scroller.pack(fill="both", expand=True)
        frame = self.scroller.content
        labels = {"id": "ID", "start": "เริ่ม (วินาที)", "end": "จบ (วินาที)", "label": "คำอธิบาย"}
        for row, key in enumerate(("id", "start", "end", "label")):
            ttk.Label(frame, text=labels[key]).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=self.values[key]).grid(row=row, column=1, sticky="ew", pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="ยกเลิก", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="บันทึก Safe Zone", command=self._submit).pack(side="right", padx=8)
        frame.columnconfigure(1, weight=1)
        self.after_idle(lambda: fit_scrollable_dialog(self, minimum_width=430, minimum_height=240))

    def _submit(self) -> None:
        try:
            zone = {
                "id": self.values["id"].get().strip(),
                "start": float(self.values["start"].get()),
                "end": float(self.values["end"].get()),
                "label": self.values["label"].get().strip(),
            }
        except ValueError:
            messagebox.showerror("ข้อมูลไม่ถูกต้อง", "เวลาเริ่มและเวลาจบต้องเป็นตัวเลข", parent=self)
            return
        self.on_submit(zone)
        self.destroy()
