from __future__ import annotations

import io
import os
import threading
import time
import tkinter as tk
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from PIL import Image, ImageTk

from adb_manager import ADBError, ADBManager, DeviceInfo, discover_adb_paths
from builtin_assets import DEFAULT_RESULT_ROI, DEFAULT_RESULT_TEMPLATE, ensure_builtin_assets
from config_manager import ConfigManager
from event_model import EventValidationError, normalize_event, normalize_pattern, normalize_safe_zones, remove_safe_zone
from input_keys import recorder_key_name
from pattern_store import PatternStore, PatternStoreError, safe_pattern_filename
from pause_profiles import DEFAULT_PAUSE_PROFILE, PAUSE_TEMPLATE_SIZE, PauseProfileError, PauseProfileStore
from player import Player
from recorder import Recorder, RecorderError
from state import AppState, StateMachine
from sync_detector import ROI, ResultDetector, SyncDetector, SyncError, SyncResult, crop_roi, decode_png, is_real_fast_roi_source, stable_sync_target, summarize_sync_samples, wait_for_sync_with_retries
from ui.event_dialog import EventDialog, SafeZoneDialog
from ui.live_view import LiveView
from ui.roi_selector import ROISelector
from ui.recording_save_dialog import RecordingSaveDialog
from ui.timeline_editor import TimelineEditor
from ui.responsive import ScrollableFrame
from mumu_window import MuMuFastCaptureError, MuMuROICapture, activate_mumu_window, is_mumu_foreground, map_viewport_point, viewport_at

try:
    from pynput import keyboard, mouse
except ImportError:  # โปรแกรมยังเปิดได้เพื่อให้ผู้ใช้ตั้งค่า/อ่าน Pattern
    keyboard = None
    mouse = None


SYNC_UI_FIELDS = ("mode", "threshold", "poll_ms", "consecutive_matches", "timeout_seconds", "offset_ms")


def saved_sync_values(sync: dict) -> dict[str, str]:
    """คืนค่าที่บันทึกไว้สำหรับ UI โดยไม่แอบแทนด้วย preset."""
    return {key: str(sync.get(key, "")) for key in SYNC_UI_FIELDS}


