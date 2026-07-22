from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


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
        }
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        self.widgets: dict[str, tk.Widget] = {}
        self._row(frame, 0, "ID", "id")
        self._row(frame, 1, "เวลา (วินาที)", "at")
        ttk.Label(frame, text="Class").grid(row=2, column=0, sticky="w", pady=4)
        class_box = ttk.Combobox(frame, textvariable=self.vars["event_class"], state="readonly", values=("required", "safe_random"))
        class_box.grid(row=2, column=1, sticky="ew", pady=4)
        class_box.bind("<<ComboboxSelected>>", lambda _event: self._class_changed())
        self.widgets["event_class"] = class_box
        ttk.Label(frame, text="Type").grid(row=3, column=0, sticky="w", pady=4)
        type_box = ttk.Combobox(frame, textvariable=self.vars["type"], state="readonly", values=("action", "optional_action", "choice"))
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
        buttons = ttk.Frame(frame)
        buttons.grid(row=14, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="ยกเลิก", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="บันทึก Event", command=self._submit).pack(side="right", padx=8)
        frame.columnconfigure(1, weight=1)
        self.vars["at"].trace_add("write", self._time_changed)
        self._refresh()

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
        if post_game:
            self.vars["event_class"].set("required")
            if self.vars["action"].get() not in {"tap", "hold"}:
                self.vars["action"].set("tap")
        required = self.vars["event_class"].get() == "required"
        if required:
            self.vars["type"].set("action")
            self.vars["chance"].set("100")
            self.vars["jitter_ms"].set("0")
        else:
            self.vars["phase"].set("synced")
            self.vars["type"].set("choice")
        choice = not required
        touch = required and self.vars["action"].get() in {"tap", "hold"}
        self._set_state(("chance",), False)
        self._set_state(("jitter_ms", "safe_zone_id"), not required)
        self._set_state(("type",), False)
        self._set_state(("action",), False)
        self._set_state(("none", "jump", "slide"), choice)
        self._set_state(("phase",), required)
        self._set_state(("event_class",), not post_game)
        self._set_state(("x", "y"), touch)
        self.required_note.configure(
            text=(
                "หลังจบเกม: บันทึกเฉพาะ Tap/Hold"
                if post_game else
                ("อยู่ใน Safe Zone: เลือก Required หรือ Safe Random ได้เอง" if in_safe_zone else "Required: บังคับทำ 100% — ห้ามสุ่ม")
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
            if event["action"] in {"tap", "hold"}:
                event["x"] = int(self.vars["x"].get())
                event["y"] = int(self.vars["y"].get())
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
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        labels = {"id": "ID", "start": "เริ่ม (วินาที)", "end": "จบ (วินาที)", "label": "คำอธิบาย"}
        for row, key in enumerate(("id", "start", "end", "label")):
            ttk.Label(frame, text=labels[key]).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=self.values[key]).grid(row=row, column=1, sticky="ew", pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="ยกเลิก", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="บันทึก Safe Zone", command=self._submit).pack(side="right", padx=8)
        frame.columnconfigure(1, weight=1)

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
