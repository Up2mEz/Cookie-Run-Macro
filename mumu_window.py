from __future__ import annotations

import ctypes
import math
import os
import time
from pathlib import Path
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageGrab


@dataclass(frozen=True)
class Viewport:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


class MuMuFastCaptureError(RuntimeError):
    pass


def native_roi_to_screen_box(
    viewport: Viewport,
    roi: object,
    native_width: int = 1280,
    native_height: int = 720,
) -> tuple[int, int, int, int]:
    """แปลง ROI พิกัดเกมเป็น bbox บนจอ โดยปัดออกเพื่อไม่ตัดขอบไอคอน."""
    if isinstance(roi, dict):
        x, y = int(roi["x"]), int(roi["y"])
        width, height = int(roi["width"]), int(roi["height"])
    else:
        x, y = int(getattr(roi, "x")), int(getattr(roi, "y"))
        width, height = int(getattr(roi, "width")), int(getattr(roi, "height"))
    if native_width <= 0 or native_height <= 0 or width <= 0 or height <= 0:
        raise MuMuFastCaptureError("ขนาด ROI/ความละเอียดไม่ถูกต้อง")
    if x < 0 or y < 0 or x + width > native_width or y + height > native_height:
        raise MuMuFastCaptureError("ROI อยู่นอกขอบภาพเกม")
    left = math.floor(viewport.left + x * viewport.width / native_width)
    top = math.floor(viewport.top + y * viewport.height / native_height)
    right = math.ceil(viewport.left + (x + width) * viewport.width / native_width)
    bottom = math.ceil(viewport.top + (y + height) * viewport.height / native_height)
    if right <= left or bottom <= top:
        raise MuMuFastCaptureError("กรอบ Pause บนหน้าต่าง MuMu มีขนาดเป็นศูนย์")
    return left, top, right, bottom


def map_viewport_point(viewport: Viewport, x: int, y: int, native_width: int = 1280, native_height: int = 720) -> tuple[int, int] | None:
    if viewport.width <= 0 or viewport.height <= 0 or not viewport.contains(x, y):
        return None
    native_x = int((x - viewport.left) * native_width / viewport.width)
    native_y = int((y - viewport.top) * native_height / viewport.height)
    return (
        max(0, min(native_width - 1, native_x)),
        max(0, min(native_height - 1, native_y)),
    )


def fit_aspect_viewport(container: Viewport, aspect_width: int = 16, aspect_height: int = 9) -> Viewport:
    """Fit พื้นที่เกมไว้กลาง container เช่น display child ที่มี black letterbox."""
    if container.width <= 0 or container.height <= 0 or aspect_width <= 0 or aspect_height <= 0:
        raise ValueError("container/aspect ต้องมีขนาดมากกว่า 0")
    width = min(container.width, round(container.height * aspect_width / aspect_height))
    height = min(container.height, round(container.width * aspect_height / aspect_width))
    left = container.left + (container.width - width) // 2
    top = container.top + (container.height - height) // 2
    return Viewport(left, top, left + width, top + height)


