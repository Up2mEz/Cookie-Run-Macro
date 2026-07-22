from __future__ import annotations

import tkinter as tk
import ctypes
import os
from pathlib import Path
from tkinter import messagebox

from ui.main_window import MainWindow


def enable_dpi_awareness() -> None:
    """ทำให้พิกัด pynput และ Win32 Window Rect ใช้ physical pixel ชุดเดียวกัน."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        MainWindow(root, Path(__file__).resolve().parent)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("MuMu Pattern Studio", f"เปิดโปรแกรมไม่สำเร็จ:\n{exc}", parent=root)
        root.destroy()
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
