from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ScrollableFrame(ttk.Frame):
    """Notebook page ที่ปรับความกว้างตามหน้าต่างและเลื่อนแนวตั้งได้."""

    def __init__(self, parent: tk.Misc, *, padding: int = 12) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background="#f4f6f8")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, padding=padding)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._mousewheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

    def _content_changed(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_changed(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=max(1, int(event.width)))

    def _mousewheel(self, event: tk.Event) -> str:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"