class MainWindow:
    def __init__(self, root: tk.Tk, project_dir: str | Path) -> None:
        self.root = root
        self.project_dir = Path(project_dir).resolve()
        ensure_builtin_assets(self.project_dir)
        self.config = ConfigManager(self.project_dir / "config.json")
        self.store = PatternStore(self.project_dir)
        self.pause_profiles = PauseProfileStore(self.project_dir)
        self.state = StateMachine(self._state_changed)
        self.current_pattern: dict | None = None
        self.devices: dict[str, DeviceInfo] = {}
        self.player: Player | None = None
        self.active_adb: ADBManager | None = None
        self.recorder = Recorder(self.state)
        self.record_shell = None
        self.hotkey_listener = None
        self.mouse_listener = None
        self.mouse_press: tuple[tuple[int, int], float] | None = None
        self.recording_started_wall: datetime | None = None
        self.operation_token = 0
        self.roi_photo = None
        self.live_view: LiveView | None = None
        self.playback_snapshot: dict = {}
        self.recording_context: tuple[ADBManager, str, dict] | None = None
        self.record_result_watch_started = False
        self.result_roi_photo = None
        self.pending_recording: tuple[dict, list[str], dict] | None = None
        self.record_save_dialog_open = False

        self.root.title("MuMu Pattern Studio")
        window = self.config.data.get("window", {})
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        initial_width = min(int(window.get("width", 1180)), max(900, screen_width - 40))
        initial_height = min(int(window.get("height", 760)), max(620, screen_height - 70))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_style()
        self._create_variables()
        self._build_ui()
        self._bind_hotkeys()
        self._refresh_pattern_names()
        self.root.after(150, self._initial_load)
        self.root.after(100, self._update_recording_monitor)
        self.root.after(100, self._update_playback_monitor)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Hint.TLabel", foreground="#4b5563")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 7))
        style.configure("Danger.TButton", foreground="#9b1c1c", padding=(12, 7))
        style.configure("Quick.TButton", font=("Segoe UI", 12, "bold"), padding=(12, 7))
        style.configure("QuickDanger.TButton", foreground="#9b1c1c", font=("Segoe UI", 11, "bold"), padding=(14, 11))
        style.configure("Step.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
        style.configure("TNotebook.Tab", padding=(11, 6), font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=25, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Horizontal.TProgressbar", thickness=12, background="#2563eb")
        style.configure("Hud.TFrame", relief="solid", borderwidth=1)

    def _create_variables(self) -> None:
        self.adb_var = tk.StringVar(value=str(self.config.data.get("adb_path", "")))
        self.ports_var = tk.StringVar(value=",".join(map(str, self.config.data.get("candidate_ports", [5557, 16416]))))
        self.device_var = tk.StringVar(value=str(self.config.data.get("selected_serial", "")))
        self.device_detail_var = tk.StringVar(value="ยังไม่ได้ทดสอบอุปกรณ์")
        self.pattern_var = tk.StringVar()
        self.pause_profile_var = tk.StringVar(value=DEFAULT_PAUSE_PROFILE)
        self.repeat_var = tk.IntVar(value=1)
        self.loop_forever_var = tk.BooleanVar(value=False)
        self.loop_interval_var = tk.StringVar(value="1000")
        self.pre_sync_extra_var = tk.StringVar(value="0")
        self.sync_each_loop_var = tk.BooleanVar(value=True)
        self.sync_vars = {
            "mode": tk.StringVar(value="manual_f9"),
            "threshold": tk.StringVar(value="0.82"),
            "poll_ms": tk.StringVar(value="20"),
            "consecutive_matches": tk.StringVar(value="2"),
            "timeout_seconds": tk.StringVar(value="25"),
            "offset_ms": tk.StringVar(value="300"),
        }
        self.post_game_vars = {
            "enabled": tk.BooleanVar(value=True),
            "threshold": tk.StringVar(value="0.86"),
            "poll_ms": tk.StringVar(value="700"),
            "consecutive_matches": tk.StringVar(value="3"),
            "min_gameplay_seconds": tk.StringVar(value="15"),
            "timeout_seconds": tk.StringVar(value="45"),
            "pause_absent_threshold": tk.StringVar(value="0.80"),
        }
        self.control_vars = {
            "jump_x": tk.StringVar(value="160"),
            "jump_y": tk.StringVar(value="635"),
            "slide_x": tk.StringVar(value="1115"),
            "slide_y": tk.StringVar(value="635"),
        }
        self.rapid_tap_var = tk.BooleanVar(value=True)
        self.rapid_gap_var = tk.StringVar(value="180")
        self.quick_connection_var = tk.StringVar(value="○ ยังไม่ได้เชื่อมต่อ MuMu")
        self.quick_pattern_var = tk.StringVar(value="○ กำลังเตรียม Pattern เริ่มต้น")
        self.quick_action_var = tk.StringVar(value="พร้อม: กดเชื่อมต่อก่อน แล้วค่อยอัดหรือเล่น")
        self.state_var = tk.StringVar(value=AppState.IDLE.value)
        self.similarity_var = tk.StringVar(value="—")
        self.result_similarity_var = tk.StringVar(value="XP — • Pause —")
        self.result_status_var = tk.StringVar(value="Result detector: พร้อมตรวจ XP หลังเริ่มด่าน")
        self.round_var = tk.StringVar(value="—")
        self.error_var = tk.StringVar(value="ไม่มี")
        self.message_var = tk.StringVar(value="พร้อมใช้งาน — เริ่มจากแท็บ 1. การเชื่อมต่อ")
        self.recording_timer_var = tk.StringVar(value="รอเริ่มอัด  00:00.000")
        self.recording_detail_var = tk.StringVar(value="Events 0 • Jump 0 • Slide 0 • Tap 0 • Hold 0")
        self.recording_started_var = tk.StringVar(value="ยังไม่ได้เริ่มจับเวลา")
        self.mouse_capture_var = tk.StringVar(value="Mouse recorder: รอเริ่มอัด")
        self.keyboard_capture_var = tk.StringVar(value="Keyboard recorder: รองรับ J/K และ VK 74/75")
        self.jump_capture_var = tk.StringVar(value="Jump detector: รอ Single / Double Jump")
        self.playback_progress_var = tk.DoubleVar(value=0.0)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=(16, 12))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="MuMu Pattern Studio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            shell,
            text="อัด Jump/Slide/Tap/Hold แบบ deterministic • สุ่มเฉพาะ Safe Zone • จอเป้าหมาย 1280×720",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 10))
        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill="both", expand=True)
        self.quick_page = ScrollableFrame(self.notebook, padding=14)
        self.quick_tab = self.quick_page.content
        self.library_tab = ttk.Frame(self.notebook, padding=14)
        self.connection_tab = ttk.Frame(self.notebook, padding=14)
        self.studio_page = ScrollableFrame(self.notebook, padding=12)
        self.studio_tab = self.studio_page.content
        self.events_page = ScrollableFrame(self.notebook, padding=14)
        self.events_tab = self.events_page.content
        self.notebook.add(self.quick_page, text="เริ่มใช้งานง่าย")
        self.notebook.add(self.library_tab, text="Library มาโคร")
        self.notebook.add(self.connection_tab, text="ตั้งค่าการเชื่อมต่อ")
        self.notebook.add(self.studio_page, text="ตั้งค่า Pattern ละเอียด")
        self.notebook.add(self.events_page, text="Random และ Safe Zone (ขั้นสูง)")
        self._build_quick_tab()
        self._build_library_tab()
        self._build_connection_tab()
        self._build_studio_tab()
        self._build_events_tab()
        self._build_status(shell)

    def _build_quick_tab(self) -> None:
        tab = self.quick_tab
        ttk.Label(tab, text="เริ่มจากหน้านี้ — ใช้เพียง 3 ขั้นตอน", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(
            tab,
            text="ตั้งค่าจากภาพเกม 1280×720 ให้แล้ว: Jump (160,635) • Slide (1115,635) • Pause ROI (1165,5,62×62)",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 14))

        connect = ttk.LabelFrame(tab, text="1  เชื่อมต่อ MuMu", padding=8, style="Step.TLabelframe")
        connect.pack(fill="x", pady=5)
        ttk.Button(connect, text="ค้นหา ADB และเชื่อมต่ออัตโนมัติ", style="Quick.TButton", command=self._quick_connect).grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        connection_status = ttk.Label(connect, textvariable=self.quick_connection_var, font=("Segoe UI", 10), wraplength=520)
        connection_status.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Button(connect, text="เปิด Preview (ตรวจภาพ)", command=self._open_live_view).grid(row=1, column=1, sticky="w")
        ttk.Button(connect, text="เลือก ADB เอง", command=lambda: self.notebook.select(self.connection_tab)).grid(row=1, column=2, sticky="e")
        connect.columnconfigure(1, weight=1)

        pattern = ttk.LabelFrame(tab, text="2  Pattern พร้อมใช้", padding=8, style="Step.TLabelframe")
        pattern.pack(fill="x", pady=8)
        ttk.Button(pattern, text="ใช้ค่าจากภาพนี้", style="Quick.TButton", command=self._quick_prepare_pattern).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(pattern, textvariable=self.quick_pattern_var, font=("Segoe UI", 10), wraplength=520).grid(row=0, column=1, sticky="ew")
        ttk.Button(pattern, text="ปรับละเอียด", command=lambda: self.notebook.select(self.studio_page)).grid(row=0, column=2, sticky="e", padx=(8, 0))
        pattern.columnconfigure(1, weight=1)

        actions = ttk.LabelFrame(tab, text="3  อัดหรือเล่น", padding=8, style="Step.TLabelframe")
        actions.pack(fill="both", expand=True, pady=5)
        monitor = ttk.Frame(actions, padding=(10, 7), style="Hud.TFrame")
        monitor.pack(fill="x", padx=10, pady=(2, 5))
        ttk.Label(monitor, textvariable=self.recording_timer_var, font=("Consolas", 17, "bold"), foreground="#b91c1c").pack()
        ttk.Label(monitor, textvariable=self.recording_detail_var, font=("Segoe UI", 10, "bold")).pack()
        ttk.Label(monitor, textvariable=self.recording_started_var, style="Hint.TLabel").pack()
        ttk.Progressbar(monitor, variable=self.playback_progress_var, maximum=100, mode="determinate").pack(fill="x", padx=8, pady=(3, 2))
        ttk.Label(monitor, textvariable=self.mouse_capture_var, foreground="#1d4ed8").pack()
        ttk.Label(monitor, textvariable=self.keyboard_capture_var, foreground="#6d28d9").pack()
        ttk.Label(monitor, textvariable=self.jump_capture_var, foreground="#0369a1").pack()
        ttk.Label(monitor, textvariable=self.result_status_var, foreground="#047857").pack()
        loop_row = ttk.Frame(monitor)
        loop_row.pack(anchor="center", pady=(3, 0))
        ttk.Checkbutton(loop_row, text="Loop จน F8", variable=self.loop_forever_var).grid(row=0, column=0, padx=4)
        ttk.Label(loop_row, text="รอบ").grid(row=0, column=1)
        ttk.Spinbox(loop_row, from_=1, to=999, textvariable=self.repeat_var, width=4).grid(row=0, column=2, padx=(3, 7))
        ttk.Label(loop_row, text="Delay ms").grid(row=0, column=3)
        ttk.Entry(loop_row, textvariable=self.loop_interval_var, width=7).grid(row=0, column=4, padx=(3, 7))
        ttk.Checkbutton(loop_row, text="Sync/รอบ", variable=self.sync_each_loop_var).grid(row=0, column=5)
        ttk.Label(loop_row, text="ก่อน Sync +ms").grid(row=0, column=6, padx=(10, 0))
        ttk.Entry(loop_row, textvariable=self.pre_sync_extra_var, width=8).grid(row=0, column=7, padx=(3, 0))
        buttons = ttk.Frame(actions)
        buttons.pack(fill="x", pady=(3, 4))
        ttk.Button(buttons, text="● เริ่มอัด  F6", style="Quick.TButton", command=self._start_recording).grid(row=0, column=0, sticky="ew", padx=4, pady=3)
        ttk.Button(buttons, text="■ หยุดอัด / เลือกที่บันทึก  F7", style="Quick.TButton", command=self._finish_recording).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(buttons, text="▶ เล่น Pattern", style="Quick.TButton", command=self._play).grid(row=1, column=0, sticky="ew", padx=4, pady=3)
        ttk.Button(buttons, text="■ ยกเลิกฉุกเฉิน  F8", style="QuickDanger.TButton", command=self._emergency_stop).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        for column in range(2):
            buttons.columnconfigure(column, weight=1)
        ttk.Label(actions, textvariable=self.quick_action_var, font=("Segoe UI", 11, "bold"), anchor="center").pack(fill="x", pady=5)
        ttk.Label(
            actions,
            text="ตอนอัด: F6 เริ่ม → เล่นบน MuMu → F7 หยุดแล้วเลือกว่าจะสร้าง Pattern ใหม่หรือบันทึกทับอันไหน • F8 ยกเลิก",
            justify="center",
            wraplength=850,
        ).pack(fill="x", pady=6)
        ttk.Label(actions, text="หมายเหตุ: ถ้า Pause ในเกมเปลี่ยนหน้าตา ให้ไป ‘ตั้งค่า Pattern ละเอียด’ แล้ว Capture Template ใหม่", style="Hint.TLabel").pack()

    def _build_library_tab(self) -> None:
        tab = self.library_tab
        ttk.Label(tab, text="Library เก็บมาโคร", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(tab, text="ดับเบิลคลิกเพื่อ Load • Export แบบ ZIP จะรวม Pause template ไปด้วย", style="Hint.TLabel").pack(anchor="w", pady=(2, 10))
        columns = ("name", "events", "duration", "plays", "last_played")
        self.library_tree = ttk.Treeview(tab, columns=columns, show="headings", height=16)
        for column, title, width in zip(columns, ("ชื่อมาโคร", "Events", "ความยาว", "เล่นแล้ว", "เล่นล่าสุด"), (300, 80, 100, 90, 240)):
            self.library_tree.heading(column, text=title)
            self.library_tree.column(column, width=width, anchor="w" if column in {"name", "last_played"} else "center", stretch=column == "name")
        self.library_tree.pack(fill="both", expand=True)
        library_xscroll = ttk.Scrollbar(tab, orient="horizontal", command=self.library_tree.xview)
        self.library_tree.configure(xscrollcommand=library_xscroll.set)
        library_xscroll.pack(fill="x")
        self.library_tree.bind("<Double-1>", lambda _event: self._library_load())
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(8, 0))
        for text, command in (
            ("บันทึกปัจจุบัน", self._save_current),
            ("Load", self._library_load),
            ("เปลี่ยนชื่อ", self._library_rename),
            ("ลบ", self._library_delete),
            ("Import", self._library_import),
            ("Export", self._library_export),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 6))

    def _build_connection_tab(self) -> None:
        tab = self.connection_tab
        ttk.Label(tab, text="ขั้นที่ 1: หา ADB แล้วเลือก MuMu Device", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )
        ttk.Label(tab, text="ADB executable").grid(row=1, column=0, sticky="w", pady=5)
        self.adb_combo = ttk.Combobox(tab, textvariable=self.adb_var)
        self.adb_combo.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(tab, text="ค้นหา ADB อัตโนมัติ", command=self._detect_adb).grid(row=1, column=2, padx=6)
        ttk.Button(tab, text="Browse…", command=self._browse_adb).grid(row=1, column=3)
        ttk.Label(tab, text="Candidate ports").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(tab, textvariable=self.ports_var).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(tab, text="คั่นด้วย comma เช่น 5557,16416", style="Hint.TLabel").grid(row=2, column=2, columnspan=2, sticky="w")
        ttk.Label(tab, text="MuMu serial").grid(row=3, column=0, sticky="w", pady=5)
        self.device_combo = ttk.Combobox(tab, textvariable=self.device_var)
        self.device_combo.grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Button(tab, text="ค้นหา MuMu Devices", command=self._detect_devices).grid(row=3, column=2, padx=6)
        ttk.Button(tab, text="ทดสอบอุปกรณ์", command=self._test_device).grid(row=3, column=3)
        info = ttk.LabelFrame(tab, text="ผลการทดสอบ", padding=12)
        info.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(14, 6))
        ttk.Label(info, textvariable=self.device_detail_var, justify="left", font=("Consolas", 10)).pack(anchor="w")
        ttk.Label(
            tab,
            text="ถ้าค้นหาไม่พบ: เปิด MuMu → Settings → Others → ADB แล้วใส่ path/serial เองได้ ช่อง serial แก้ไขได้เสมอ",
            style="Hint.TLabel",
            wraplength=900,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=8)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(4, weight=1)

    def _build_studio_tab(self) -> None:
        tab = self.studio_tab
        pattern_box = ttk.LabelFrame(tab, text="Pattern", padding=10)
        pattern_box.pack(fill="x")
        self.pattern_combo = ttk.Combobox(pattern_box, textvariable=self.pattern_var, state="readonly", width=28)
        self.pattern_combo.pack(side="left", fill="x", expand=True)
        self.pattern_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_selected_pattern())
        for text, command in (
            ("ใหม่", self._new_pattern), ("เปลี่ยนชื่อ", self._rename_pattern), ("ทำสำเนา", self._duplicate_pattern), ("ลบ", self._delete_pattern)
        ):
            ttk.Button(pattern_box, text=text, command=command).pack(side="left", padx=(6, 0))

        content = ttk.Frame(tab)
        content.pack(fill="both", expand=True, pady=10)
        sync_box = ttk.LabelFrame(content, text="Auto Sync — ปุ่ม Pause", padding=10)
        sync_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ttk.Label(sync_box, text="Pause ของ Stage").grid(row=0, column=0, sticky="w", pady=4)
        self.pause_profile_combo = ttk.Combobox(
            sync_box, textvariable=self.pause_profile_var, state="readonly",
        )
        self.pause_profile_combo.grid(row=0, column=1, sticky="ew", pady=4)
        self.pause_profile_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_pause_profile())
        ttk.Label(
            sync_box,
            text="เลือก Template ตาม Stage; ค่าเริ่มต้นยังใช้ได้กับด่านที่ปุ่มเหมือนเดิม",
            style="Hint.TLabel", wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 5))
        fields = [
            ("Mode", "mode", ("auto_pause_icon", "manual_f9")),
            ("Threshold (0.50–1.00)", "threshold", None),
            ("Poll (20–1000 ms)", "poll_ms", None),
            ("พบต่อเนื่อง (1–5)", "consecutive_matches", None),
            ("ลองตรวจใหม่ทุก (5–120 s)", "timeout_seconds", None),
            ("ดีเลย์หลังพบ Pause (0–3000 ms)", "offset_ms", None),
        ]
        for row, (label, key, values) in enumerate(fields, start=2):
            ttk.Label(sync_box, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if values:
                widget = ttk.Combobox(sync_box, textvariable=self.sync_vars[key], values=values, state="readonly")
            else:
                widget = ttk.Entry(sync_box, textvariable=self.sync_vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(sync_box, text="เพิ่มเวลาก่อน Auto Sync เท่านั้น (ms)").grid(row=8, column=0, sticky="w", pady=4)
        ttk.Entry(sync_box, textvariable=self.pre_sync_extra_var).grid(row=8, column=1, sticky="ew", pady=4)
        ttk.Label(
            sync_box,
            text="เพิ่มเวลาหลัง Event ก่อน Sync ตัวสุดท้าย โดยไม่ขยับ Event หลัง Sync/หลังจบเกม",
            style="Hint.TLabel", wraplength=520,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Button(sync_box, text="สร้าง/อัปเดต Pause ของ Stage", command=self._capture_template).grid(row=10, column=0, sticky="ew", pady=(10, 4))
        ttk.Button(sync_box, text="ทดสอบจริง 8 เฟรม (เปิด MuMu อัตโนมัติ)", command=self._test_sync).grid(row=10, column=1, sticky="ew", padx=(6, 0), pady=(10, 4))
        ttk.Button(sync_box, text="เร็วสุด • Delay 0 ms", command=self._apply_accurate_sync_preset).grid(
            row=11, column=0, sticky="ew", pady=4
        )
        ttk.Button(sync_box, text="คงที่ • Delay 300 ms (แนะนำ)", command=self._apply_stable_sync_preset).grid(
            row=11, column=1, sticky="ew", padx=(6, 0), pady=4
        )
        ttk.Label(
            sync_box,
            text="จุดเริ่ม Pattern = Pause เฟรมแรก + Delay; หากยังไม่พบ ระบบเริ่มรอบตรวจใหม่เอง ไม่ค้างรอ F9",
            style="Hint.TLabel", wraplength=520,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(sync_box, text="Similarity รวม/รูปทรง:").grid(row=13, column=0, sticky="w", pady=4)
        ttk.Label(sync_box, textvariable=self.similarity_var, font=("Segoe UI", 11, "bold")).grid(row=13, column=1, sticky="w")
        ttk.Label(sync_box, text="Pause ROI preview:").grid(row=14, column=0, sticky="nw", pady=(8, 0))
        self.roi_preview = ttk.Label(sync_box, text="ยังไม่มี template", anchor="center", relief="sunken", padding=5)
        self.roi_preview.grid(row=14, column=1, sticky="w", pady=(8, 0))
        ttk.Label(
            sync_box,
            text="Auto Sync ทนพลาด: Fast ROI แข่งกับ ADB Raw เฉพาะช่วงรอ Pause; เส้นทางที่พบก่อนจะเริ่ม Pattern",
            style="Hint.TLabel",
            wraplength=520,
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=(8, 0))
        sync_box.columnconfigure(1, weight=1)
        self._refresh_pause_profiles()

        controls = ttk.LabelFrame(content, text="พิกัดควบคุม (จอ 1280×720)", padding=10)
        controls.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        for row, (key, label) in enumerate((
            ("jump_x", "Jump X"), ("jump_y", "Jump Y"), ("slide_x", "Slide X"), ("slide_y", "Slide Y")
        )):
            ttk.Label(controls, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(controls, textvariable=self.control_vars[key], width=12).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(
            controls,
            text="วัดจากภาพเกมที่ให้มา\nJump (160,635) • Slide (1115,635)",
            style="Hint.TLabel",
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 5))
        ttk.Checkbutton(controls, text="Tap Slide รัว → รวมเป็น Hold", variable=self.rapid_tap_var).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 2)
        )
        ttk.Label(controls, text="ช่วงรวม Tap (ms)").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(controls, textvariable=self.rapid_gap_var, width=12).grid(row=6, column=1, sticky="ew", pady=4)
        controls.columnconfigure(1, weight=1)
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        def reflow_studio(event: tk.Event) -> None:
            if event.width < 820:
                sync_box.grid_configure(row=0, column=0, padx=0, pady=(0, 8))
                controls.grid_configure(row=1, column=0, padx=0, pady=0)
                content.columnconfigure(0, weight=1)
                content.columnconfigure(1, weight=0)
            else:
                sync_box.grid_configure(row=0, column=0, padx=(0, 5), pady=0)
                controls.grid_configure(row=0, column=1, padx=(5, 0), pady=0)
                content.columnconfigure(0, weight=2)
                content.columnconfigure(1, weight=1)

        content.bind("<Configure>", reflow_studio)

        result_box = ttk.LabelFrame(tab, text="หลังจบเกม — ตรวจ XP แล้วหยุด Jump/Slide", padding=10)
        result_box.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(
            result_box,
            text="เปิดตรวจหน้า Result อัตโนมัติ (แนะนำ)",
            variable=self.post_game_vars["enabled"],
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        result_fields = (
            ("Threshold XP", "threshold"),
            ("Poll ms", "poll_ms"),
            ("พบต่อเนื่อง", "consecutive_matches"),
            ("เริ่มตรวจหลังเล่น (s)", "min_gameplay_seconds"),
            ("รอหลัง Event สุดท้าย (s)", "timeout_seconds"),
            ("Pause ต้องต่ำกว่า", "pause_absent_threshold"),
        )
        for index, (label, key) in enumerate(result_fields):
            row = 1 + index // 3
            column = (index % 3) * 2
            ttk.Label(result_box, text=label).grid(row=row, column=column, sticky="w", padx=(0, 4), pady=3)
            ttk.Entry(result_box, textvariable=self.post_game_vars[key], width=9).grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=3)
        ttk.Button(result_box, text="ใช้ XP มาตรฐานจากภาพนี้", command=self._use_default_result_template).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        ttk.Button(result_box, text="Capture กรอบ XP เอง", command=self._capture_result_template).grid(row=3, column=2, columnspan=2, sticky="ew", padx=6, pady=(8, 3))
        ttk.Button(result_box, text="ทดสอบหน้า Result ตอนนี้", command=self._test_result_detection).grid(row=3, column=4, columnspan=2, sticky="ew", pady=(8, 3))
        self.result_roi_preview = ttk.Label(result_box, text="XP template", anchor="center", relief="sunken", padding=5)
        self.result_roi_preview.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(result_box, textvariable=self.result_similarity_var, font=("Segoe UI", 10, "bold")).grid(row=4, column=2, columnspan=4, sticky="w", padx=8)
        ttk.Label(
            result_box,
            text="Logic: XP ต้องตรงหลายเฟรม + ปุ่ม Pause ต้องหาย • พบแล้ว timeline เกมถูกตัดทันที และเล่นเฉพาะ Tap/Hold phase หลังจบเกม",
            style="Hint.TLabel", wraplength=850,
        ).grid(row=5, column=0, columnspan=6, sticky="w", pady=(6, 0))
        for column in (1, 3, 5):
            result_box.columnconfigure(column, weight=1)

        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        ttk.Button(actions, text="● อัด (F6)", style="Primary.TButton", command=self._start_recording).pack(side="left")
        ttk.Button(actions, text="■ หยุดอัด / เลือกที่บันทึก (F7)", command=self._finish_recording).pack(side="left", padx=6)
        ttk.Button(actions, text="▶ เล่น", style="Primary.TButton", command=self._play).pack(side="left", padx=(12, 6))
        ttk.Label(actions, text="รอบ").pack(side="left")
        ttk.Spinbox(actions, from_=1, to=999, textvariable=self.repeat_var, width=5).pack(side="left", padx=5)
        ttk.Label(actions, text="Delay ms").pack(side="left")
        ttk.Entry(actions, textvariable=self.loop_interval_var, width=7).pack(side="left", padx=5)
        ttk.Checkbutton(actions, text="Loop ∞", variable=self.loop_forever_var).pack(side="left", padx=4)
        ttk.Checkbutton(actions, text="Sync/รอบ", variable=self.sync_each_loop_var).pack(side="left", padx=4)
        ttk.Button(actions, text="หยุดฉุกเฉิน (F8)", style="Danger.TButton", command=self._emergency_stop).pack(side="right")
        ttk.Button(actions, text="Manual Sync (F9)", command=self._manual_sync).pack(side="right", padx=6)

    def _build_events_tab(self) -> None:
        pattern_picker = ttk.LabelFrame(self.events_tab, text="Pattern ที่จะแก้ Safe Zone", padding=8)
        pattern_picker.pack(fill="x", pady=(0, 8))
        ttk.Label(pattern_picker, text="เลือก Pattern ก่อนลาก Timeline:").pack(side="left")
        self.safe_zone_pattern_combo = ttk.Combobox(
            pattern_picker, textvariable=self.pattern_var, state="readonly", width=34,
        )
        self.safe_zone_pattern_combo.pack(side="left", fill="x", expand=True, padx=8)
        self.safe_zone_pattern_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_zone_pattern())
        ttk.Button(pattern_picker, text="โหลด Pattern นี้", command=self._load_zone_pattern).pack(side="left")

        self.timeline_editor = TimelineEditor(
            self.events_tab,
            on_create=self._timeline_create_zone,
            on_update=self._timeline_update_zone,
            on_select=self._timeline_zone_selected,
            on_delete=self._delete_zone,
        )
        self.timeline_editor.pack(fill="x", pady=(0, 8))
        pane = ttk.Panedwindow(self.events_tab, orient="vertical")
        pane.pack(fill="both", expand=True)
        event_box = ttk.LabelFrame(pane, text="Events — Required หรือ Safe Random ตามที่เลือก", padding=8)
        zone_box = ttk.LabelFrame(pane, text="Safe Zones — เก็บแยกใน Pattern ปัจจุบัน", padding=8)
        pane.add(event_box, weight=3)
        pane.add(zone_box, weight=2)
        event_buttons = ttk.Frame(event_box)
        event_buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(event_buttons, text="เพิ่ม Event", command=self._add_event).pack(side="left")
        ttk.Button(event_buttons, text="แก้ไข Event ที่เลือก", command=self._edit_event).pack(side="left", padx=5)
        ttk.Button(event_buttons, text="ทำสำเนา", command=self._duplicate_event).pack(side="left")
        ttk.Button(event_buttons, text="ลบ", command=self._delete_event).pack(side="left", padx=5)
        ttk.Label(event_buttons, text="เลือกหรือดับเบิลคลิก Event เพื่อแก้ไข", style="Hint.TLabel").pack(side="right")
        columns = ("phase", "time", "class", "type", "action", "chance", "jitter", "duration", "zone", "validation")
        self.event_tree = ttk.Treeview(event_box, columns=columns, show="headings", height=10)
        headings = ("Phase", "Time", "Class", "Type", "Action/Options", "Chance", "Jitter", "Duration", "Safe Zone", "Validation")
        widths = (85, 70, 90, 95, 170, 60, 60, 70, 90, 80)
        for column, heading, width in zip(columns, headings, widths):
            self.event_tree.heading(column, text=heading)
            self.event_tree.column(column, width=width, anchor="center", stretch=column == "action")
        self.event_tree.pack(fill="both", expand=True)
        self.event_tree.bind("<Double-1>", lambda _event: self._edit_event())
        event_xscroll = ttk.Scrollbar(event_box, orient="horizontal", command=self.event_tree.xview)
        self.event_tree.configure(xscrollcommand=event_xscroll.set)
        event_xscroll.pack(fill="x")
        self.event_tree.tag_configure("required", foreground="#8a2c0d")
        self.event_tree.tag_configure("safe", foreground="#12633b")

        zone_columns = ("id", "start", "end", "label", "count")
        self.zone_tree = ttk.Treeview(zone_box, columns=zone_columns, show="headings", height=5)
        for column, heading, width in zip(zone_columns, ("ID", "Start", "End", "คำอธิบาย", "Safe Random"), (120, 80, 80, 400, 100)):
            self.zone_tree.heading(column, text=heading)
            self.zone_tree.column(column, width=width, stretch=column == "label", anchor="center" if column != "label" else "w")
        self.zone_tree.pack(fill="both", expand=True)
        self.zone_tree.bind("<<TreeviewSelect>>", self._zone_tree_selected)
        zone_xscroll = ttk.Scrollbar(zone_box, orient="horizontal", command=self.zone_tree.xview)
        self.zone_tree.configure(xscrollcommand=zone_xscroll.set)
        zone_xscroll.pack(fill="x")
        zone_buttons = ttk.Frame(zone_box)
        zone_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(zone_buttons, text="เพิ่ม Safe Zone", command=self._add_zone).pack(side="left")
        ttk.Button(zone_buttons, text="แก้ไข", command=self._edit_zone).pack(side="left", padx=5)
        ttk.Button(zone_buttons, text="ทำสำเนา", command=self._duplicate_zone).pack(side="left")
        self.delete_zone_button = ttk.Button(
            zone_buttons, text="ลบ Safe Zone ที่เลือก", style="Danger.TButton", command=self._delete_zone,
        )
        self.delete_zone_button.pack(side="left")
        ttk.Button(zone_buttons, text="บันทึก Pattern", command=self._save_current).pack(side="right")

    def _build_status(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, textvariable=self.message_var, anchor="w", padding=(8, 6), relief="groove").pack(fill="x", pady=(8, 3))
        status = ttk.Frame(parent)
        status.pack(fill="x")
        items = (
            ("State", self.state_var), ("Device", self.device_var), ("Pattern", self.pattern_var),
            ("Similarity", self.similarity_var), ("Round", self.round_var), ("Last error", self.error_var),
        )
        for label, variable in items:
            ttk.Label(status, text=f"{label}:", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 2))
            ttk.Label(status, textvariable=variable).pack(side="left")
        ttk.Label(status, text="F6 อัด • F7 หยุด/เลือกที่บันทึก • F8 ยกเลิก • F9 Sync • J Jump • K Slide", style="Hint.TLabel").pack(side="right")

    def _background(self, work: Callable, success: Callable | None = None, message: str = "กำลังทำงาน…") -> None:
        self.operation_token += 1
        token = self.operation_token
        self.message_var.set(message)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                def failed(error: Exception = exc) -> None:
                    if token != self.operation_token:
                        return
                    if isinstance(error, ADBError) and "ยกเลิก" in str(error):
                        self.state.transition(AppState.STOPPED, force=True)
                        self.message_var.set("ยกเลิกงานเบื้องหลังแล้ว")
                    else:
                        self._show_error(str(error))
                self.root.after(0, failed)
            else:
                def completed() -> None:
                    if token != self.operation_token:
                        if isinstance(result, tuple):
                            for item in result:
                                if hasattr(item, "close"):
                                    item.close()
                        return
                    success(result) if success else self.message_var.set("เสร็จแล้ว")
                self.root.after(0, completed)

        threading.Thread(target=runner, daemon=True).start()

    def _show_error(self, message: str, *, dialog: bool = True) -> None:
        self.error_var.set(message)
        self.message_var.set(f"เกิดข้อผิดพลาด: {message}")
        self.state.transition(AppState.ERROR, force=True)
        if dialog:
            messagebox.showerror("MuMu Pattern Studio", message, parent=self.root)

    def _state_changed(self, state: AppState) -> None:
        friendly = {
            AppState.IDLE: "พร้อมใช้งาน",
            AppState.DISCOVERING_ADB: "กำลังค้นหา ADB…",
            AppState.CONNECTING: "กำลังเชื่อมต่อ MuMu…",
            AppState.WAITING_FOR_AUTO_SYNC: "รอปุ่ม Pause — กด F9 เพื่อเริ่มเองได้",
            AppState.WAITING_FOR_MANUAL_SYNC: "รอกด F9 เพื่อเริ่มจับเวลา",
            AppState.RECORDING_WAITING_SYNC: "กำลังเก็บก่อน Sync — เล่นบน MuMu ได้ทันที",
            AppState.RECORDING: "กำลังอัด: J กระโดด • กด K ค้างสไลด์ • F7 หยุดแล้วเลือกที่บันทึก",
            AppState.PLAYING: "กำลังเล่น Pattern • F8 หยุดฉุกเฉิน",
            AppState.STOPPED: "หยุดแล้ว",
            AppState.ERROR: "พบข้อผิดพลาด — ดูข้อความด้านล่าง",
        }.get(state, state.value)

        def update() -> None:
            self.state_var.set(state.value)
            self.quick_action_var.set(friendly)

        self.root.after(0, update)

    def _parse_ports(self) -> list[int]:
        try:
            ports = [int(value.strip()) for value in self.ports_var.get().split(",") if value.strip()]
        except ValueError as exc:
            raise ValueError("Candidate ports ต้องเป็นตัวเลขและคั่นด้วย comma") from exc
        if not ports or any(not 1 <= port <= 65535 for port in ports):
            raise ValueError("กรุณาใส่ port 1–65535 อย่างน้อยหนึ่งค่า")
        return list(dict.fromkeys(ports))

    def _quick_connect(self) -> None:
        try:
            ports = self._parse_ports()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.state.transition(AppState.DISCOVERING_ADB, force=True)
        self.quick_connection_var.set("… กำลังค้นหา ADB และทดลองพอร์ต 5557/16416")

        def work() -> tuple[str, ADBManager, list[DeviceInfo]]:
            candidates = discover_adb_paths(self.adb_var.get().strip())
            if not candidates:
                raise ADBError("ไม่พบ ADB อัตโนมัติ กด ‘หาไม่เจอ? เลือก ADB เอง’ แล้ว Browse ไปที่ adb.exe ของ MuMu")
            usable_adb = False
            last_error = ""
            for path in candidates:
                manager = ADBManager(path)
                self.active_adb = manager
                if not manager.validate_executable():
                    continue
                usable_adb = True
                try:
                    devices = manager.discover_devices(ports)
                except ADBError as exc:
                    last_error = str(exc)
                    continue
                if devices:
                    return str(path), manager, devices
            if usable_adb:
                raise ADBError(last_error or "พบ ADB แต่ไม่พบ MuMu ที่พร้อมใช้ ตรวจว่าเปิด MuMu และเปิด ADB ใน Settings แล้ว")
            raise ADBError("พบไฟล์ ADB แต่เรียกใช้งานไม่ได้ กรุณาเลือก adb.exe ของ MuMu เอง")

        def success(result: tuple[str, ADBManager, list[DeviceInfo]]) -> None:
            adb_path, manager, devices = result
            self.active_adb = manager
            self.adb_var.set(adb_path)
            self.adb_combo.configure(values=[adb_path])
            self.devices = {device.serial: device for device in devices}
            self.device_combo.configure(values=list(self.devices))
            selected = devices[0]
            self.device_var.set(selected.serial)
            self._show_device(selected)
            self.state.transition(AppState.IDLE, force=True)
            self.quick_connection_var.set(f"✓ เชื่อมต่อแล้ว: {selected.serial} • {selected.model} • {selected.resolution_text}")
            self.message_var.set("เชื่อมต่อ MuMu สำเร็จ — ขั้นต่อไปกด ‘ใช้ค่าจากภาพนี้’")
            self._save_config()

        self._background(work, success, "กำลังค้นหา ADB และเชื่อมต่อ MuMu แบบอัตโนมัติ…")

    def _adb(self) -> ADBManager:
        path = self.adb_var.get().strip()
        if not path:
            raise ADBError("ยังไม่ได้เลือก ADB executable")
        adb = ADBManager(path)
        self.active_adb = adb
        if not adb.validate_executable():
            raise ADBError("ADB executable ใช้งานไม่ได้ กรุณาเลือก adb.exe หรือ adb_server.exe ที่ถูกต้อง")
        return adb

    @staticmethod
    def _probe_selected(adb: ADBManager, serial: str) -> DeviceInfo:
        if ":" in serial:
            adb.connect(serial)
        return adb.probe_device(serial)

    def _detect_adb(self) -> None:
        self.state.transition(AppState.DISCOVERING_ADB, force=True)

        def work() -> list[str]:
            valid = []
            for path in discover_adb_paths(self.adb_var.get().strip()):
                manager = ADBManager(path)
                self.active_adb = manager
                if manager.validate_executable():
                    valid.append(str(path))
            if not valid:
                raise ADBError("ไม่พบ ADB ที่ใช้งานได้ กรุณากด Browse แล้วเลือก adb.exe ของ MuMu")
            return valid

        def success(paths: list[str]) -> None:
            self.adb_combo.configure(values=paths)
            self.adb_var.set(paths[0])
            self.state.transition(AppState.IDLE, force=True)
            self.message_var.set(f"พบ ADB ที่ใช้งานได้ {len(paths)} รายการ")
            self._save_config()

        self._background(work, success, "กำลังค้นหา ADB เฉพาะโฟลเดอร์ MuMu/Netease…")

    def _browse_adb(self) -> None:
        path = filedialog.askopenfilename(title="เลือก ADB executable", filetypes=(("ADB executable", "*.exe"), ("ทุกไฟล์", "*.*")))
        if path:
            self.adb_var.set(path)
            self._save_config()

    def _detect_devices(self) -> None:
        try:
            ports = self._parse_ports()
            adb = self._adb()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.state.transition(AppState.CONNECTING, force=True)

        def work() -> list[DeviceInfo]:
            devices = adb.discover_devices(ports)
            if not devices:
                raise ADBError("ไม่พบ MuMu device ที่ boot พร้อมใช้งาน ลองตรวจ ADB port ใน MuMu Settings")
            return devices

        def success(devices: list[DeviceInfo]) -> None:
            self.devices = {device.serial: device for device in devices}
            self.device_combo.configure(values=[device.serial for device in devices])
            if len(devices) == 1 or self.device_var.get() not in self.devices:
                self.device_var.set(devices[0].serial)
            self._show_device(self.devices[self.device_var.get()])
            self.state.transition(AppState.IDLE, force=True)
            self.message_var.set(f"พบอุปกรณ์พร้อมใช้ {len(devices)} เครื่อง")
            self._save_config()

        self._background(work, success, f"กำลังทดลองพอร์ต {', '.join(map(str, ports))} และตรวจ boot/resolution…")

    def _show_device(self, device: DeviceInfo) -> None:
        self.device_detail_var.set(
            f"Serial: {device.serial}\nState: {device.state}\nBoot completed: {1 if device.boot_completed else 0}\n"
            f"Model: {device.model}\nResolution: {device.resolution_text}\nADB executable: {self.adb_var.get()}"
        )
        self.quick_connection_var.set(f"✓ เชื่อมต่อแล้ว: {device.serial} • {device.model} • {device.resolution_text}")

    def _test_device(self) -> None:
        try:
            adb, serial = self._adb(), self.device_var.get().strip()
            if not serial:
                raise ADBError("กรุณาเลือกหรือกรอก serial")
        except Exception as exc:
            self._show_error(str(exc))
            return

        def success(device: DeviceInfo) -> None:
            self.devices[device.serial] = device
            self._show_device(device)
            self.state.transition(AppState.IDLE, force=True)
            self.message_var.set("อุปกรณ์พร้อมใช้งาน")
            self._save_config()

        self._background(lambda: self._probe_selected(adb, serial), success, "กำลังทดสอบ state, boot และ resolution…")

    def _open_live_view(self) -> None:
        if self.live_view and self.live_view.winfo_exists():
            self.live_view.lift()
            self.live_view.focus_force()
            return
        try:
            adb = self._adb()
            serial = self.device_var.get().strip()
            if not serial or ":" not in serial:
                raise ADBError("ยังไม่ได้เลือก serial กด ‘ค้นหา ADB และเชื่อมต่ออัตโนมัติ’ ก่อน")
        except Exception as exc:
            self._show_error(str(exc))
            return

        def success(device: DeviceInfo) -> None:
            self._show_device(device)
            self.live_view = LiveView(
                self.root,
                str(adb.adb_path),
                serial,
                on_interaction=self._on_live_interaction,
                on_error=lambda error: self._show_error(error, dialog=False),
            )
            self.message_var.set("เปิด Preview แล้ว — ใช้ตรวจภาพ/พิกัดเท่านั้น; ตอนอัดให้เล่นบนหน้าต่าง MuMu จริง")

        self._background(lambda: self._probe_selected(adb, serial), success, "กำลังเปิด Preview จาก MuMu…")

    def _on_live_interaction(self, x: int, y: int, started_at: float, duration_ms: int) -> None:
        self._record_touch(x, y, started_at, duration_ms, "จอสด")

    def _record_touch(self, x: int, y: int, started_at: float, duration_ms: int, source: str) -> None:
        if not self.recorder.status()["active"] or not self.current_pattern:
            return
        recording_status = self.recorder.status()
        controls = self.current_pattern.get("controls", {})
        matches: list[tuple[float, str]] = []
        for action in ("jump", "slide"):
            point = controls.get(action, {})
            distance = ((x - int(point.get("x", -10000))) ** 2 + (y - int(point.get("y", -10000))) ** 2) ** 0.5
            matches.append((distance, action))
        distance, nearest_action = min(matches)
        if recording_status.get("post_game"):
            action = "hold" if duration_ms >= 250 else "tap"
        elif distance <= 150:
            action = nearest_action
        else:
            action = "hold" if duration_ms >= 250 else "tap"
        try:
            event, merged = self.recorder.record_external_tap(
                action,
                started_at=started_at,
                duration_ms=duration_ms,
                x=x if action in {"tap", "hold"} else None,
                y=y if action in {"tap", "hold"} else None,
            )
        except RecorderError as exc:
            self._show_error(str(exc), dialog=False)
            return
        if merged:
            self.message_var.set(f"รวม Tap รัวเป็น Hold แล้ว • {source} • Event #{self.recorder.status()['event_count']}")
        else:
            coordinate = f" ({x},{y})" if event.get("action") in {"tap", "hold"} else ""
            phase_text = {
                "pre_sync": "ก่อน Sync", "synced": "หลัง Sync", "post_game": "หลังจบเกม",
            }.get(event.get("phase"), "หลัง Sync")
            self.message_var.set(f"บันทึก {event['action'].upper()}{coordinate} จาก{source} • {phase_text} • Event #{self.recorder.status()['event_count']}")
        self.mouse_capture_var.set(f"จับได้: {source} ({x},{y}) • {event.get('phase', 'synced')}")
        if event.get("action") == "jump":
            self.jump_capture_var.set(self._jump_feedback(self.recorder.status()))

    @staticmethod
    def _jump_feedback(status: dict) -> str:
        count = int(status.get("counts", {}).get("jump", 0))
        if status.get("last_jump_kind") == "double":
            return f"Jump detector: DOUBLE JUMP • 2 Events แยกกัน • gap {int(status.get('last_jump_gap_ms', 0))} ms • Jump #{count}"
        return f"Jump detector: SINGLE JUMP • 1 Event • Jump #{count}"

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1000))
        minutes, remainder = divmod(milliseconds, 60_000)
        whole_seconds, millis = divmod(remainder, 1000)
        return f"{minutes:02d}:{whole_seconds:02d}.{millis:03d}"

    def _update_recording_monitor(self) -> None:
        try:
            status = self.recorder.status()
            if status["active"]:
                self.playback_progress_var.set(0)
                counts = status["counts"]
                self.recording_detail_var.set(
                    f"Events {status['event_count']} • Jump {counts['jump']} • Slide {counts['slide']} • "
                    f"Tap {counts['tap']} • Hold {counts['hold']} • ก่อน Sync {status['pre_sync_count']} • หลังจบ {status['post_game_count']}"
                )
                if status.get("post_game"):
                    self.recording_timer_var.set(f"● POST-GAME  {self._format_elapsed(status['post_game_elapsed_seconds'])}")
                    self.recording_started_var.set("พบ XP แล้ว • รับเฉพาะ Tap/Hold • F7 = หยุด/เลือก Pattern ปลายทาง")
                elif status["synced"]:
                    if self.recording_started_wall is None:
                        self.recording_started_wall = datetime.now().astimezone()
                    self.recording_timer_var.set(f"● REC  {self._format_elapsed(status['elapsed_seconds'])}")
                    self.recording_started_var.set(f"เริ่มจับเวลา {self.recording_started_wall.strftime('%H:%M:%S')} • F7 = หยุด/เลือก Pattern ปลายทาง")
                else:
                    self.recording_timer_var.set(f"● PRE-SYNC  {self._format_elapsed(status['armed_elapsed_seconds'])}")
                    self.recording_started_var.set(f"รอ Auto Sync/F9 • เก็บก่อน Sync แล้ว {status['pre_sync_count']} Events")
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self._update_recording_monitor)

    def _player_progress(self, snapshot: dict) -> None:
        self.playback_snapshot = dict(snapshot)
        if hasattr(self, "timeline_editor"):
            self.timeline_editor.update_playback(
                str(snapshot.get("phase", "")), float(snapshot.get("elapsed", 0.0)),
            )
        self._render_playback_progress()

    def _render_playback_progress(self) -> None:
        if not self.playback_snapshot:
            return
        snapshot = self.playback_snapshot
        phase = str(snapshot.get("phase", ""))
        elapsed = float(snapshot.get("elapsed", 0.0))
        duration = float(snapshot.get("duration", 0.0))
        if phase in {"waiting_sync", "waiting_result"} and self.player:
            elapsed += max(0.0, time.perf_counter() - float(snapshot.get("reported_at", time.perf_counter())))
        phase_names = {
            "pre_sync": "ก่อน Sync",
            "waiting_sync": "รอ Auto Sync / F9",
            "synced": "หลัง Sync",
            "waiting_result": "รอหน้า Result / XP",
            "post_game": "หลังจบเกม — Tap เท่านั้น",
            "result_timeout": "ไม่พบหน้า Result",
            "loop_delay": "พักก่อน Loop ถัดไป",
            "completed": "เล่นครบแล้ว",
            "stopped": "หยุดแล้ว",
            "error": "เล่นผิดพลาด",
        }
        phase_name = phase_names.get(phase, phase or "เตรียมเล่น")
        if phase == "waiting_sync":
            timer_text = f"▶ WAIT SYNC  {self._format_elapsed(elapsed)}"
            percent = 0.0
            remaining_text = "รอพบ Pause หรือกด F9"
        elif phase == "waiting_result":
            timer_text = f"▶ WAIT RESULT  {self._format_elapsed(elapsed)}"
            percent = min(100.0, elapsed / duration * 100) if duration > 0 else 0.0
            remaining_text = "กำลังหา XP • ไม่ส่ง Jump/Slide หลังตรวจพบ"
        elif phase in {"completed", "stopped", "error", "result_timeout"}:
            timer_text = f"{'✓' if phase == 'completed' else '■'} {phase_name}"
            percent = 100.0 if phase == "completed" else float(self.playback_progress_var.get())
            remaining_text = phase_name
        else:
            timer_text = f"▶ PLAY  {self._format_elapsed(elapsed)} / {self._format_elapsed(duration)}"
            percent = min(100.0, elapsed / duration * 100) if duration > 0 else 0.0
            remaining = max(0.0, duration - elapsed)
            remaining_text = f"เหลือ {self._format_elapsed(remaining)}"
        round_number = int(snapshot.get("round_number", 1))
        round_total = int(snapshot.get("round_total", 1))
        round_text = f"{round_number}/∞" if round_total == 0 else f"{round_number}/{round_total}"
        event_index = int(snapshot.get("event_index", 0))
        event_total = int(snapshot.get("event_total", 0))
        self.recording_timer_var.set(timer_text)
        self.recording_detail_var.set(f"รอบ {round_text} • ช่วง {phase_name} • Event {event_index}/{event_total}")
        self.recording_started_var.set(remaining_text)
        self.playback_progress_var.set(percent)
        if hasattr(self, "timeline_editor"):
            self.timeline_editor.update_playback(phase, elapsed)

    def _update_playback_monitor(self) -> None:
        try:
            if self.player and self.playback_snapshot:
                self._render_playback_progress()
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self._update_playback_monitor)

    def _initial_load(self) -> None:
        names = self.store.list_patterns()
        preferred = self.config.data.get("last_pattern", "")
        if names:
            self.pattern_var.set(preferred if preferred in names else names[0])
            self._load_selected_pattern()
        else:
            self._new_pattern(default_name="stage_01")

    @staticmethod
    def _default_pattern_payload(name: str) -> dict:
        return {
            "name": name,
            "device": {"preferred_serial": "", "resolution": {"width": 1280, "height": 720}},
            "controls": {
                "jump": {"x": 160, "y": 635},
                "slide": {"x": 1115, "y": 635},
            },
            "sync": {
                "mode": "auto_pause_icon",
                "profile_name": DEFAULT_PAUSE_PROFILE,
                "template_path": "templates/default_pause_1280x720.png",
                "roi": {"x": 1165, "y": 5, "width": 62, "height": 62},
                "poll_ms": 20,
                "threshold": 0.82,
                "consecutive_matches": 2,
                "timeout_seconds": 25,
                # Pattern ใหม่ใช้ช่วงกันชนคงที่; Pattern เก่ายังคงค่าที่บันทึกไว้.
                "offset_ms": 300,
                "manual_fallback": True,
            },
            "post_game": {
                "enabled": True,
                "mode": "xp_result",
                "template_path": DEFAULT_RESULT_TEMPLATE,
                "roi": deepcopy(DEFAULT_RESULT_ROI),
                "poll_ms": 700,
                "threshold": 0.86,
                "consecutive_matches": 3,
                "min_gameplay_seconds": 15,
                "timeout_seconds": 45,
                "pause_absent_threshold": 0.80,
            },
            "recording": {"rapid_tap_to_hold": True, "rapid_tap_gap_ms": 180},
            "playback": {"repeat_count": 1, "loop_forever": False, "loop_interval_ms": 1000, "pre_sync_extra_ms": 0, "sync_each_loop": True},
            "stats": {"play_count": 0, "last_played_at": ""},
            "events": [],
            "safe_zones": [],
        }

    def _quick_prepare_pattern(self) -> None:
        name = (self.current_pattern or {}).get("name", "stage_01")
        events = deepcopy((self.current_pattern or {}).get("events", []))
        safe_zones = deepcopy((self.current_pattern or {}).get("safe_zones", []))
        playback = deepcopy((self.current_pattern or {}).get("playback", {}))
        payload = self._default_pattern_payload(name)
        payload["events"] = events
        payload["safe_zones"] = safe_zones
        # ปุ่มตั้งค่าจากภาพมีหน้าที่เปลี่ยนพิกัด/Auto Sync เท่านั้น
        # ห้ามล้างค่ารอบ, delay หรือเวลาที่ผู้ใช้เพิ่มก่อน Sync
        payload["playback"].update(playback)
        try:
            self.current_pattern, warnings = normalize_pattern(payload)
            path, save_warnings = self.store.save(self.current_pattern)
        except (EventValidationError, PatternStoreError) as exc:
            self._show_error(str(exc))
            return
        self.pattern_var.set(self.current_pattern["name"])
        self._load_pattern_to_ui()
        self._refresh_pattern_names()
        self.quick_pattern_var.set(
            f"✓ {path.stem}: Jump (160,635) • Slide (1115,635) • Auto Pause พร้อม"
        )
        self.message_var.set("Pattern พร้อมใช้แล้ว — เชื่อมต่อ MuMu จากนั้นกด ‘เริ่มอัด’")
        all_warnings = warnings + save_warnings
        if all_warnings:
            self.message_var.set(self.message_var.get() + " • " + "; ".join(all_warnings))

    def _new_pattern(self, default_name: str | None = None) -> None:
        name = default_name or simpledialog.askstring("Pattern ใหม่", "ชื่อ Pattern (เช่น stage_01):", parent=self.root)
        if not name:
            return
        pattern, _ = normalize_pattern(self._default_pattern_payload(name))
        self.current_pattern = pattern
        self.pattern_var.set(pattern["name"])
        self._load_pattern_to_ui()
        self._save_current(quiet=True)
        self._refresh_pattern_names()
        self.message_var.set(f"สร้าง Pattern {pattern['name']} แล้ว — ตั้งค่า Sync หรือกด F6 เพื่ออัด")

    def _load_selected_pattern(self) -> None:
        name = self.pattern_var.get()
        if not name:
            return
        try:
            self.current_pattern, warnings = self.store.load(name)
        except (PatternStoreError, EventValidationError) as exc:
            self._show_error(str(exc))
            return
        self._load_pattern_to_ui()
        self.message_var.set("เปิด Pattern แล้ว" + (f" • {'; '.join(warnings)}" if warnings else ""))
        self.config.data["last_pattern"] = name
        self._save_config()

    def _load_pattern_to_ui(self) -> None:
        if not self.current_pattern:
            return
        sync = self.current_pattern["sync"]
        self._refresh_pause_profiles()
        selected_profile = str(sync.get("profile_name", "")).strip()
        if not selected_profile:
            try:
                for profile in self.pause_profiles.load():
                    if profile["template_path"] == sync.get("template_path") and profile["roi"] == sync.get("roi"):
                        selected_profile = profile["name"]
                        break
            except PauseProfileError:
                pass
        if selected_profile:
            sync["profile_name"] = selected_profile
        self.pause_profile_var.set(selected_profile or "Template เดิมของ Pattern")
        for key, value in saved_sync_values(sync).items():
            self.sync_vars[key].set(value)
        # ห้าม "อัปเกรด" ค่า Threshold/Poll เงียบ ๆ: ค่าที่ผู้ใช้บันทึกต้อง
        # กลับมาแสดงและถูกใช้ตรงตัว แม้จะต่างจาก preset ที่เราแนะนำ.
        post_game = self.current_pattern.get("post_game", {})
        for key, variable in self.post_game_vars.items():
            variable.set(post_game.get(key, variable.get()))
        controls = self.current_pattern["controls"]
        for action in ("jump", "slide"):
            for axis in ("x", "y"):
                self.control_vars[f"{action}_{axis}"].set(str(controls[action][axis]))
        recording = self.current_pattern.get("recording", {})
        self.rapid_tap_var.set(bool(recording.get("rapid_tap_to_hold", True)))
        self.rapid_gap_var.set(str(recording.get("rapid_tap_gap_ms", 180)))
        playback = self.current_pattern.get("playback", {})
        self.repeat_var.set(int(playback.get("repeat_count", 1)))
        self.loop_forever_var.set(bool(playback.get("loop_forever", False)))
        self.loop_interval_var.set(str(playback.get("loop_interval_ms", 1000)))
        self.pre_sync_extra_var.set(str(playback.get("pre_sync_extra_ms", 0)))
        self.sync_each_loop_var.set(bool(playback.get("sync_each_loop", True)))
        self._refresh_tables()
        self._update_roi_preview()
        self._update_result_roi_preview()
        controls = self.current_pattern["controls"]
        sync = self.current_pattern["sync"]
        ready = bool(sync.get("template_path") and sync.get("roi"))
        post_ready = bool(self.current_pattern.get("post_game", {}).get("template_path") and self.current_pattern.get("post_game", {}).get("roi"))
        self.quick_pattern_var.set(
            f"✓ {self.current_pattern['name']}: Jump ({controls['jump']['x']},{controls['jump']['y']}) • "
            f"Slide ({controls['slide']['x']},{controls['slide']['y']}) • "
            f"{'Auto Pause Fast ROI พร้อม' if ready else 'ยังไม่มี Pause template'} • "
            f"{'Result XP พร้อม' if post_ready else 'ยังไม่มี XP template'}"
        )

    def _collect_ui(self) -> dict:
        if not self.current_pattern:
            raise PatternStoreError("ยังไม่ได้เลือก Pattern")
        pattern = deepcopy(self.current_pattern)
        pattern["sync"].update({
            "mode": self.sync_vars["mode"].get(),
            "threshold": float(self.sync_vars["threshold"].get()),
            "poll_ms": int(self.sync_vars["poll_ms"].get()),
            "consecutive_matches": int(self.sync_vars["consecutive_matches"].get()),
            "timeout_seconds": int(self.sync_vars["timeout_seconds"].get()),
            "offset_ms": int(self.sync_vars["offset_ms"].get()),
            "manual_fallback": True,
            "profile_name": str(pattern["sync"].get("profile_name", "")),
        })
        pattern.setdefault("post_game", {}).update({
            "enabled": bool(self.post_game_vars["enabled"].get()),
            "mode": "xp_result",
            "threshold": float(self.post_game_vars["threshold"].get()),
            "poll_ms": int(self.post_game_vars["poll_ms"].get()),
            "consecutive_matches": int(self.post_game_vars["consecutive_matches"].get()),
            "min_gameplay_seconds": int(self.post_game_vars["min_gameplay_seconds"].get()),
            "timeout_seconds": int(self.post_game_vars["timeout_seconds"].get()),
            "pause_absent_threshold": float(self.post_game_vars["pause_absent_threshold"].get()),
        })
        for action in ("jump", "slide"):
            pattern["controls"][action] = {
                "x": int(self.control_vars[f"{action}_x"].get()),
                "y": int(self.control_vars[f"{action}_y"].get()),
            }
        pattern["recording"] = {
            "rapid_tap_to_hold": bool(self.rapid_tap_var.get()),
            "rapid_tap_gap_ms": int(self.rapid_gap_var.get()),
        }
        pattern["playback"] = {
            "repeat_count": int(self.repeat_var.get()),
            "loop_forever": bool(self.loop_forever_var.get()),
            "loop_interval_ms": int(self.loop_interval_var.get()),
            "pre_sync_extra_ms": int(self.pre_sync_extra_var.get()),
            "sync_each_loop": bool(self.sync_each_loop_var.get()),
        }
        pattern["device"]["preferred_serial"] = self.device_var.get().strip()
        return normalize_pattern(pattern)[0]

    def _save_current(self, quiet: bool = False) -> bool:
        try:
            self.current_pattern = self._collect_ui()
            path, warnings = self.store.save(self.current_pattern)
        except (ValueError, PatternStoreError, EventValidationError) as exc:
            self._show_error(str(exc))
            return False
        self.pattern_var.set(self.current_pattern["name"])
        self._refresh_pattern_names()
        if not quiet:
            self.message_var.set(f"บันทึก {path.name} แล้ว" + (f" • {'; '.join(warnings)}" if warnings else ""))
        return True

    def _refresh_pattern_names(self) -> None:
        names = self.store.list_patterns()
        self.pattern_combo.configure(values=names)
        if hasattr(self, "safe_zone_pattern_combo"):
            self.safe_zone_pattern_combo.configure(values=names)
        if hasattr(self, "library_tree"):
            self._refresh_library()

    def _load_zone_pattern(self) -> None:
        name = self.pattern_var.get().strip()
        if not name:
            messagebox.showinfo("เลือก Pattern", "กรุณาเลือก Pattern ที่ต้องการแก้ Safe Zone", parent=self.root)
            return
        self._load_selected_pattern()
        if self.current_pattern:
            self.message_var.set(f"โหลด {self.current_pattern['name']} สำหรับแก้ Safe Zone แล้ว")

    def _refresh_library(self) -> None:
        for item in self.library_tree.get_children():
            self.library_tree.delete(item)
        for name in self.store.list_patterns():
            try:
                pattern, _ = self.store.load(name)
                stats = pattern.get("stats", {})
                duration = max(
                    (float(event.get("at", 0)) + int(event.get("duration_ms", 0)) / 1000 for event in pattern.get("events", [])),
                    default=0.0,
                )
                self.library_tree.insert(
                    "", "end", iid=name,
                    values=(name, len(pattern.get("events", [])), self._format_elapsed(duration), stats.get("play_count", 0), stats.get("last_played_at", "—") or "—"),
                )
            except PatternStoreError:
                self.library_tree.insert("", "end", iid=name, values=(name, "ERROR", "—", "—", "ไฟล์เสียหาย"))

    def _selected_library_name(self) -> str | None:
        selection = self.library_tree.selection()
        if selection:
            return str(selection[0])
        return self.pattern_var.get() or None

    def _library_load(self) -> None:
        name = self._selected_library_name()
        if not name:
            return
        self.pattern_var.set(name)
        self._load_selected_pattern()
        self.notebook.select(self.quick_page)

    def _library_rename(self) -> None:
        name = self._selected_library_name()
        if not name:
            return
        self.pattern_var.set(name)
        self._load_selected_pattern()
        self._rename_pattern()

    def _library_delete(self) -> None:
        name = self._selected_library_name()
        if not name:
            return
        self.pattern_var.set(name)
        self._load_selected_pattern()
        self._delete_pattern()

    def _library_import(self) -> None:
        source = filedialog.askopenfilename(
            title="Import มาโคร",
            filetypes=(("MuMu Pattern Bundle", "*.zip"), ("Pattern JSON", "*.json"), ("ทุกไฟล์", "*.*")),
        )
        if not source:
            return
        try:
            path, warnings = self.store.import_pattern(source)
        except PatternStoreError as exc:
            self._show_error(str(exc))
            return
        self._refresh_pattern_names()
        self.pattern_var.set(path.stem)
        self._load_selected_pattern()
        self.message_var.set(f"Import {path.name} แล้ว" + (f" • {'; '.join(warnings)}" if warnings else ""))

    def _library_export(self) -> None:
        name = self._selected_library_name()
        if not name:
            messagebox.showinfo("Export", "กรุณาเลือกมาโครก่อน", parent=self.root)
            return
        destination = filedialog.asksaveasfilename(
            title="Export มาโคร",
            initialfile=f"{name}.zip",
            defaultextension=".zip",
            filetypes=(("MuMu Pattern Bundle", "*.zip"), ("Pattern JSON", "*.json")),
        )
        if not destination:
            return
        try:
            path = self.store.export_pattern(name, destination)
        except PatternStoreError as exc:
            self._show_error(str(exc))
            return
        self.message_var.set(f"Export สำเร็จ: {path}")

    def _rename_pattern(self) -> None:
        if not self.current_pattern:
            return
        old_name = self.current_pattern["name"]
        new_name = simpledialog.askstring("เปลี่ยนชื่อ", "ชื่อใหม่:", initialvalue=old_name, parent=self.root)
        if not new_name or new_name == old_name:
            return
        try:
            self.store.rename(old_name, new_name)
        except PatternStoreError as exc:
            self._show_error(str(exc))
            return
        self.pattern_var.set(new_name)
        self._refresh_pattern_names()
        self._load_selected_pattern()

    def _duplicate_pattern(self) -> None:
        if not self.current_pattern:
            return
        new_name = simpledialog.askstring("ทำสำเนา", "ชื่อ Pattern สำเนา:", initialvalue=f"{self.current_pattern['name']}_copy", parent=self.root)
        if not new_name:
            return
        try:
            self.store.duplicate(self.current_pattern["name"], new_name)
        except PatternStoreError as exc:
            self._show_error(str(exc))
            return
        self.pattern_var.set(new_name)
        self._refresh_pattern_names()
        self._load_selected_pattern()

    def _delete_pattern(self) -> None:
        if not self.current_pattern or not messagebox.askyesno("ยืนยันการลบ", f"ลบ Pattern {self.current_pattern['name']} หรือไม่?", parent=self.root):
            return
        try:
            self.store.delete(self.current_pattern["name"])
        except PatternStoreError as exc:
            self._show_error(str(exc))
            return
        self.current_pattern = None
        self._refresh_pattern_names()
        names = self.store.list_patterns()
        if names:
            self.pattern_var.set(names[0])
            self._load_selected_pattern()
        else:
            self._new_pattern(default_name="stage_01")

    def _refresh_tables(self) -> None:
        for item in self.event_tree.get_children():
            self.event_tree.delete(item)
        for item in self.zone_tree.get_children():
            self.zone_tree.delete(item)
        if not self.current_pattern:
            if hasattr(self, "timeline_editor"):
                self.timeline_editor.set_pattern(None)
            return
        for event in self.current_pattern["events"]:
            action = event.get("action") or ", ".join(f"{key}:{value}" for key, value in event.get("options", {}).items())
            if event.get("action") in {"tap", "hold"}:
                action = f"{event['action']} ({event.get('x','?')},{event.get('y','?')})"
            event_class = "REQUIRED" if event["event_class"] == "required" else "SAFE RANDOM"
            phase_label = {
                "pre_sync": "ก่อน Sync", "synced": "หลัง Sync", "post_game": "หลังจบเกม",
            }.get(event.get("phase", "synced"), str(event.get("phase", "synced")))
            self.event_tree.insert("", "end", iid=event["id"], values=(
                phase_label,
                f"{event['at']:.3f}", event_class, event["type"], action,
                event.get("chance", "—"), event.get("jitter_ms", 0), event.get("duration_ms", "—"),
                event.get("safe_zone_id", "—"), "ถูกต้อง",
            ), tags=("required" if event["event_class"] == "required" else "safe",))
        for zone in self.current_pattern["safe_zones"]:
            count = sum(1 for event in self.current_pattern["events"] if event.get("safe_zone_id") == zone["id"])
            self.zone_tree.insert("", "end", iid=zone["id"], values=(zone["id"], f"{zone['start']:.3f}", f"{zone['end']:.3f}", zone["label"], count))
        if hasattr(self, "timeline_editor"):
            self.timeline_editor.set_pattern(self.current_pattern)

    def _next_zone_id(self) -> str:
        existing = {str(zone.get("id", "")) for zone in (self.current_pattern or {}).get("safe_zones", [])}
        number = 1
        while f"zone_{number:03d}" in existing:
            number += 1
        return f"zone_{number:03d}"

    def _next_event_id(self) -> str:
        existing = {str(event.get("id", "")) for event in (self.current_pattern or {}).get("events", [])}
        number = 1
        while f"evt_{number:04d}" in existing:
            number += 1
        return f"evt_{number:04d}"

    def _timeline_create_zone(self, start: float, end: float) -> None:
        if not self.current_pattern:
            return
        candidate = deepcopy(self.current_pattern)
        zone_id = self._next_zone_id()
        candidate.setdefault("safe_zones", []).append({
            "id": zone_id,
            "start": start,
            "end": end,
            "label": f"เลือกจาก Timeline {start:.3f}–{end:.3f}s",
        })
        try:
            self.current_pattern, warnings = normalize_pattern(candidate)
        except EventValidationError as exc:
            self._show_error(str(exc))
            return
        self._refresh_tables()
        if self.zone_tree.exists(zone_id):
            self.zone_tree.selection_set(zone_id)
            self.zone_tree.see(zone_id)
            self.timeline_editor.select_zone(zone_id)
        self._save_current(quiet=True)
        count = sum(1 for event in self.current_pattern["events"] if event.get("phase", "synced") == "synced" and start <= float(event["at"]) <= end)
        self.message_var.set(f"สร้าง {zone_id} ช่วง {start:.3f}–{end:.3f}s แล้ว • มี {count} Events" + (f" • {'; '.join(warnings)}" if warnings else ""))

    def _timeline_update_zone(self, zone_id: str, start: float, end: float) -> None:
        if not self.current_pattern:
            return
        candidate = deepcopy(self.current_pattern)
        zone = next((item for item in candidate.get("safe_zones", []) if item.get("id") == zone_id), None)
        if not zone:
            self._show_error(f"ไม่พบ Safe Zone {zone_id}")
            return
        zone["start"], zone["end"] = start, end
        zone["label"] = str(zone.get("label") or f"แก้จาก Timeline {start:.3f}–{end:.3f}s")
        try:
            self.current_pattern, warnings = normalize_pattern(candidate)
        except EventValidationError as exc:
            self._show_error(str(exc))
            return
        self._refresh_tables()
        self._save_current(quiet=True)
        self.message_var.set(f"อัปเดต {zone_id} เป็น {start:.3f}–{end:.3f}s แล้ว" + (f" • {'; '.join(warnings)}" if warnings else ""))

    def _selected_event(self) -> dict | None:
        selected = self.event_tree.selection()
        if not selected or not self.current_pattern:
            return None
        return next((event for event in self.current_pattern["events"] if event["id"] == selected[0]), None)

    def _add_event(self) -> None:
        if not self.current_pattern:
            return
        EventDialog(self.root, None, self.current_pattern["safe_zones"], self._accept_event)

    def _edit_event(self) -> None:
        event = self._selected_event()
        if not event:
            messagebox.showinfo("เลือก Event", "กรุณาเลือก Event ที่ต้องการแก้ไข", parent=self.root)
            return
        EventDialog(self.root, event, self.current_pattern["safe_zones"], lambda updated: self._accept_event(updated, event["id"]))

    def _duplicate_event(self) -> None:
        event = self._selected_event()
        if not event or not self.current_pattern:
            messagebox.showinfo("เลือก Event", "กรุณาเลือก Event ที่ต้องการทำสำเนา", parent=self.root)
            return
        duplicate = deepcopy(event)
        duplicate["id"] = self._next_event_id()
        duplicate["at"] = round(float(duplicate.get("at", 0)) + 0.05, 6)
        EventDialog(self.root, duplicate, self.current_pattern["safe_zones"], self._accept_event)

    def _accept_event(self, event: dict, old_id: str | None = None) -> None:
        if not self.current_pattern:
            return
        events = [item for item in self.current_pattern["events"] if item["id"] != old_id]
        if not event.get("id"):
            event["id"] = self._next_event_id()
        required = [item for item in events if item["event_class"] == "required"]
        try:
            normalized, warnings = normalize_event(
                event,
                self.current_pattern["safe_zones"],
                required,
                self.current_pattern["safety"]["safe_random_min_gap_ms"],
            )
            if any(item["id"] == normalized["id"] for item in events):
                raise EventValidationError(f"Event ID ซ้ำ: {normalized['id']}")
            candidate = deepcopy(self.current_pattern)
            candidate["events"] = events + [normalized]
            self.current_pattern, all_warnings = normalize_pattern(candidate)
        except EventValidationError as exc:
            messagebox.showerror("Event ไม่ปลอดภัย", str(exc), parent=self.root)
            return
        self._refresh_tables()
        self._save_current(quiet=True)
        self.message_var.set("บันทึก Event แล้ว" + (f" • {'; '.join(warnings + all_warnings)}" if warnings or all_warnings else ""))

    def _delete_event(self) -> None:
        event = self._selected_event()
        if not event or not self.current_pattern:
            return
        if messagebox.askyesno("ยืนยัน", f"ลบ Event {event['id']} หรือไม่?", parent=self.root):
            self.current_pattern["events"] = [item for item in self.current_pattern["events"] if item["id"] != event["id"]]
            self._refresh_tables()
            self._save_current(quiet=True)

    def _selected_zone(self) -> dict | None:
        selected = self.zone_tree.selection()
        if not selected and hasattr(self, "timeline_editor") and self.timeline_editor.selected_zone_id:
            selected = (self.timeline_editor.selected_zone_id,)
        if not selected or not self.current_pattern:
            return None
        return next((zone for zone in self.current_pattern["safe_zones"] if zone["id"] == selected[0]), None)

    def _timeline_zone_selected(self, zone_id: str | None) -> None:
        if not hasattr(self, "zone_tree"):
            return
        if zone_id and self.zone_tree.exists(zone_id):
            self.zone_tree.selection_set(zone_id)
            self.zone_tree.see(zone_id)
        else:
            self.zone_tree.selection_remove(*self.zone_tree.selection())

    def _zone_tree_selected(self, _event=None) -> None:
        selected = self.zone_tree.selection()
        self.timeline_editor.select_zone(str(selected[0]) if selected else None)

    def _add_zone(self) -> None:
        if self.current_pattern:
            SafeZoneDialog(self.root, None, self._accept_zone)

    def _edit_zone(self) -> None:
        zone = self._selected_zone()
        if not zone:
            messagebox.showinfo("เลือก Safe Zone", "กรุณาเลือก Safe Zone ที่ต้องการแก้ไข", parent=self.root)
            return
        SafeZoneDialog(self.root, zone, lambda updated: self._accept_zone(updated, zone["id"]))

    def _duplicate_zone(self) -> None:
        zone = self._selected_zone()
        if not zone:
            messagebox.showinfo("เลือก Safe Zone", "กรุณาเลือก Safe Zone ที่ต้องการทำสำเนา", parent=self.root)
            return
        duplicate = deepcopy(zone)
        duration = float(zone["end"]) - float(zone["start"])
        duplicate["id"] = self._next_zone_id()
        duplicate["start"] = round(float(zone["end"]) + 0.05, 6)
        duplicate["end"] = round(duplicate["start"] + duration, 6)
        duplicate["label"] = f"สำเนา {zone.get('label') or zone['id']}"
        SafeZoneDialog(self.root, duplicate, self._accept_zone)

    def _accept_zone(self, zone: dict, old_id: str | None = None) -> None:
        if not self.current_pattern:
            return
        zones = [item for item in self.current_pattern["safe_zones"] if item["id"] != old_id]
        if not zone.get("id"):
            zone["id"] = self._next_zone_id()
        if old_id and old_id != zone["id"]:
            for event in self.current_pattern["events"]:
                if event.get("safe_zone_id") == old_id:
                    event["safe_zone_id"] = zone["id"]
        try:
            normalized_zones, warnings = normalize_safe_zones(zones + [zone])
            candidate = deepcopy(self.current_pattern)
            candidate["safe_zones"] = normalized_zones
            self.current_pattern, pattern_warnings = normalize_pattern(candidate)
        except EventValidationError as exc:
            messagebox.showerror("Safe Zone ใช้ไม่ได้", str(exc), parent=self.root)
            return
        self._refresh_tables()
        self._save_current(quiet=True)
        self.message_var.set("บันทึก Safe Zone แล้ว" + (f" • {'; '.join(warnings + pattern_warnings)}" if warnings or pattern_warnings else ""))

    def _delete_zone(self, zone_id: str | None = None) -> None:
        zone = (
            next((item for item in (self.current_pattern or {}).get("safe_zones", []) if item.get("id") == zone_id), None)
            if zone_id else self._selected_zone()
        )
        if not self.current_pattern:
            return
        if not zone:
            messagebox.showinfo(
                "เลือก Safe Zone ก่อน",
                "คลิกแถว Safe Zone ด้านล่าง หรือคลิกกรอบสีเขียวบน Timeline แล้วกดลบอีกครั้ง",
                parent=self.root,
            )
            return
        count = sum(1 for event in self.current_pattern["events"] if event.get("safe_zone_id") == zone["id"])
        detail = f"\n\nSafe Random ที่ผูกอยู่ {count} Event จะถูกลบด้วย" if count else ""
        if not messagebox.askyesno("ยืนยันการลบ", f"ลบ Safe Zone {zone['id']} หรือไม่?{detail}", parent=self.root):
            return
        try:
            self.current_pattern, warnings, removed_events = remove_safe_zone(
                self.current_pattern, zone["id"], remove_linked_events=True,
            )
        except EventValidationError as exc:
            self._show_error(str(exc))
            return
        self._refresh_tables()
        self._save_current(quiet=True)
        self.message_var.set(
            f"ลบ Safe Zone {zone['id']} แล้ว"
            + (f" • ลบ Safe Random {removed_events} Event ด้วย" if removed_events else "")
            + (f" • {'; '.join(warnings)}" if warnings else "")
        )

    def _capture_template(self) -> None:
        if not self.current_pattern:
            return
        suggested = self.pause_profile_var.get().strip()
        if not suggested or suggested in {DEFAULT_PAUSE_PROFILE, "Template เดิมของ Pattern"}:
            suggested = self.current_pattern["name"]
        profile_name = simpledialog.askstring(
            "ชื่อ Pause ของ Stage",
            "ตั้งชื่อ Stage/หน้าตาปุ่ม Pause (เช่น Episode 1 หรือ Oven Stage):\n"
            "ชื่อเดิม = อัปเดต Template ของ Stage นั้น",
            initialvalue=suggested,
            parent=self.root,
        )
        if not profile_name or not profile_name.strip():
            return
        profile_name = profile_name.strip()
        if profile_name == DEFAULT_PAUSE_PROFILE:
            self._show_error("Template ค่าเริ่มต้นแก้ทับไม่ได้ กรุณาตั้งชื่อ Stage ใหม่")
            return
        try:
            adb, serial = self._adb(), self.device_var.get().strip()
            if not serial:
                raise ADBError("กรุณาเลือก MuMu device ก่อน")
        except Exception as exc:
            self._show_error(str(exc))
            return

        def success(data: bytes) -> None:
            image = decode_png(data)
            if image.size != (1280, 720):
                self._show_error(f"ต้อง Capture ที่ความละเอียด 1280×720 แต่ได้ {image.width}×{image.height}")
                return
            ROISelector(
                self.root, image,
                lambda roi: self._save_template(image, roi, profile_name),
                fixed_size=PAUSE_TEMPLATE_SIZE,
            )
            self.message_var.set("เลื่อนกรอบ 62×62 ให้ครอบปุ่ม Pause — ขนาดถูกล็อกเท่ากันทุก Stage")

        self._background(lambda: adb.capture_png(serial), success, "กำลัง Capture หน้าจอ MuMu…")

    def _save_template(self, screenshot: Image.Image, roi: ROI, profile_name: str | None = None) -> None:
        if not self.current_pattern:
            return
        profile_name = (profile_name or self.current_pattern["name"]).strip()
        if (roi.width, roi.height) != PAUSE_TEMPLATE_SIZE:
            self._show_error("Pause Template ทุก Stage ต้องมีขนาด 62×62 px กรุณาเลือกใหม่")
            return
        filename = f"pause_{safe_pattern_filename(profile_name)}.png"
        path = self.store.templates_dir / filename
        temp = path.with_suffix(".tmp")
        crop = crop_roi(screenshot, roi).convert("RGB")
        if crop.size != PAUSE_TEMPLATE_SIZE:
            self._show_error(f"ขนาด Crop ผิด: ได้ {crop.width}×{crop.height} แต่ต้องเป็น 62×62")
            return
        try:
            crop.save(temp, format="PNG")
            os.replace(temp, path)
            profile = self.pause_profiles.upsert({
                "name": profile_name,
                "template_path": f"templates/{filename}",
                "roi": {"x": roi.x, "y": roi.y, "width": roi.width, "height": roi.height},
                "resolution": {"width": screenshot.width, "height": screenshot.height},
            })
        except (OSError, PauseProfileError) as exc:
            temp.unlink(missing_ok=True)
            self._show_error(str(exc))
            return
        self.current_pattern["sync"].update({
            "mode": "auto_pause_icon",
            "profile_name": profile["name"],
            "template_path": profile["template_path"],
            "roi": deepcopy(profile["roi"]),
        })
        self.current_pattern["device"]["resolution"] = {"width": screenshot.width, "height": screenshot.height}
        self.sync_vars["mode"].set("auto_pause_icon")
        self._refresh_pause_profiles()
        self.pause_profile_var.set(profile["name"])
        self._save_current(quiet=True)
        self._update_roi_preview()
        self.message_var.set(f"บันทึก Pause Stage ‘{profile_name}’ แล้ว • Crop/เทียบขนาด 62×62 px")

    def _refresh_pause_profiles(self) -> None:
        try:
            names = self.pause_profiles.names()
        except PauseProfileError as exc:
            names = [DEFAULT_PAUSE_PROFILE]
            self.message_var.set(str(exc))
        if hasattr(self, "pause_profile_combo"):
            self.pause_profile_combo.configure(values=names)

    def _apply_pause_profile(self) -> None:
        if not self.current_pattern:
            return
        try:
            profile = self.pause_profiles.get(self.pause_profile_var.get())
        except PauseProfileError as exc:
            self._show_error(str(exc))
            return
        self.current_pattern["sync"].update({
            "mode": "auto_pause_icon",
            "profile_name": profile["name"],
            "template_path": profile["template_path"],
            "roi": deepcopy(profile["roi"]),
        })
        self.current_pattern["device"]["resolution"] = deepcopy(profile["resolution"])
        self.sync_vars["mode"].set("auto_pause_icon")
        self._save_current(quiet=True)
        self._update_roi_preview()
        self.message_var.set(
            f"เลือก Pause Stage ‘{profile['name']}’ แล้ว • ROI "
            f"({profile['roi']['x']},{profile['roi']['y']}) • 62×62 px"
        )

    def _apply_accurate_sync_preset(self) -> None:
        self.sync_vars["threshold"].set("0.82")
        self.sync_vars["poll_ms"].set("20")
        self.sync_vars["consecutive_matches"].set("2")
        self.sync_vars["offset_ms"].set("0")
        self.message_var.set("โหมดเร็วสุด: จุดเริ่มตรงเฟรม Pause แรก • เหมาะเมื่อ Fast ROI ทำงานได้ตลอด")

    def _apply_stable_sync_preset(self) -> None:
        self.sync_vars["threshold"].set("0.82")
        self.sync_vars["poll_ms"].set("20")
        self.sync_vars["consecutive_matches"].set("2")
        self.sync_vars["offset_ms"].set("300")
        self.message_var.set("โหมดคงที่: จุดเริ่ม Pattern = Pause เฟรมแรก + 300 ms • ปรับ Delay นี้ได้อิสระ")

    def _use_default_result_template(self) -> None:
        if not self.current_pattern:
            return
        ensure_builtin_assets(self.project_dir)
        self.current_pattern.setdefault("post_game", {}).update({
            "enabled": True,
            "mode": "xp_result",
            "template_path": DEFAULT_RESULT_TEMPLATE,
            "roi": deepcopy(DEFAULT_RESULT_ROI),
        })
        self.post_game_vars["enabled"].set(True)
        self._save_current(quiet=True)
        self._update_result_roi_preview()
        self.message_var.set("ใช้ XP template จากภาพ Result ที่ให้มาแล้ว • ถ้าคะแนนต่ำให้เปิดหน้า Result แล้ว Capture กรอบ XP เอง")

    def _capture_result_template(self) -> None:
        if not self.current_pattern:
            return
        try:
            adb, serial = self._adb(), self.device_var.get().strip()
            if not serial:
                raise ADBError("กรุณาเลือก MuMu device ก่อน")
        except Exception as exc:
            self._show_error(str(exc))
            return

        def success(data: bytes) -> None:
            image = decode_png(data)
            ROISelector(self.root, image, lambda roi: self._save_result_template(image, roi))
            self.message_var.set("ลากกรอบเล็ก ๆ รอบคำว่า XP เท่านั้น แล้วกด ‘ใช้กรอบนี้’")

        self._background(lambda: adb.capture_png(serial), success, "กำลัง Capture หน้า Result จาก MuMu…")

    def _save_result_template(self, screenshot: Image.Image, roi: ROI) -> None:
        if not self.current_pattern:
            return
        filename = f"{safe_pattern_filename(self.current_pattern['name'])}_result_xp.png"
        path = self.store.templates_dir / filename
        temp = path.with_suffix(".tmp")
        crop_roi(screenshot, roi).save(temp, format="PNG")
        os.replace(temp, path)
        self.current_pattern.setdefault("post_game", {}).update({
            "enabled": True,
            "mode": "xp_result",
            "template_path": f"templates/{filename}",
            "roi": {"x": roi.x, "y": roi.y, "width": roi.width, "height": roi.height},
        })
        self.current_pattern["device"]["resolution"] = {"width": screenshot.width, "height": screenshot.height}
        self.post_game_vars["enabled"].set(True)
        self._save_current(quiet=True)
        self._update_result_roi_preview()
        self.message_var.set(f"บันทึก XP template แล้ว: {filename} ({roi.width}×{roi.height})")

    def _update_roi_preview(self) -> None:
        template = str((self.current_pattern or {}).get("sync", {}).get("template_path", ""))
        if not template:
            self.roi_photo = None
            self.roi_preview.configure(image="", text="ยังไม่มี template")
            return
        path = self.project_dir / template
        try:
            with Image.open(path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((110, 80), Image.Resampling.LANCZOS)
                self.roi_photo = ImageTk.PhotoImage(preview)
        except (OSError, ValueError):
            self.roi_photo = None
            self.roi_preview.configure(image="", text="ไม่พบ template")
            return
        self.roi_preview.configure(image=self.roi_photo, text="")

    def _update_result_roi_preview(self) -> None:
        template = str((self.current_pattern or {}).get("post_game", {}).get("template_path", ""))
        if not template:
            self.result_roi_photo = None
            self.result_roi_preview.configure(image="", text="ยังไม่มี XP template")
            return
        path = self.project_dir / template
        try:
            with Image.open(path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((130, 75), Image.Resampling.LANCZOS)
                self.result_roi_photo = ImageTk.PhotoImage(preview)
        except (OSError, ValueError):
            self.result_roi_photo = None
            self.result_roi_preview.configure(image="", text="ไม่พบ XP template")
            return
        self.result_roi_preview.configure(image=self.result_roi_photo, text="")

    def _make_detector(self, adb: ADBManager, serial: str, pattern: dict, on_similarity: Callable | None = None) -> SyncDetector:
        sync = pattern["sync"]
        if not sync.get("template_path") or not sync.get("roi"):
            raise SyncError("ยังไม่มี Pause template/ROI กรุณากด ‘ตั้งค่า Pause Template’")
        template_path = self.project_dir / sync["template_path"]
        resolution = pattern.get("device", {}).get("resolution", {})
        expected_size = None
        if int(resolution.get("width", 0)) > 0 and int(resolution.get("height", 0)) > 0:
            expected_size = (int(resolution["width"]), int(resolution["height"]))
        fast_capture = MuMuROICapture(expected_size or (1280, 720))
        waiting_roi = Image.new("RGB", (int(sync["roi"]["width"]), int(sync["roi"]["height"])), "black")

        def adb_pause_roi(roi: ROI) -> tuple[Image.Image, str]:
            # MuMu รุ่นที่รองรับ Raw ให้ภาพ native 1280×720 เร็วและคงที่กว่า PNG;
            # เส้นทางนี้แข่งกับ Fast ROI เฉพาะตอนรอ Sync เท่านั้น.
            screenshot = adb.capture_raw_image(serial, timeout=2.0)
            capture_source = "ADB Raw"
            if expected_size and screenshot.size != expected_size:
                raise SyncError(
                    f"Resolution ไม่ตรงกับตอนสร้าง template: คาด {expected_size[0]}x{expected_size[1]} "
                    f"แต่ภาพปัจจุบันเป็น {screenshot.width}x{screenshot.height}"
                )
            return crop_roi(screenshot, roi), f"{capture_source} verification fallback"

        def capture_pause_roi(roi: ROI) -> tuple[Image.Image, str]:
            try:
                return fast_capture.capture_roi(roi)
            except MuMuFastCaptureError as exc:
                # อย่าบล็อก detector ด้วย ADB 250–900 ms ในจังหวะที่ผู้ใช้เพิ่ง
                # กดเล่นแล้วกำลังสลับไป MuMu ให้ส่งสถานะเบากลับไปก่อน จากนั้น
                # SyncDetector จะเปิด ADB เฉพาะเมื่อ Fast ROI หายต่อเนื่องจริง.
                return waiting_roi, f"Fast ROI unavailable: {exc}"
        return SyncDetector(
            lambda: adb.capture_png(serial), template_path, sync["roi"], poll_ms=sync["poll_ms"],
            threshold=sync["threshold"], consecutive_matches=sync["consecutive_matches"],
            timeout_seconds=sync["timeout_seconds"], expected_size=expected_size, on_similarity=on_similarity,
            capture_roi=capture_pause_roi, fallback_capture_roi=adb_pause_roi,
        )

    def _similarity_callback(self, value: float, _history: list[float]) -> None:
        self.root.after(0, lambda: self.similarity_var.set(f"รวม {value:.4f}"))

    def _make_result_detector(
        self,
        adb: ADBManager,
        serial: str,
        pattern: dict,
        *,
        expected_duration: float = 0,
        on_similarity: Callable | None = None,
    ) -> ResultDetector:
        post_game = pattern.get("post_game", {})
        if not post_game.get("enabled", True):
            raise SyncError("Pattern นี้ปิดระบบตรวจหลังจบเกม")
        if not post_game.get("template_path") or not post_game.get("roi"):
            raise SyncError("ยังไม่มี XP template/ROI กรุณากด ‘ใช้ XP มาตรฐาน’ หรือ Capture กรอบ XP")
        pause = pattern.get("sync", {})
        resolution = pattern.get("device", {}).get("resolution", {})
        expected_size = None
        if int(resolution.get("width", 0)) > 0 and int(resolution.get("height", 0)) > 0:
            expected_size = (int(resolution["width"]), int(resolution["height"]))
        pause_template = self.project_dir / pause["template_path"] if pause.get("template_path") and pause.get("roi") else None
        return ResultDetector(
            lambda: adb.capture_png(serial),
            self.project_dir / post_game["template_path"],
            post_game["roi"],
            pause_template=pause_template,
            pause_roi=pause.get("roi"),
            poll_ms=post_game["poll_ms"],
            threshold=post_game["threshold"],
            consecutive_matches=post_game["consecutive_matches"],
            min_gameplay_seconds=post_game["min_gameplay_seconds"],
            timeout_seconds=post_game["timeout_seconds"],
            expected_duration=expected_duration,
            pause_absent_threshold=post_game["pause_absent_threshold"],
            expected_size=expected_size,
            on_similarity=on_similarity,
        )

    def _result_similarity_callback(self, xp_score: float, pause_score: float | None, consecutive: int) -> None:
        pause_text = "—" if pause_score is None else f"{pause_score:.3f}"
        value = f"XP {xp_score:.3f} • Pause {pause_text} • ต่อเนื่อง {consecutive}"
        self.root.after(0, lambda: self.result_similarity_var.set(value))

    def _test_result_detection(self) -> None:
        try:
            pattern, adb, serial = self._collect_ui(), self._adb(), self.device_var.get().strip()
            if not serial:
                raise ADBError("กรุณาเลือก MuMu device")
            detector = self._make_result_detector(adb, serial, pattern)
        except Exception as exc:
            self._show_error(str(exc))
            return

        def work() -> tuple[float, float | None, bool, dict]:
            xp_score, pause_score, confirmed = detector.check_once()
            return xp_score, pause_score, confirmed, detector.last_xp_details

        def success(result) -> None:
            xp_score, pause_score, _confirmed, details = result
            pause_absent = pause_score is None or pause_score < float(self.post_game_vars["pause_absent_threshold"].get())
            xp_ok = (
                xp_score >= float(self.post_game_vars["threshold"].get())
                and details["feature"] >= detector.feature_threshold
                and details["structure"] >= detector.structure_threshold
            )
            self.result_similarity_var.set(
                f"XP {xp_score:.3f} • โครง {details['structure']:.3f} • เส้น {details['feature']:.3f} • "
                f"Pause {'—' if pause_score is None else f'{pause_score:.3f}'}"
            )
            self.message_var.set(
                f"ทดสอบ Result: {'พบ XP' if xp_ok else 'ยังไม่พบ XP'} • {'Pause หายแล้ว' if pause_absent else 'Pause ยังอยู่'} • "
                f"ตอนเล่นจริงต้องผ่าน {self.post_game_vars['consecutive_matches'].get()} เฟรมต่อเนื่อง"
            )

        self._background(work, success, "กำลังตรวจ XP และ Pause จาก screenshot เดียว…")

    def _test_sync(self) -> None:
        if self.state.state not in {AppState.IDLE, AppState.STOPPED}:
            messagebox.showwarning(
                "กำลังอัดหรือเล่นอยู่",
                "หยุดงานปัจจุบันก่อนทดสอบ Auto Sync เพื่อไม่ให้ Detector สองตัวทำงานพร้อมกัน\n"
                "อัดอยู่: กด F7   •   เล่นอยู่: กด F8",
                parent=self.root,
            )
            return
        try:
            pattern, adb, serial = self._collect_ui(), self._adb(), self.device_var.get().strip()
            detector = self._make_detector(adb, serial, pattern)
        except Exception as exc:
            self._show_error(str(exc))
            return

        threshold = float(self.sync_vars["threshold"].get())
        self.similarity_var.set("รอภาพจริงจาก MuMu…")

        def work() -> dict[str, object]:
            samples: list[dict[str, object]] = []
            deadline = time.perf_counter() + 6.0
            while len(samples) < 8 and time.perf_counter() < deadline:
                detector.check_once()
                if not is_real_fast_roi_source(detector.last_capture_source):
                    time.sleep(0.05)
                    continue
                samples.append({
                    "details": dict(detector.last_details),
                    "capture_ms": detector.last_capture_ms,
                    "source": detector.last_capture_source,
                })
                time.sleep(0.02)
            if len(samples) < 8:
                raise SyncError(
                    "ทดสอบ Fast ROI ไม่ได้ เพราะ MuMu ยังไม่อยู่ด้านหน้า\n"
                    "เปิดหน้าต่าง Android Device-1 แล้วกดทดสอบใหม่"
                )
            return summarize_sync_samples(
                samples, threshold, detector.feature_threshold, detector.structure_threshold,
            )

        def success(summary: dict[str, object]) -> None:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            sources = ", ".join(f"{name} ×{count}" for name, count in dict(summary["sources"]).items())
            passed, count = int(summary["passed"]), int(summary["count"])
            stable = passed >= max(2, count - 1)
            if stable:
                verdict = "พบ Pause เสถียร"
                guidance = "พร้อมใช้ Auto Sync กับ Pause Stage นี้"
            elif passed == 0:
                verdict = "ไม่พบ Pause ในภาพปัจจุบัน"
                guidance = (
                    "ถ้าหน้านี้เป็นร้านค้า/หน้าเตรียม/ก่อนเข้าด่าน ผลนี้ถูกต้อง\n"
                    "ถ้าในเกมมองเห็นปุ่ม Pause อยู่ ให้สร้าง Pause ของ Stage ใหม่"
                )
            else:
                verdict = "พบ Pause บางเฟรม แต่คะแนนยังแกว่ง"
                guidance = "ค้างหน้าที่เห็น Pause ชัด ๆ แล้วทดสอบใหม่ หรือสร้าง Pause ของ Stage ใหม่"
            self.similarity_var.set(
                f"ผ่าน {passed}/{count} • รวม {float(summary['combined_min']):.3f}–{float(summary['combined_max']):.3f} • เกณฑ์ {threshold:.2f}"
            )
            detail = (
                f"ผลทดสอบ Auto Sync {count} เฟรม\n\n"
                f"{verdict}\n{guidance}\n\n"
                f"ผ่านเกณฑ์: {passed}/{count} เฟรม\n"
                f"คะแนนรวม ต่ำสุด/เฉลี่ย/สูงสุด: {float(summary['combined_min']):.4f} / "
                f"{float(summary['combined_avg']):.4f} / {float(summary['combined_max']):.4f}\n"
                f"โครงต่ำสุด: {float(summary['structure_min']):.4f} • เส้นต่ำสุด: {float(summary['feature_min']):.4f}\n"
                f"เกณฑ์ที่ใช้จริง: รวม ≥ {threshold:.2f} • เส้น ≥ {detector.feature_threshold:.2f} • "
                f"โครง ≥ {detector.structure_threshold:.2f}\n"
                f"Capture median/max: {float(summary['capture_median_ms']):.0f} / {float(summary['capture_max_ms']):.0f} ms\n"
                f"แหล่งภาพ: {sources}\n\n"
                "การทดสอบนี้นับเฉพาะภาพ MuMu Window ROI จริง ไม่ใช้ภาพดำหรือ ADB มาปน"
            )
            self.message_var.set(
                f"Auto Sync: {verdict} • {passed}/{count} • "
                f"median {float(summary['capture_median_ms']):.0f} ms • {sources}"
            )
            messagebox.showinfo("ผลทดสอบ Auto Sync หลายเฟรม", detail, parent=self.root)

        self._background(work, success, "กำลังเปิด MuMu อัตโนมัติ — ค้างหน้าที่เห็นปุ่ม Pause…")
        self.root.after(10, self._activate_mumu_for_sync)

    def _activate_mumu_for_sync(self) -> None:
        if not activate_mumu_window():
            self.message_var.set("เปิด MuMu อัตโนมัติไม่ได้ — กรุณาคลิกหน้าต่าง Android Device-1")

    def _resolution_ok(self, pattern: dict, device: DeviceInfo) -> bool:
        expected = pattern.get("device", {}).get("resolution", {})
        width, height = int(expected.get("width", 0)), int(expected.get("height", 0))
        if not width or not height or (width == device.width and height == device.height):
            return True
        choice = messagebox.askyesnocancel(
            "ความละเอียดไม่ตรง",
            f"Pattern สร้างสำหรับ {width}×{height} แต่ MuMu ตอนนี้เป็น {device.resolution_text}\n\n"
            "Yes = ใช้ต่อโดยยอมรับความเสี่ยง\nNo = ยกเลิกและกลับไปสร้าง template/พิกัดใหม่\nCancel = ยกเลิก",
            parent=self.root,
        )
        if choice is False:
            self.notebook.select(self.studio_page)
            self.message_var.set("กรุณาปรับพิกัดและสร้าง Pause template ใหม่")
        return choice is True

    def _sync_waiter(self, adb: ADBManager, serial: str, pattern: dict):
        def wait(stop_event, manual_event, auto_event) -> bool | float:
            if pattern["sync"]["mode"] == "manual_f9":
                self.state.transition(AppState.WAITING_FOR_MANUAL_SYNC, force=True)
                while not stop_event.is_set() and not manual_event.wait(0.25):
                    pass
                return time.perf_counter() if manual_event.is_set() and not stop_event.is_set() else False
            self.state.transition(AppState.WAITING_FOR_AUTO_SYNC, force=True)
            def make_detector() -> SyncDetector:
                return self._make_detector(adb, serial, pattern, self._similarity_callback)

            def retry(detector: SyncDetector, attempt: int) -> None:
                best, source = detector.best_score, detector.best_source
                self.root.after(0, lambda: self.message_var.set(
                    f"ยังไม่พบ Pause (ดีที่สุด {best:.3f} จาก {source}) • "
                    f"เริ่มตรวจใหม่รอบ {attempt + 1} อัตโนมัติ — F8 หยุด / F9 เริ่มเอง"
                ))

            result, detector = wait_for_sync_with_retries(
                make_detector, stop_event, manual_event, auto_event,
                retry_on_timeout=True, on_retry=retry,
            )
            if result == SyncResult.AUTO:
                self.root.after(0, lambda: self.message_var.set(
                    f"Auto Sync: {detector.last_capture_source} {detector.last_capture_ms:.0f} ms • "
                    f"ชดเชย Capture + ยืนยัน {detector.confirmation_delay_ms:.0f} ms"
                ))
                return detector.detected_at or time.perf_counter()
            if result == SyncResult.MANUAL:
                return time.perf_counter()
            return False
        return wait

    def _result_waiter(self, adb: ADBManager, serial: str, pattern: dict):
        def wait(stop_event, cancel_event, expected_duration: float) -> bool:
            try:
                detector = self._make_result_detector(
                    adb, serial, pattern,
                    expected_duration=expected_duration,
                    on_similarity=self._result_similarity_callback,
                )
                found = detector.wait(stop_event, cancel_event)
            except Exception as exc:
                self.root.after(0, lambda error=exc: self.result_status_var.set(f"Result detector: ใช้งานไม่ได้ • {error}"))
                return False
            if found:
                self.root.after(0, lambda: self.result_status_var.set("Result detector: พบ XP + Pause หาย • หยุดคำสั่งเกมแล้ว"))
            elif not stop_event.is_set() and not cancel_event.is_set():
                self.root.after(0, lambda: self.result_status_var.set("Result detector: timeout • ไม่ส่ง Tap หลังเกมเพื่อความปลอดภัย"))
            return found
        return wait

    def _play(self) -> None:
        try:
            pattern, adb, serial = self._collect_ui(), self._adb(), self.device_var.get().strip()
            playback = pattern.get("playback", {})
            repeat = 0 if playback.get("loop_forever") else int(playback.get("repeat_count", 1))
            if not serial:
                raise ADBError("กรุณาเลือก MuMu device")
            if not pattern.get("events"):
                raise ValueError("Pattern นี้ยังมี 0 Events จึงเล่นไม่ได้ กรุณากด F6 อัด และดูตัวนับ Events ให้เพิ่มก่อนกด F7")
        except Exception as exc:
            self._show_error(str(exc))
            return

        def ready(device: DeviceInfo) -> None:
            if not self._resolution_ok(pattern, device):
                return
            pattern.setdefault("stats", {})
            pattern["stats"]["play_count"] = int(pattern["stats"].get("play_count", 0)) + 1
            pattern["stats"]["last_played_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self.current_pattern = pattern
            self._save_current(quiet=True)
            self.player = Player(
                adb, self.state,
                on_round=lambda current, total: self.root.after(0, lambda: self.round_var.set(f"{current}/∞" if total == 0 else f"{current}/{total}")),
                on_error=lambda error: self.root.after(0, lambda: self._show_error(error)),
                on_progress=lambda snapshot: self.root.after(0, lambda data=snapshot: self._player_progress(data)),
            )
            active_player = self.player

            def run_player() -> None:
                active_player.play(
                    pattern,
                    serial,
                    repeat_count=repeat,
                    loop_interval_ms=int(playback.get("loop_interval_ms", 0)),
                    sync_each_loop=bool(playback.get("sync_each_loop", True)),
                    sync_waiter=self._sync_waiter(adb, serial, pattern),
                    result_waiter=self._result_waiter(adb, serial, pattern) if pattern.get("post_game", {}).get("enabled", True) else None,
                )
                self.root.after(0, lambda: self._player_finished(active_player))

            threading.Thread(target=run_player, daemon=True).start()
            self.root.after(10, self._activate_mumu_for_sync)
            self.playback_snapshot = {
                "phase": "waiting_sync", "elapsed": 0.0, "duration": 0.0,
                "event_index": 0, "event_total": 0, "round_number": 1,
                "round_total": 0 if repeat == 0 else repeat, "reported_at": time.perf_counter(),
            }
            self.playback_progress_var.set(0)
            self.result_status_var.set("Result detector: รอเริ่มด่าน • จะตรวจ XP หลัง Sync")
            self._render_playback_progress()
            loop_text = "Loop ต่อเนื่อง" if repeat == 0 else f"{repeat} รอบ"
            self.message_var.set(f"เริ่มเล่น {loop_text} — กำลังเปิด MuMu อัตโนมัติ; รอ Auto Sync/F9 และกด F8 หยุดได้")

        self._background(lambda: self._probe_selected(adb, serial), ready, "กำลังตรวจอุปกรณ์ก่อนเล่น…")

    def _start_recording(self) -> None:
        if self.state.state in {AppState.RECORDING, AppState.RECORDING_WAITING_SYNC, AppState.WAITING_FOR_AUTO_SYNC}:
            return
        if self.pending_recording:
            self._choose_recording_destination()
            if self.pending_recording:
                self.message_var.set("มี Draft ยังไม่บันทึก — บันทึก Draft หรือปิดโปรแกรมก่อนเริ่มอัดใหม่")
                return
        try:
            pattern, adb, serial = self._collect_ui(), self._adb(), self.device_var.get().strip()
            if not serial:
                raise ADBError("กรุณาเลือก MuMu device")
        except Exception as exc:
            self._show_error(str(exc))
            return

        def work():
            device = self._probe_selected(adb, serial)
            shell = adb.open_persistent_shell(serial)
            return device, shell

        def ready(result) -> None:
            device, shell = result
            if not self._resolution_ok(pattern, device):
                shell.close()
                return
            self.record_shell = shell
            self.recorder.start(pattern, shell)
            self.recording_context = (adb, serial, pattern)
            self.record_result_watch_started = False
            self.recording_started_wall = None
            self.recording_timer_var.set("● PRE-SYNC  00:00.000")
            self.recording_detail_var.set("Events 0 • Jump 0 • Slide 0 • Tap 0 • Hold 0")
            self.recording_started_var.set("เริ่มเก็บ Event ก่อน Sync แล้ว • รอ Auto Sync/F9")
            self.mouse_capture_var.set("Mouse recorder: พร้อมจับหน้าต่าง MuMu จริง")
            self.keyboard_capture_var.set("Keyboard recorder: พร้อมจับ J/K จาก MuMu (char + VK)")
            self.jump_capture_var.set("Jump detector: รอ Single / Double Jump")
            self.result_status_var.set("Result detector: รอ Sync ก่อนเริ่มตรวจ XP")
            self.message_var.set("เริ่มอัดแล้ว — กำลังเปิด MuMu อัตโนมัติ; Event ก่อน Auto Sync จะถูกเก็บเป็น Pre-Sync")
            if pattern["sync"]["mode"] == "auto_pause_icon":
                self.state.transition(AppState.WAITING_FOR_AUTO_SYNC, force=True)

                def auto_wait() -> None:
                    try:
                        def make_detector() -> SyncDetector:
                            return self._make_detector(adb, serial, pattern, self._similarity_callback)

                        def retry(detector: SyncDetector, attempt: int) -> None:
                            best, source = detector.best_score, detector.best_source
                            self.root.after(0, lambda: self.message_var.set(
                                f"ยังไม่พบ Pause (ดีที่สุด {best:.3f} จาก {source}) • "
                                f"อัด Pre-Sync ต่อและเริ่มตรวจใหม่รอบ {attempt + 1} — F9 เริ่มเองได้"
                            ))

                        result, detector = wait_for_sync_with_retries(
                            make_detector,
                            self.recorder.stop_event,
                            self.recorder.manual_sync_event,
                            self.recorder.auto_sync_event,
                            retry_on_timeout=True,
                            on_retry=retry,
                        )
                        if result == SyncResult.AUTO:
                            delay_ms = max(0, int(pattern["sync"].get("offset_ms", 0)))
                            detected_at = detector.detected_at or time.perf_counter()
                            sync_anchor, remaining = stable_sync_target(detected_at, delay_ms)
                            if remaining:
                                self.root.after(0, lambda: self.message_var.set(
                                    f"พบ Pause แล้ว — รอถึงจุดคงที่ Pause + {delay_ms} ms"
                                ))
                            if self.recorder.stop_event.wait(remaining):
                                return
                            self.recorder.sync(automatic=True, anchor=sync_anchor)
                            self._start_record_result_watch(adb, serial, pattern)
                            self.root.after(0, lambda: self.message_var.set(
                                f"Auto Sync สำเร็จ — {detector.last_capture_source} {detector.last_capture_ms:.0f} ms • "
                                f"ชดเชย Capture + ยืนยัน {detector.confirmation_delay_ms:.0f} ms • กด J/K เพื่ออัด"
                            ))
                    except Exception as exc:
                        self.root.after(0, lambda error=exc: self._show_error(str(error)))
                threading.Thread(target=auto_wait, daemon=True).start()
                self.similarity_var.set("รอภาพจริงจาก MuMu…")
                self.root.after(10, self._activate_mumu_for_sync)

        self._background(work, ready, "กำลังเชื่อมต่อและเปิด ADB shell สำหรับอัด…")

    def _start_record_result_watch(self, adb: ADBManager, serial: str, pattern: dict) -> None:
        if self.record_result_watch_started or not pattern.get("post_game", {}).get("enabled", True):
            return
        self.record_result_watch_started = True

        def watch() -> None:
            try:
                detector = self._make_result_detector(
                    adb, serial, pattern,
                    expected_duration=600,
                    on_similarity=self._result_similarity_callback,
                )
                if detector.wait(self.recorder.stop_event):
                    if self.recorder.mark_post_game():
                        self.root.after(0, lambda: self.result_status_var.set(
                            "✓ พบหน้า Result (XP + Pause หาย) • เปลี่ยนเป็น POST-GAME • รับเฉพาะ Tap/Hold"
                        ))
                        self.root.after(0, lambda: self.message_var.set(
                            "ตรวจพบจบเกมแล้ว — Jump/Slide จะไม่ถูกบันทึก; คลิก OK/เมนูต่อได้ตามปกติ"
                        ))
            except Exception as exc:
                if not self.recorder.stop_event.is_set():
                    self.root.after(0, lambda error=exc: self._show_error(f"Result detector: {error}", dialog=False))

        threading.Thread(target=watch, daemon=True).start()

    def _finish_recording(self) -> None:
        if self.state.state not in {AppState.RECORDING, AppState.RECORDING_WAITING_SYNC, AppState.WAITING_FOR_AUTO_SYNC, AppState.WAITING_FOR_MANUAL_SYNC}:
            if self.pending_recording:
                self._choose_recording_destination()
            return
        try:
            final_status = self.recorder.status()
            recorded_pattern, warnings = self.recorder.finish()
            self.record_shell = None
            self.recording_context = None
            self.record_result_watch_started = False
            self.pending_recording = (recorded_pattern, warnings, final_status)
        except RecorderError as exc:
            self._show_error(str(exc))
            return
        count = len(recorded_pattern["events"])
        shown_time = final_status["elapsed_seconds"] if final_status["synced"] else final_status["armed_elapsed_seconds"]
        self.recording_timer_var.set(f"■ DRAFT  {self._format_elapsed(shown_time)}")
        self.recording_detail_var.set(
            f"Events {count} • Jump {final_status['counts']['jump']} • Slide {final_status['counts']['slide']} • "
            f"Tap {final_status['counts']['tap']} • Hold {final_status['counts']['hold']}"
        )
        if count == 0:
            self.recording_started_var.set("งานอัดค้าง: 0 Events • เลือกที่บันทึกหรือเก็บ Draft ไว้ก่อน")
            messagebox.showwarning(
                "อัดเสร็จแต่ไม่มี Event",
                "งานอัดนี้มี 0 Events จึงเล่นไม่ได้ และยังไม่ได้เขียนทับ Pattern ใด\n\n"
                "ตรวจตัวนับ Events ระหว่างอัด จากนั้นเลือกว่าจะเก็บ Draft นี้หรือบันทึกลง Library",
                parent=self.root,
            )
        else:
            self.recording_started_var.set("อัดเสร็จแล้ว • ยังไม่เขียนทับ Pattern • กรุณาเลือกที่บันทึก")
        self.message_var.set("หยุดอัดแล้ว — เลือกสร้าง Pattern ใหม่หรือบันทึกทับ Pattern ที่ต้องการ")
        self._choose_recording_destination()

    def _suggest_recording_name(self, source_name: str) -> str:
        names = set(self.store.list_patterns())
        base = f"{safe_pattern_filename(source_name)}_take"
        if base not in names:
            return base
        index = 2
        while f"{base}_{index}" in names:
            index += 1
        return f"{base}_{index}"

    def _choose_recording_destination(self) -> None:
        if not self.pending_recording or self.record_save_dialog_open:
            return
        recorded_pattern, warnings, final_status = self.pending_recording
        current_name = str(recorded_pattern.get("name", self.pattern_var.get() or "pattern"))
        shown_time = final_status["elapsed_seconds"] if final_status["synced"] else final_status["armed_elapsed_seconds"]
        self.record_save_dialog_open = True
        try:
            choice = RecordingSaveDialog(
                self.root,
                pattern_names=self.store.list_patterns(),
                current_name=current_name,
                suggested_name=self._suggest_recording_name(current_name),
                event_count=len(recorded_pattern.get("events", [])),
                duration_text=self._format_elapsed(shown_time),
            ).show()
        finally:
            self.record_save_dialog_open = False
        if choice is None:
            self.recording_timer_var.set(f"■ DRAFT  {self._format_elapsed(shown_time)}")
            self.recording_started_var.set("เก็บงานอัดล่าสุดไว้เป็น Draft • กด F7 เพื่อเลือกที่บันทึกอีกครั้ง")
            self.quick_action_var.set("มี Draft ยังไม่บันทึก — กด F7 เพื่อเลือก Pattern ปลายทาง")
            self.message_var.set("ยังไม่เขียนทับไฟล์ใด • งานอัดถูกเก็บในหน่วยความจำจนกว่าจะปิดโปรแกรม")
            return
        mode, target_name = choice
        if mode == "discard":
            event_count = len(recorded_pattern.get("events", []))
            self.pending_recording = None
            self.recording_timer_var.set(f"■ DISCARDED  {self._format_elapsed(shown_time)}")
            self.recording_started_var.set("ทิ้งงานอัดล่าสุดแล้ว • ไม่มี Pattern ถูกเขียนทับ")
            self.quick_action_var.set("พร้อม: เริ่มอัดใหม่หรือเล่น Pattern เดิม")
            self.message_var.set(f"ไม่บันทึกงานอัด {event_count} Events • Draft ถูกทิ้งตามที่ยืนยันแล้ว")
            return
        candidate = deepcopy(recorded_pattern)
        candidate["name"] = target_name
        try:
            path, save_warnings = self.store.save(candidate, name=target_name)
            self.current_pattern, load_warnings = self.store.load(target_name)
        except (PatternStoreError, EventValidationError) as exc:
            self._show_error(str(exc))
            return
        self.pending_recording = None
        self.pattern_var.set(target_name)
        self._load_pattern_to_ui()
        self._refresh_pattern_names()
        self.recording_timer_var.set(f"■ SAVED  {self._format_elapsed(shown_time)}")
        self.recording_started_var.set(f"บันทึก {path.name} สำเร็จ • พร้อมเล่น")
        self.quick_action_var.set("พร้อม: อัดใหม่หรือเล่น Pattern ที่เพิ่งบันทึก")
        all_warnings = warnings + save_warnings + load_warnings
        self.message_var.set(
            f"บันทึกงานอัดลง {path.name} แล้ว ({len(self.current_pattern['events'])} Events)"
            + (f" • {'; '.join(all_warnings)}" if all_warnings else "")
        )

    def _manual_sync(self) -> None:
        if self.player:
            self.player.request_manual_sync()
        if self.state.state in {AppState.RECORDING_WAITING_SYNC, AppState.WAITING_FOR_AUTO_SYNC, AppState.WAITING_FOR_MANUAL_SYNC}:
            try:
                self.recorder.sync(automatic=False)
                if self.recording_context:
                    self._start_record_result_watch(*self.recording_context)
                self.message_var.set("Manual Sync สำเร็จ — t = 0 เริ่มแล้ว")
            except RecorderError:
                pass

    def _emergency_stop(self) -> None:
        final_status = self.recorder.status()
        self.operation_token += 1
        if self.player:
            self.player.stop()
            self.player = None
            self.playback_snapshot["phase"] = "stopped"
            self._render_playback_progress()
        self.recorder.stop()
        self.record_shell = None
        self.recording_context = None
        self.record_result_watch_started = False
        if self.active_adb:
            self.active_adb.cancel_all()
        self.state.transition(AppState.STOPPED, force=True)
        if final_status["active"]:
            shown_time = final_status["elapsed_seconds"] if final_status["synced"] else final_status["armed_elapsed_seconds"]
            self.recording_timer_var.set(f"■ STOPPED  {self._format_elapsed(shown_time)}")
            self.recording_started_var.set("หยุดฉุกเฉินแล้ว • ไม่ได้บันทึก Pattern")
            self.mouse_capture_var.set("Mouse recorder: หยุดแล้ว")
            self.keyboard_capture_var.set("Keyboard recorder: หยุดแล้ว")
            self.jump_capture_var.set("Jump detector: หยุดแล้ว")
        self.message_var.set("หยุดทุก worker และ ADB shell แล้ว")

    def _player_finished(self, player: Player) -> None:
        if self.player is player:
            self.player = None
        if self.state.state == AppState.STOPPED:
            if self.playback_snapshot.get("phase") == "completed":
                self.message_var.set("เล่น Pattern ครบแล้ว")
            elif self.playback_snapshot.get("phase") == "result_timeout":
                self.message_var.set("ไม่พบ XP ภายในเวลา — หยุดก่อน Tap ผิดหน้าจอ กรุณาทดสอบ/ตั้งค่า XP template ใหม่")
            else:
                self.message_var.set("หยุดเล่น Pattern แล้ว")

    def _bind_hotkeys(self) -> None:
        mappings = {"<F6>": self._start_recording, "<F7>": self._finish_recording, "<F8>": self._emergency_stop, "<F9>": self._manual_sync}
        for sequence, command in mappings.items():
            self.root.bind_all(sequence, lambda _event, callback=command: callback())
        if keyboard is None:
            self.message_var.set("ยังไม่มี pynput — Hotkey ใช้ได้เมื่อหน้าต่างโปรแกรม active; ติดตั้ง requirements.txt เพื่อใช้ Global Hotkey")
            return

        def on_press(key) -> None:
            if key == keyboard.Key.f6:
                self.root.after(0, self._start_recording)
            elif key == keyboard.Key.f7:
                self.root.after(0, self._finish_recording)
            elif key == keyboard.Key.f8:
                self.root.after(0, self._emergency_stop)
            elif key == keyboard.Key.f9:
                self.root.after(0, self._manual_sync)
            else:
                action_key = recorder_key_name(key)
                if action_key and self.recorder.status()["active"]:
                    try:
                        if self.recorder.status().get("post_game"):
                            self.root.after(0, lambda name=action_key.upper(): self.keyboard_capture_var.set(
                                f"Keyboard recorder: ไม่บันทึก {name} หลังจบเกม • ใช้ Mouse Tap ปุ่มที่ต้องการ"
                            ))
                            return
                        foreground_mumu = is_mumu_foreground()
                        self.recorder.key_down(action_key, send_input=not foreground_mumu)
                        status = self.recorder.status()
                        phase = "ก่อน Sync" if not status["synced"] else "หลัง Sync"
                        if action_key == "j":
                            jump_message = self._jump_feedback(status)
                            self.root.after(0, lambda value=jump_message: self.jump_capture_var.set(value))
                        self.root.after(0, lambda name=action_key.upper(), current_phase=phase, using_mumu=foreground_mumu: self.keyboard_capture_var.set(
                            f"Keyboard recorder: จับ {name} DOWN • {current_phase} • {'MuMu keymap' if using_mumu else 'ADB input'}"
                        ))
                    except RecorderError as exc:
                        self.root.after(0, lambda error=exc: self._show_error(str(error)))

        def on_release(key) -> None:
            action_key = recorder_key_name(key)
            if action_key and self.recorder.status()["active"]:
                try:
                    if self.recorder.status().get("post_game"):
                        return
                    self.recorder.key_up(action_key)
                    status = self.recorder.status()
                    if action_key == "k":
                        duration_count = status["counts"]["slide"]
                        message = f"Keyboard recorder: จับ K UP • Slide #{duration_count}"
                    else:
                        jump_count = status["counts"]["jump"]
                        message = f"Keyboard recorder: จับ J UP • พร้อมรับ Jump ถัดไป • Jump #{jump_count}"
                    self.root.after(0, lambda value=message: self.keyboard_capture_var.set(value))
                except RecorderError as exc:
                    self.root.after(0, lambda error=exc: self._show_error(str(error)))

        self.hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.hotkey_listener.daemon = True
        self.hotkey_listener.start()

        def on_click(x: int, y: int, button, pressed: bool) -> None:
            if button != mouse.Button.left:
                return
            if pressed:
                viewport = viewport_at(int(x), int(y))
                point = map_viewport_point(viewport, int(x), int(y)) if viewport else None
                if point:
                    active = self.recorder.status()["active"]
                    self.root.after(0, lambda p=point, is_active=active: self.mouse_capture_var.set(
                        f"Mouse recorder: พบ MuMu ({p[0]},{p[1]}) • {'กำลังบันทึก' if is_active else 'พร้อม (กด F6 เพื่ออัด)'}"
                    ))
                else:
                    active = self.recorder.status()["active"]
                    if active:
                        self.root.after(0, lambda: self.mouse_capture_var.set("Mouse recorder: คลิกนี้อยู่นอกพื้นที่เกม MuMu"))
                self.mouse_press = (point, time.perf_counter()) if point and self.recorder.status()["active"] else None
                return
            captured = self.mouse_press
            self.mouse_press = None
            if not captured or not self.recorder.status()["active"]:
                return
            point, started_at = captured
            duration_ms = max(1, round((time.perf_counter() - started_at) * 1000))
            self.root.after(0, lambda p=point, at=started_at, duration=duration_ms: self._record_touch(p[0], p[1], at, duration, "หน้าต่าง MuMu"))

        self.mouse_listener = mouse.Listener(on_click=on_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

    def _save_config(self) -> None:
        try:
            self.config.data.update({
                "adb_path": self.adb_var.get().strip(), "candidate_ports": self._parse_ports(),
                "selected_serial": self.device_var.get().strip(), "last_pattern": self.pattern_var.get(),
                "window": {"width": self.root.winfo_width(), "height": self.root.winfo_height()},
            })
            self.config.save()
        except (OSError, ValueError):
            pass

    def _on_close(self) -> None:
        if self.player or self.state.state in {AppState.RECORDING, AppState.PLAYING, AppState.WAITING_FOR_AUTO_SYNC, AppState.WAITING_FOR_MANUAL_SYNC}:
            if not messagebox.askyesno("ปิดโปรแกรม", "กำลังทำงานอยู่ ต้องการหยุดทุกอย่างและปิดโปรแกรมหรือไม่?", parent=self.root):
                return
        if self.pending_recording:
            if not messagebox.askyesno(
                "Draft ยังไม่บันทึก",
                "มีงานอัดล่าสุดที่ยังไม่ได้บันทึกลง Pattern\n\nปิดโปรแกรมและทิ้ง Draft นี้หรือไม่?",
                parent=self.root,
            ):
                self._choose_recording_destination()
                return
        self._save_config()
        if self.live_view and self.live_view.winfo_exists():
            self.live_view.close()
        if self.player:
            self.player.stop()
        self.recorder.stop()
        if self.active_adb:
            self.active_adb.cancel_all()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.root.destroy()
