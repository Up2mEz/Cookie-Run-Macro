from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from pattern_store import safe_pattern_filename


class RecordingSaveDialog:
    """Dialog เดียวที่บอกชัดว่างานอัดจะสร้างใหม่หรือเขียนทับ Pattern ใด."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        pattern_names: list[str],
        current_name: str,
        suggested_name: str,
        event_count: int,
        duration_text: str,
    ) -> None:
        self.parent = parent
        self.pattern_names = pattern_names
        self.result: tuple[str, str] | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("เลือกที่บันทึกงานอัด")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._keep_draft)

        frame = ttk.Frame(self.window, padding=18)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="บันทึกงานอัดนี้ไว้ที่ไหน?", font=("Segoe UI", 15, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            frame,
            text=f"อัดเสร็จแล้ว • {event_count} Events • ความยาว {duration_text}\nยังไม่มี Pattern ใดถูกเขียนทับ",
            foreground="#374151",
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))

        self.mode = tk.StringVar(value="new")
        self.new_name = tk.StringVar(value=suggested_name)
        self.existing_name = tk.StringVar(value=current_name if current_name in pattern_names else (pattern_names[0] if pattern_names else ""))

        ttk.Radiobutton(
            frame, text="สร้าง Pattern ใหม่ (แนะนำ — ไม่ทับงานเดิม)", variable=self.mode, value="new",
            command=self._refresh,
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        self.new_entry = ttk.Entry(frame, textvariable=self.new_name, width=48)
        self.new_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(24, 0), pady=(4, 12))

        ttk.Radiobutton(
            frame, text="บันทึกทับ Pattern ที่มีอยู่", variable=self.mode, value="existing",
            command=self._refresh,
        ).grid(row=4, column=0, columnspan=2, sticky="w")
        self.existing_combo = ttk.Combobox(
            frame, textvariable=self.existing_name, values=pattern_names, state="readonly", width=45,
        )
        self.existing_combo.grid(row=5, column=0, columnspan=2, sticky="ew", padx=(24, 0), pady=(4, 4))
        self.warning = ttk.Label(
            frame,
            text="คำเตือน: Events และการตั้งค่าของ Pattern ที่เลือกจะถูกแทนที่ด้วยงานอัดนี้",
            foreground="#b91c1c",
        )
        self.warning.grid(row=6, column=0, columnspan=2, sticky="w", padx=(24, 0), pady=(0, 14))

        ttk.Button(frame, text="เก็บเป็น Draft", command=self._keep_draft).grid(row=7, column=0, sticky="w")
        self.discard_button = ttk.Button(
            frame, text="ไม่บันทึก (ทิ้งงานอัด)", style="Danger.TButton", command=self._discard,
        )
        self.discard_button.grid(row=7, column=1, padx=10)
        ttk.Button(frame, text="บันทึกตามที่เลือก", style="Primary.TButton", command=self._save).grid(row=7, column=2, sticky="e")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)

        self._refresh()
        self.window.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.window.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.window.winfo_height()) // 2)
        self.window.geometry(f"+{x}+{y}")
        self.window.grab_set()
        self.new_entry.focus_set()
        self.new_entry.selection_range(0, "end")

    def _refresh(self) -> None:
        is_new = self.mode.get() == "new"
        self.new_entry.configure(state="normal" if is_new else "disabled")
        self.existing_combo.configure(state="disabled" if is_new else "readonly")
        self.warning.configure(foreground="#9ca3af" if is_new else "#b91c1c")

    def _save(self) -> None:
        if self.mode.get() == "new":
            raw_name = self.new_name.get().strip()
            name = safe_pattern_filename(raw_name)
            if not raw_name:
                messagebox.showwarning("ยังไม่มีชื่อ", "กรุณาใส่ชื่อ Pattern ใหม่", parent=self.window)
                return
            if name in self.pattern_names:
                messagebox.showwarning(
                    "ชื่อซ้ำ",
                    "ชื่อนี้มีอยู่แล้ว หากต้องการเขียนทับให้เลือก ‘บันทึกทับ Pattern ที่มีอยู่’",
                    parent=self.window,
                )
                return
            self.result = "new", name
        else:
            name = self.existing_name.get().strip()
            if not name:
                messagebox.showwarning("ยังไม่ได้เลือก", "กรุณาเลือก Pattern ที่ต้องการบันทึกทับ", parent=self.window)
                return
            self.result = "existing", name
        self.window.destroy()

    def _keep_draft(self) -> None:
        self.result = None
        self.window.destroy()

    def _discard(self) -> None:
        if not messagebox.askyesno(
            "ทิ้งงานอัดนี้?",
            "งานอัดล่าสุดจะไม่ถูกบันทึกและจะกู้คืนจาก Draft ไม่ได้\n\nยืนยันว่าต้องการทิ้งหรือไม่?",
            parent=self.window,
        ):
            return
        self.result = "discard", ""
        self.window.destroy()

    def show(self) -> tuple[str, str] | None:
        self.parent.wait_window(self.window)
        return self.result