if os.name == "nt":
    _user32 = ctypes.windll.user32
    _enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _title(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _rect(hwnd: int) -> Viewport | None:
        value = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(value)):
            return None
        viewport = Viewport(value.left, value.top, value.right, value.bottom)
        return viewport if viewport.width > 0 and viewport.height > 0 else None

    def _is_mumu_title(title: str) -> bool:
        lowered = title.casefold()
        return (
            lowered.startswith("android device")
            or lowered == "mumunxdevice"
            or lowered.startswith("mumu player")
        ) and "pattern studio" not in lowered and "mumu live" not in lowered

    def _process_name(hwnd: int) -> str:
        process_id = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return ""
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
        return ""

    def _is_mumu_window(hwnd: int) -> bool:
        process_name = _process_name(hwnd).casefold()
        return _is_mumu_title(_title(hwnd)) or process_name in {
            "mumunxdevice.exe", "mumunxmain.exe", "nemuplayer.exe", "nemuheadless.exe",
        }

    def _root_window_at(x: int, y: int) -> int:
        point = wintypes.POINT(int(x), int(y))
        hwnd = _user32.WindowFromPoint(point)
        return int(_user32.GetAncestor(hwnd, 2)) if hwnd else 0  # GA_ROOT

    def _viewport_for_root(root: int) -> Viewport | None:
        candidates: list[tuple[float, int, Viewport]] = []

        @_enum_proc
        def collect(child: int, _lparam: int) -> bool:
            viewport = _rect(child)
            if viewport and viewport.width >= 320 and viewport.height >= 180:
                ratio_error = abs(viewport.width / viewport.height - 16 / 9)
                title_bonus = 0 if _title(child).casefold() in {"nemudisplay", "mumunxdevice"} else 1
                candidates.append((ratio_error, title_bonus, viewport))
            return True

        _user32.EnumChildWindows(root, collect, 0)
        good = [item for item in candidates if item[0] <= 0.12]
        if good:
            return min(good, key=lambda item: (item[1], item[0], -(item[2].width * item[2].height)))[2]
        # MuMu แบบหน้าต่างแนวตั้งมักให้ NemuDisplay เป็นพื้นที่สูงเต็มส่วน content
        # แล้ว letterbox เกม 16:9 ไว้ข้างใน ต้อง fit จาก display child ไม่ใช่ root
        # ที่รวม title bar/toolbar เพราะจะทำให้ Pause ROI เลื่อนในแนว Y.
        display_candidates = [item for item in candidates if item[1] == 0]
        if display_candidates:
            container = max(display_candidates, key=lambda item: item[2].width * item[2].height)[2]
            return fit_aspect_viewport(container)
        if candidates:
            container = max(candidates, key=lambda item: item[2].width * item[2].height)[2]
            if container.width * container.height >= 320 * 180:
                return fit_aspect_viewport(container)
        root_rect = _rect(root)
        if not root_rect:
            return None
        return fit_aspect_viewport(root_rect)

    def viewport_at(x: int, y: int) -> Viewport | None:
        """คืน viewport เฉพาะเมื่อหน้าต่างที่อยู่ใต้ cursor เป็น MuMu จริง."""
        root = _root_window_at(x, y)
        if not root or not _is_mumu_window(root):
            return None
        viewport = _viewport_for_root(root)
        return viewport if viewport and viewport.contains(x, y) else None

    def find_visible_viewport() -> Viewport | None:
        candidates: list[Viewport] = []

        @_enum_proc
        def collect(hwnd: int, _lparam: int) -> bool:
            if _user32.IsWindowVisible(hwnd) and not _user32.IsIconic(hwnd) and _is_mumu_window(hwnd):
                viewport = _viewport_for_root(hwnd)
                if viewport and viewport.width >= 640 and viewport.height >= 360:
                    screen_w = _user32.GetSystemMetrics(0)
                    screen_h = _user32.GetSystemMetrics(1)
                    center_x = viewport.left + viewport.width // 2
                    center_y = viewport.top + viewport.height // 2
                    # ImageGrab จับสิ่งที่มองเห็นบน desktop จึงใช้โหมดเร็วเฉพาะเมื่อ
                    # MuMu อยู่บนสุดที่กึ่งกลาง ป้องกันภาพวนเมื่อ Live View บัง MuMu
                    unobstructed = _root_window_at(center_x, center_y) == int(hwnd)
                    if unobstructed and viewport.right > 0 and viewport.bottom > 0 and viewport.left < screen_w and viewport.top < screen_h:
                        candidates.append(viewport)
            return True

        _user32.EnumWindows(collect, 0)
        return max(candidates, key=lambda item: item.width * item.height, default=None)

    def activate_mumu_window() -> bool:
        """นำหน้าต่างเล่น MuMu ที่ใหญ่ที่สุดขึ้นหน้า จากการคลิกของผู้ใช้ใน Studio."""
        candidates: list[tuple[int, int]] = []

        @_enum_proc
        def collect(hwnd: int, _lparam: int) -> bool:
            if _user32.IsWindowVisible(hwnd) and _is_mumu_window(hwnd):
                viewport = _rect(hwnd)
                if viewport:
                    title = _title(hwnd).casefold()
                    title_bonus = 1 if title.startswith("android device") else 0
                    candidates.append((title_bonus * 10**9 + viewport.width * viewport.height, int(hwnd)))
            return True

        _user32.EnumWindows(collect, 0)
        if not candidates:
            return False
        hwnd = max(candidates)[1]
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(_user32.SetForegroundWindow(hwnd))

    def is_mumu_foreground() -> bool:
        hwnd = int(_user32.GetForegroundWindow())
        return bool(hwnd and _is_mumu_window(hwnd))

    class MuMuROICapture:
        """Capture เฉพาะ ROI จากหน้าต่าง MuMu ที่กำลังเล่น เพื่อลด ADB latency."""

        def __init__(self, native_size: tuple[int, int] = (1280, 720), refresh_seconds: float = 0.75) -> None:
            self.native_width, self.native_height = map(int, native_size)
            self.refresh_seconds = max(0.1, float(refresh_seconds))
            self._viewport: Viewport | None = None
            self._last_refresh = 0.0

        def capture_roi(self, roi: object) -> tuple[Image.Image, str]:
            if not is_mumu_foreground():
                raise MuMuFastCaptureError("MuMu ไม่ได้อยู่ด้านหน้า")
            now = time.perf_counter()
            if self._viewport is None or now - self._last_refresh >= self.refresh_seconds:
                self._viewport = find_visible_viewport()
                self._last_refresh = now
            if self._viewport is None:
                raise MuMuFastCaptureError("มองไม่เห็นพื้นที่เกม MuMu")
            bbox = native_roi_to_screen_box(
                self._viewport, roi, self.native_width, self.native_height,
            )
            try:
                try:
                    image = ImageGrab.grab(bbox=bbox, all_screens=True)
                except TypeError:  # Pillow เก่าที่ยังไม่มี all_screens
                    image = ImageGrab.grab(bbox=bbox)
            except OSError as exc:
                self._viewport = None
                raise MuMuFastCaptureError(f"Capture หน้าต่าง MuMu ไม่สำเร็จ: {exc}") from exc
            width = int(roi["width"] if isinstance(roi, dict) else getattr(roi, "width"))
            height = int(roi["height"] if isinstance(roi, dict) else getattr(roi, "height"))
            if image.size != (width, height):
                image = image.resize((width, height), Image.Resampling.BILINEAR)
            return image.convert("RGB"), f"MuMu Window ROI {self._viewport.width}x{self._viewport.height}"

else:
    def viewport_at(_x: int, _y: int) -> Viewport | None:
        return None

    def find_visible_viewport() -> Viewport | None:
        return None

    def is_mumu_foreground() -> bool:
        return False

    def activate_mumu_window() -> bool:
        return False

    class MuMuROICapture:
        def __init__(self, native_size: tuple[int, int] = (1280, 720), refresh_seconds: float = 0.75) -> None:
            del native_size, refresh_seconds

        def capture_roi(self, _roi: object) -> tuple[Image.Image, str]:
            raise MuMuFastCaptureError("Fast MuMu capture รองรับเฉพาะ Windows")
