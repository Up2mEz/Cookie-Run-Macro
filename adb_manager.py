from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, UnidentifiedImageError


DEFAULT_MUMU_PORT_CANDIDATES = [5557, 16416]
COMMON_MUMU_ROOTS = [
    Path(r"C:\Program Files\Netease"),
    Path(r"C:\Program Files (x86)\Netease"),
    Path(r"C:\Program Files\MuMu"),
    Path(r"C:\Program Files (x86)\MuMu"),
    Path(r"C:\Program Files\Nemu"),
    Path(r"C:\Program Files (x86)\Nemu"),
    Path(r"D:\Program Files\Netease"),
    Path(r"D:\Program Files (x86)\Netease"),
    Path(r"D:\Program Files\MuMu"),
    Path(r"D:\Program Files (x86)\MuMu"),
    Path(r"D:\Program Files\Nemu"),
    Path(r"D:\Program Files (x86)\Nemu"),
]
ADB_FILENAMES = {"adb.exe", "adb_server.exe"}


class ADBError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    state: str
    model: str = "ไม่ทราบ"
    product: str = ""
    boot_completed: bool = False
    width: int | None = None
    height: int | None = None

    @property
    def resolution_text(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "ไม่ทราบ"

    @property
    def display_text(self) -> str:
        return f"{self.serial} — {self.model} — {self.resolution_text}"


def parse_adb_devices(output: str) -> list[str]:
    serials: list[str] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2 or fields[1] != "device":
            continue
        serial = fields[0]
        if serial not in seen:
            seen.add(serial)
            serials.append(serial)
    return serials


def parse_device_metadata(output: str, serial: str) -> tuple[str, str]:
    for raw_line in output.splitlines():
        fields = raw_line.strip().split()
        if len(fields) < 2 or fields[0] != serial:
            continue
        metadata = {}
        for field in fields[2:]:
            if ":" in field:
                key, value = field.split(":", 1)
                metadata[key] = value
        return metadata.get("model", "ไม่ทราบ").replace("_", " "), metadata.get("product", "")
    return "ไม่ทราบ", ""


def parse_wm_size(output: str) -> tuple[int, int]:
    matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", output, flags=re.IGNORECASE)
    if not matches:
        match = re.search(r"\b(\d{2,5})x(\d{2,5})\b", output)
        if not match:
            raise ADBError("ADB ไม่ส่งค่าความละเอียดหน้าจอ")
        return int(match.group(1)), int(match.group(2))
    width, height = matches[-1]
    return int(width), int(height)


def _deduplicate_paths(paths: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve()).casefold()
        except OSError:
            key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            found.append(path)
    return found


def discover_adb_paths(configured_path: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if configured_path:
        candidates.append(Path(configured_path))
    which_adb = shutil.which("adb")
    if which_adb:
        candidates.append(Path(which_adb))
    for root in COMMON_MUMU_ROOTS:
        if not root.is_dir():
            continue
        # จำกัดการค้นหาอยู่ใต้โฟลเดอร์ผู้ผลิตที่ระบุเท่านั้น
        try:
            candidates.extend(path for path in root.rglob("*.exe") if path.name.casefold() in ADB_FILENAMES)
        except OSError:
            continue
    return [path for path in _deduplicate_paths(candidates) if path.is_file()]


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


class PersistentShell:
    def __init__(self, adb_path: Path, serial: str) -> None:
        self._lock = threading.Lock()
        self._closed = False
        try:
            self.process = subprocess.Popen(
                [str(adb_path), "-s", serial, "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=_startupinfo(),
            )
        except OSError as exc:
            raise ADBError(f"เปิด persistent ADB shell ไม่สำเร็จ: {exc}") from exc

    def send(self, command: str) -> None:
        safe_command = command.replace("\r", " ").replace("\n", " ").strip()
        with self._lock:
            if self.process.poll() is not None or self.process.stdin is None:
                raise ADBError("Persistent ADB shell ปิด unexpectedly")
            try:
                self.process.stdin.write(safe_command + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ADBError("ส่งคำสั่งไป persistent ADB shell ไม่สำเร็จ") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.process.stdin:
                try:
                    self.process.stdin.write("exit\n")
                    self.process.stdin.flush()
                    self.process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                self.process.wait(timeout=0.7)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=0.3)

    def __enter__(self) -> "PersistentShell":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ADBManager:
    def __init__(
        self,
        adb_path: str | Path,
        *,
        timeout: float = 5.0,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.adb_path = Path(adb_path)
        self.timeout = timeout
        self._runner = runner
        self._process_lock = threading.RLock()
        self._processes: set[subprocess.Popen] = set()
        self._cancelled = threading.Event()

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        binary: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        if self._cancelled.is_set():
            raise ADBError("คำสั่ง ADB ถูกยกเลิก")
        command = [str(self.adb_path), *args]
        if self._runner is subprocess.run:
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=not binary,
                    startupinfo=_startupinfo(),
                )
            except OSError as exc:
                raise ADBError(f"เปิด ADB ไม่สำเร็จ: {exc}") from exc
            with self._process_lock:
                self._processes.add(process)
            try:
                stdout, stderr = process.communicate(timeout=timeout or self.timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate()
                raise ADBError(f"คำสั่ง ADB timeout หลัง {timeout or self.timeout:g} วินาที") from exc
            finally:
                with self._process_lock:
                    self._processes.discard(process)
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            if self._cancelled.is_set():
                raise ADBError("คำสั่ง ADB ถูกยกเลิก")
        else:
            try:
                result = self._runner(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=not binary,
                    timeout=timeout or self.timeout,
                    startupinfo=_startupinfo(),
                )
            except subprocess.TimeoutExpired as exc:
                raise ADBError(f"คำสั่ง ADB timeout หลัง {timeout or self.timeout:g} วินาที") from exc
            except OSError as exc:
                raise ADBError(f"เปิด ADB ไม่สำเร็จ: {exc}") from exc
        if check and result.returncode != 0:
            stderr = result.stderr.decode(errors="replace") if binary and isinstance(result.stderr, bytes) else result.stderr
            raise ADBError((stderr or "ADB command failed").strip())
        return result

    def cancel_all(self) -> None:
        self._cancelled.set()
        with self._process_lock:
            processes = list(self._processes)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=0.2)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def validate_executable(self) -> bool:
        if not self.adb_path.is_file():
            return False
        try:
            self._run(["devices"], timeout=4)
            return True
        except ADBError:
            return False

    def connect(self, serial: str) -> str:
        result = self._run(["connect", serial], timeout=8, check=False)
        combined = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        if result.returncode != 0 and "already connected" not in combined.lower():
            raise ADBError(combined or f"เชื่อมต่อ {serial} ไม่สำเร็จ")
        return combined

    def connect_candidate_ports(self, ports: Iterable[int], host: str = "127.0.0.1") -> dict[str, str]:
        results: dict[str, str] = {}
        for port in dict.fromkeys(int(port) for port in ports):
            serial = f"{host}:{port}"
            try:
                results[serial] = self.connect(serial)
            except (ADBError, ValueError) as exc:
                results[serial] = str(exc)
        return results

    def devices_output(self) -> str:
        return str(self._run(["devices", "-l"]).stdout)

    def ready_serials(self) -> list[str]:
        return parse_adb_devices(self.devices_output())

    def shell(self, serial: str, command: str, *, timeout: float | None = None) -> str:
        result = self._run(["-s", serial, "shell", command], timeout=timeout)
        return str(result.stdout).strip()

    def probe_device(self, serial: str, devices_output: str | None = None) -> DeviceInfo:
        output = devices_output if devices_output is not None else self.devices_output()
        if serial not in parse_adb_devices(output):
            raise ADBError(f"อุปกรณ์ {serial} ไม่ได้อยู่ในสถานะ device")
        boot = self.shell(serial, "getprop sys.boot_completed")
        if boot.strip() != "1":
            raise ADBError(f"อุปกรณ์ {serial} ยัง boot ไม่เสร็จ")
        size = self.shell(serial, "wm size")
        width, height = parse_wm_size(size)
        # MuMu มักรายงาน physical size เป็นแนวตั้ง แม้ภาพเกมจริงหมุนเป็นแนวนอน
        # ใช้ขนาด screencap เป็นขนาด interaction หาก Capture ได้สำเร็จ
        try:
            png_data = self.capture_png(serial)
            with Image.open(io.BytesIO(png_data)) as screenshot:
                width, height = screenshot.size
        except (ADBError, UnidentifiedImageError, OSError, ValueError):
            pass
        model, product = parse_device_metadata(output, serial)
        if model == "ไม่ทราบ":
            try:
                model = self.shell(serial, "getprop ro.product.model") or model
            except ADBError:
                pass
        return DeviceInfo(serial, "device", model, product, True, width, height)

    def discover_devices(self, ports: Iterable[int] = DEFAULT_MUMU_PORT_CANDIDATES) -> list[DeviceInfo]:
        self.connect_candidate_ports(ports)
        output = self.devices_output()
        devices: list[DeviceInfo] = []
        for serial in parse_adb_devices(output):
            try:
                devices.append(self.probe_device(serial, output))
            except ADBError:
                continue
        return devices

    def capture_png(self, serial: str, *, timeout: float = 8.0) -> bytes:
        result = self._run(["-s", serial, "exec-out", "screencap", "-p"], timeout=timeout, binary=True)
        png_data = bytes(result.stdout)
        try:
            with Image.open(io.BytesIO(png_data)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ADBError("ADB ส่งภาพ PNG ที่เปิดไม่ได้") from exc
        return png_data

    @staticmethod
    def decode_raw_screencap(raw_data: bytes) -> Image.Image:
        """แปลง Android screencap แบบ raw (12/16-byte header) เป็นภาพ RGB."""
        if len(raw_data) < 12:
            raise ADBError("ADB ส่งภาพ Raw สั้นเกินไป")
        width, height, pixel_format = struct.unpack_from("<III", raw_data, 0)
        if not (1 <= width <= 10000 and 1 <= height <= 10000):
            raise ADBError("ADB ส่งขนาดภาพ Raw ไม่ถูกต้อง")
        if pixel_format != 1:
            raise ADBError(f"Raw pixel format {pixel_format} ยังไม่รองรับ")
        payload_size = width * height * 4
        if len(raw_data) == 12 + payload_size:
            offset = 12
        elif len(raw_data) >= 16 + payload_size:
            offset = 16
        else:
            raise ADBError("ADB ส่ง payload ภาพ Raw ไม่ครบ")
        return Image.frombytes("RGBA", (width, height), raw_data[offset:offset + payload_size]).convert("RGB")

    def capture_raw_image(self, serial: str, *, timeout: float = 5.0) -> Image.Image:
        result = self._run(["-s", serial, "exec-out", "screencap"], timeout=timeout, binary=True)
        return self.decode_raw_screencap(bytes(result.stdout))

    def capture_image(self, serial: str, *, timeout: float = 5.0) -> tuple[Image.Image, str]:
        """ใช้ Raw ที่เร็วกว่าเป็นหลัก และ fallback เป็น PNG เพื่อความเข้ากันได้."""
        try:
            return self.capture_raw_image(serial, timeout=timeout), "ADB Raw"
        except ADBError:
            png_data = self.capture_png(serial, timeout=max(timeout, 8.0))
            with Image.open(io.BytesIO(png_data)) as image:
                return image.convert("RGB"), "ADB PNG fallback"

    def open_persistent_shell(self, serial: str) -> PersistentShell:
        return PersistentShell(self.adb_path, serial)
