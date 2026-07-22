# MuMu Pattern Studio — Codex Implementation Specification v2

> ใช้ไฟล์นี้เป็นคำสั่งให้ Codex เปิดและแก้โปรเจกต์ `mumu_macro_studio` โดยตรง
> เป้าหมายคือทำโปรแกรมอัดและเล่นแพตเทิร์นบน MuMu ที่เบา เสถียร ใช้ง่าย และไม่ให้ความสุ่มทำลายคำสั่งหลบสิ่งกีดขวางที่จำเป็น

---

## 1. ข้อมูลที่ยืนยันจากเครื่องจริง

จากหน้า **MuMu Settings → Others → ADB port**

- Instance: `#1`
- Device name: `Android Device-1`
- หน้าจอแสดงค่าพอร์ต: `5557,16416`

อย่าสมมติว่าค่าใดค่าหนึ่งเป็นพอร์ตที่ถูกต้องเสมอ ให้ถือว่าเป็น **candidate ports** และทดลองเชื่อมต่อทั้งสองค่า:

```text
127.0.0.1:5557
127.0.0.1:16416
```

ให้เลือกเฉพาะ serial ที่:

1. ปรากฏใน `adb devices`
2. มีสถานะ `device` ไม่ใช่ `offline` หรือ `unauthorized`
3. เรียก `shell getprop sys.boot_completed` แล้วได้ `1`
4. เรียก `shell wm size` ได้สำเร็จ

ถ้าทั้งสองค่าเชื่อมต่อได้ ให้แสดง Dropdown ให้ผู้ใช้เลือก และแสดงข้อมูลประกอบ เช่น serial, model และขนาดหน้าจอ

---

## 2. เป้าหมายหลัก

อัปเกรดโปรแกรมให้รองรับ:

1. อัด Jump และ Slide เป็นหลายแพตเทิร์น
2. เก็บเวลาแบบ absolute จากจุดเริ่มด่าน
3. Auto Sync โดยตรวจปุ่ม Pause มุมขวาบน
4. Manual fallback ด้วย `F9`
5. Required obstacle events ที่ห้ามสุ่ม
6. Safe random events ที่ใช้ได้เฉพาะช่วงปลอดภัย
7. ค้นหา ADB และ MuMu serial อัตโนมัติ
8. GUI ที่ตั้งค่าและทดสอบทุกอย่างได้
9. ใช้ dependency ให้น้อยและเบาที่สุด
10. รองรับไฟล์ JSON เดิม

---

## 3. ข้อจำกัดด้าน dependency

อนุญาตเฉพาะ:

```text
pynput
Pillow
```

และ Python standard library เช่น:

```text
tkinter
subprocess
threading
queue
json
pathlib
shutil
time
io
statistics
unittest
```

ห้ามเพิ่ม:

```text
opencv-python
numpy
torch
tensorflow
pyautogui
selenium
```

เหตุผลคือ Auto Sync ตรวจเพียง ROI เล็ก ๆ และทำงานเฉพาะช่วงรอเริ่มด่าน จึงไม่จำเป็นต้องใช้ Computer Vision framework ขนาดใหญ่

---

## 4. Architecture ที่ต้องการ

แยกโค้ดเป็นโมดูลแทนการรวมทุกอย่างใน `app.py`

```text
mumu_macro_studio/
├─ app.py
├─ adb_manager.py
├─ sync_detector.py
├─ event_model.py
├─ pattern_store.py
├─ recorder.py
├─ player.py
├─ ui/
│  ├─ main_window.py
│  ├─ event_dialog.py
│  └─ roi_selector.py
├─ tests/
│  ├─ test_adb_manager.py
│  ├─ test_event_model.py
│  ├─ test_sync_detector.py
│  └─ test_pattern_store.py
├─ patterns/
├─ templates/
├─ config.json
├─ requirements.txt
└─ README_TH.md
```

สามารถปรับโครงสร้างได้ถ้ามีเหตุผล แต่ต้องแยกอย่างน้อย:

- ADB discovery/control
- Auto Sync
- Event validation
- Pattern persistence
- GUI

---

# 5. ADB Auto Discovery

## 5.1 ลำดับการหา ADB executable

ใช้ลำดับต่อไปนี้:

1. Path ที่บันทึกอยู่ใน `config.json`
2. `shutil.which("adb")`
3. ค้นเฉพาะ common MuMu directories
4. ให้ผู้ใช้ Browse เลือกเอง

ตัวอย่าง root ที่อนุญาตให้ค้น:

```text
C:\Program Files\Netease
C:\Program Files (x86)\Netease
C:\Program Files\MuMu
C:\Program Files (x86)\MuMu
C:\Program Files\Nemu
C:\Program Files (x86)\Nemu
```

ชื่อไฟล์ที่ค้น:

```text
adb.exe
adb_server.exe
```

ข้อกำหนด:

- ห้าม recursive scan ทั้งไดรฟ์
- ห้าม recursive scan ทั้ง `C:\Program Files` โดยไม่มีการจำกัดโฟลเดอร์
- ตัด path ซ้ำ
- ตรวจว่า executable เรียก `devices` ได้จริงก่อนนำมาใช้
- แสดง path ที่พบใน Dropdown
- เก็บตัวเลือกที่ใช้งานสำเร็จไว้ใน config

## 5.2 การหา Device/Serial

เริ่มจาก candidate ports ที่รู้จากเครื่องจริง:

```python
DEFAULT_MUMU_PORT_CANDIDATES = [5557, 16416]
```

ทำตามขั้นตอน:

```text
adb connect 127.0.0.1:5557
adb connect 127.0.0.1:16416
adb devices -l
```

จากนั้น parse เฉพาะบรรทัดที่สถานะเป็น `device`

ต้องรองรับกรณี:

- มีอุปกรณ์เดียว → เลือกให้อัตโนมัติ
- มีหลายอุปกรณ์ → แสดง Dropdown
- มี `offline` → ไม่เลือก
- connect สำเร็จแต่ไม่ boot → ไม่เลือก
- ไม่มีอุปกรณ์ → ให้กรอก host/port เอง
- MuMu เปลี่ยนพอร์ตในอนาคต → มีช่องเพิ่ม candidate port

เพิ่มปุ่มใน GUI:

```text
ค้นหา ADB
ค้นหา MuMu Devices
ทดสอบอุปกรณ์
```

ผลการทดสอบควรแสดง:

```text
Serial: 127.0.0.1:5557
State: device
Boot completed: 1
Model: ...
Resolution: 1600x900
ADB executable: ...
```

อย่า hardcode ว่า serial ต้องเป็น `127.0.0.1:7555`

## 5.3 ADB command strategy

- Gameplay commands ใช้ persistent `adb shell` เพื่อลด process overhead
- Screenshot ใช้ subprocess แยก:

```text
adb -s <serial> exec-out screencap -p
```

เพราะผลลัพธ์เป็น binary PNG ไม่ควรใช้ shell process เดียวกับคำสั่ง tap/swipe

- ทุก subprocess ต้องมี timeout
- ปิด process และ pipe ให้สะอาดเมื่อ Stop หรือปิดโปรแกรม
- ห้ามใช้ busy loop

---

# 6. Lightweight Auto Sync

## 6.1 เป้าหมาย

ช่วงโหลดเกมใช้เวลาต่างกัน จึงห้ามเริ่มแพตเทิร์นจากเวลาที่กด Start หรือจาก `sleep()` คงที่

ให้เริ่มนาฬิกาแพตเทิร์นเมื่อระบบตรวจพบว่า **ปุ่ม Pause มุมขวาบนปรากฏแล้ว**

## 6.2 Pipeline

```text
Play Pattern
→ Connect ADB
→ WAITING_FOR_GAME
→ Capture screenshot ผ่าน ADB
→ Crop เฉพาะ Pause ROI
→ Grayscale
→ Resize เป็น 64×64
→ เปรียบเทียบกับ template
→ พบต่อเนื่องตามจำนวนที่กำหนด
→ รอ sync offset
→ เริ่มแพตเทิร์นที่ t = 0
```

ค่า default:

```json
{
  "mode": "auto_pause_icon",
  "poll_ms": 400,
  "threshold": 0.88,
  "consecutive_matches": 2,
  "timeout_seconds": 25,
  "offset_ms": 0,
  "manual_fallback": true
}
```

ใช้ `400 ms` เป็นค่าเริ่มต้นเพื่อให้เบา โปรแกรมทำงานประมาณ 2.5 checks/second เฉพาะตอนรอโหลด และหยุด screenshot polling ทันทีเมื่อ sync สำเร็จ

ให้ผู้ใช้ปรับได้ช่วง:

```text
poll_ms: 250–1000
threshold: 0.50–1.00
consecutive_matches: 1–5
timeout_seconds: 5–120
offset_ms: -500 ถึง 3000
```

ถ้า `offset_ms` เป็นค่าลบ ให้จัดการอย่างปลอดภัย โดยไม่สามารถเริ่มย้อนหลังได้จริง ควรแสดงคำเตือนหรือ clamp เป็น 0 ใน runtime

## 6.3 Image similarity

ใช้ Pillow เท่านั้น:

```python
from PIL import Image, ImageChops, ImageStat


def image_similarity(current: Image.Image, template: Image.Image) -> float:
    current_small = current.convert("L").resize((64, 64))
    template_small = template.convert("L").resize((64, 64))

    difference = ImageChops.difference(current_small, template_small)
    mean_difference = ImageStat.Stat(difference).mean[0]

    return max(0.0, min(1.0, 1.0 - mean_difference / 255.0))
```

ปรับปรุงได้โดย normalize contrast หรือใช้ edge แบบเบาด้วย Pillow ถ้าภาพพื้นหลังมี animation แต่ห้ามเพิ่ม OpenCV/NumPy

ต้อง:

- ตรวจขนาด ROI ก่อนเปรียบเทียบ
- จัดการ PNG decode error
- จัดการ ADB timeout
- แสดง similarity ล่าสุดใน GUI
- เก็บ similarity history ล่าสุดไม่เกิน 20 ค่าเพื่อ debug
- ไม่เขียน screenshot ลง disk ทุก poll
- ใช้ `BytesIO` เปิด PNG ใน memory

## 6.4 Template setup workflow

เพิ่มปุ่ม:

```text
ตั้งค่า Pause Template
ทดสอบ Auto Sync
```

ขั้นตอน:

1. ผู้ใช้เปิดด่านที่ปุ่ม Pause ปรากฏ
2. โปรแกรม Capture screenshot ผ่าน ADB
3. แสดงภาพใน Tkinter Canvas
4. ผู้ใช้ลากกรอบ ROI รอบปุ่ม Pause
5. บันทึก ROI เป็นพิกัด Android ต้นฉบับ
6. Crop และบันทึก template เป็น PNG
7. ผูก template กับ pattern ปัจจุบัน
8. แสดง preview และ similarity ทดสอบ

ข้อกำหนด ROI:

- ต้องมีขนาดอย่างน้อย 12×12 px
- แนะนำไม่เกิน 160×160 px
- ควรครอบเฉพาะปุ่ม Pause และขอบรอบปุ่มเล็กน้อย
- อย่ารวมคะแนน ตัวเลข หรือ UI ที่เปลี่ยนตลอดเวลา
- ถ้า Canvas ย่อ/ขยายภาพ ต้องแปลงพิกัดกลับเป็น Android native coordinates อย่างถูกต้อง

ตัวอย่างข้อมูลใน pattern:

```json
{
  "sync": {
    "mode": "auto_pause_icon",
    "template_path": "templates/stage_01_pause.png",
    "roi": {
      "x": 1450,
      "y": 20,
      "width": 90,
      "height": 90
    },
    "poll_ms": 400,
    "threshold": 0.88,
    "consecutive_matches": 2,
    "timeout_seconds": 25,
    "offset_ms": 120,
    "manual_fallback": true
  }
}
```

Path ที่เก็บใน JSON ควรเป็น relative path จาก project directory ไม่ใช่ absolute path

---

# 7. Manual Sync Fallback

กำหนดคีย์ลัด:

```text
F6 = เริ่มอัด
F7 = หยุดอัดและบันทึก
F8 = Emergency Stop
F9 = Manual Sync / ยืนยันว่าด่านเริ่มแล้ว
```

F9 ต้องทำงานได้ทั้ง:

- ระหว่าง Auto Sync กำลังตรวจ
- หลัง Auto Sync timeout
- ตอนอัดแพตเทิร์น
- ตอนเล่นแพตเทิร์น

Logic:

```text
Auto Sync พบปุ่ม → เริ่มอัตโนมัติ
ผู้ใช้กด F9 ก่อนพบปุ่ม → เริ่มทันทีด้วย Manual Sync
Auto Sync timeout → ไม่ Abort → เปลี่ยนเป็น WAITING_FOR_MANUAL_SYNC
ผู้ใช้กด F9 → เริ่มแพตเทิร์น
F8 ทุกสถานะ → ยกเลิกทันที
```

ใช้ `threading.Event` อย่างน้อย:

```python
stop_event
manual_sync_event
auto_sync_event
```

ห้ามให้ worker thread เรียก Tkinter widget โดยตรง ให้ใช้:

```python
root.after(...)
```

---

# 8. State Machine

กำหนดสถานะอย่างชัดเจน:

```text
IDLE
DISCOVERING_ADB
CONNECTING
WAITING_FOR_AUTO_SYNC
WAITING_FOR_MANUAL_SYNC
SYNCED
PLAYING
RECORDING_WAITING_SYNC
RECORDING
STOPPING
STOPPED
ERROR
```

ทุก transition ต้องตรวจสอบได้ และ GUI ต้องแสดงสถานะปัจจุบัน

ตัวอย่าง:

```text
IDLE
→ CONNECTING
→ WAITING_FOR_AUTO_SYNC
→ SYNCED
→ PLAYING
→ STOPPED
```

กรณี timeout:

```text
WAITING_FOR_AUTO_SYNC
→ WAITING_FOR_MANUAL_SYNC
→ SYNCED
→ PLAYING
```

---

# 9. Event Safety Model

นี่เป็นข้อกำหนดสำคัญที่สุด

## 9.1 Required Obstacle Event

ใช้สำหรับคำสั่งที่จำเป็นต่อการหลบสิ่งกีดขวางจริง

กฎบังคับ:

```text
event_class = required
chance = 100
jitter_ms = 0
action ต้องเป็น jump หรือ slide เท่านั้น
ห้าม random choice
ห้าม none
```

ตัวอย่าง:

```json
{
  "id": "evt_001",
  "at": 2.350,
  "event_class": "required",
  "type": "action",
  "action": "jump",
  "chance": 100,
  "jitter_ms": 0
}
```

สำหรับ Slide:

```json
{
  "id": "evt_002",
  "at": 4.120,
  "event_class": "required",
  "type": "action",
  "action": "slide",
  "duration_ms": 430,
  "chance": 100,
  "jitter_ms": 0
}
```

ทุก Event ที่ได้จากการอัดต้อง default เป็น `required`

ใน GUI เมื่อเลือก Required:

- ปิดช่อง Chance
- ปิดช่อง Jitter
- ปิด Choice
- แสดงข้อความ `Required: บังคับทำ 100%`
- ถ้ามีค่าเก่าที่ผิด ให้แก้อัตโนมัติ

แม้ผู้ใช้แก้ JSON เอง โปรแกรมต้อง normalize กลับเป็นค่าที่ปลอดภัยก่อน Save และ Playback

## 9.2 Safe Zone

ความสุ่มต้องเกิดได้เฉพาะช่วงที่ผู้ใช้ระบุว่าปลอดภัยเท่านั้น

เพิ่มข้อมูล:

```json
{
  "safe_zones": [
    {
      "id": "zone_001",
      "start": 6.000,
      "end": 7.500,
      "label": "ทางตรง ไม่มีสิ่งกีดขวาง"
    }
  ]
}
```

กฎ:

- `start >= 0`
- `end > start`
- Safe zones ซ้อนกันได้ แต่ควรรวมช่วงที่ซ้อนกันตอน validate
- GUI ต้องมีตารางหรือ Timeline สำหรับเพิ่ม/แก้/ลบ safe zone
- Safe random event ทุกตัวต้องอยู่ภายใน safe zone
- Required event ไม่จำเป็นต้องอยู่ใน safe zone
- ถ้าไม่มี safe zone ห้ามสร้าง Safe Random Event

## 9.3 Safe Random Event

ใช้ได้เฉพาะใน safe zone

รองรับสองแบบ:

### Optional action

```json
{
  "id": "evt_safe_001",
  "at": 6.600,
  "event_class": "safe_random",
  "type": "optional_action",
  "action": "jump",
  "chance": 25,
  "jitter_ms": 40,
  "safe_zone_id": "zone_001"
}
```

### Weighted choice

```json
{
  "id": "evt_safe_002",
  "at": 7.000,
  "event_class": "safe_random",
  "type": "choice",
  "options": {
    "none": 50,
    "jump": 30,
    "slide": 20
  },
  "duration_ms": 400,
  "jitter_ms": 30,
  "safe_zone_id": "zone_001"
}
```

กฎ:

- `none`, `jump`, `slide` weight ต้องไม่ติดลบ
- ผลรวมน้ำหนักต้องมากกว่า 0
- `chance` ต้องอยู่ 0–100
- `jitter_ms >= 0`
- Slide duration ต้องมากกว่า 0
- Event ต้องอยู่ใน safe zone ที่อ้างถึง
- ช่วง `at ± jitter` ต้องไม่ออกนอก safe zone
- ถ้า jitter มากเกินไป ให้ลดอัตโนมัติถึงค่าสูงสุดที่ยังปลอดภัย และแจ้งผู้ใช้
- Safe random ต้องไม่อยู่ใกล้ Required event เกิน safety gap

ค่า default:

```json
{
  "safe_random_min_gap_ms": 250
}
```

ตรวจว่า effective time range ของ Safe Random อยู่ห่าง Required event อย่างน้อย 250 ms ทั้งก่อนและหลัง ถ้าไม่ผ่านให้ reject พร้อมข้อความที่เข้าใจง่าย

## 9.4 Event normalization

สร้างฟังก์ชัน pure function ที่ test ได้:

```python
def normalize_event(
    event: dict,
    safe_zones: list[dict],
    required_events: list[dict],
    min_gap_ms: int,
) -> tuple[dict, list[str]]:
    ...
```

Required normalization:

```python
if event_class == "required":
    type = "action"
    chance = 100
    jitter_ms = 0
    remove options
```

Backward compatibility:

```text
ถ้า event ไม่มี event_class → ถือเป็น required
ถ้า pattern ไม่มี safe_zones → ใช้ []
ถ้า required เก่ามี chance/jitter → บังคับเป็น 100/0
```

---

# 10. Timing และ Playback

ใช้:

```python
time.perf_counter()
```

Event time ต้องเป็น absolute จาก sync anchor:

```text
sync สำเร็จ = t0
event.at = 2.350
target = t0 + 2.350
```

ห้าม schedule แบบสะสม:

```text
sleep(delay_from_previous)
```

เพราะ event ที่ถูกสุ่มข้ามหรือ command overhead จะทำให้เวลาคลาดเคลื่อนสะสม

Player logic:

1. Normalize และ validate pattern
2. Connect ADB
3. Auto/Manual sync
4. กำหนด `round_start = perf_counter()`
5. เรียง Event ตาม effective timestamp
6. รอถึง target timestamp โดยใช้ `stop_event.wait(timeout)`
7. ส่งคำสั่งผ่าน persistent ADB shell
8. F8 หยุดได้ทันที
9. ปิด shell เมื่อจบ

ถ้า Safe Random มี jitter ให้สุ่ม effective timestamp ก่อนเริ่มรอบ แล้วเรียง Event ใหม่ตามเวลาที่สุ่มได้

Required event ห้ามมี jitter

---

# 11. Recording Workflow

## อัดแบบ Manual Sync

```text
เลือกชื่อ pattern
→ กด F6
→ โปรแกรมเข้าสถานะ RECORDING_WAITING_SYNC
→ ผู้ใช้เริ่มด่าน
→ เมื่อด่านเริ่มจริง กด F9
→ t = 0
→ กด J/K เพื่อเล่น
→ F7 บันทึก
```

## อัดแบบ Auto Sync

ถ้ามี Pause template แล้ว:

```text
F6
→ WAITING_FOR_AUTO_SYNC
→ พบ Pause icon
→ offset
→ เริ่มอัดที่ t = 0
```

ผู้ใช้กด F9 เพื่อ fallback ได้ตลอด

การอัด:

```text
J key down → required jump
K key down → เริ่ม required slide
K key up → บันทึก slide duration
```

ป้องกัน key repeat และปุ่มค้าง

---

# 12. Pattern JSON Schema

ตัวอย่างสมบูรณ์:

```json
{
  "schema_version": 2,
  "name": "stage_01",
  "created_at": "2026-07-18T17:00:00+07:00",
  "device": {
    "preferred_serial": "127.0.0.1:5557",
    "resolution": {
      "width": 1600,
      "height": 900
    }
  },
  "controls": {
    "jump": {
      "x": 1420,
      "y": 760
    },
    "slide": {
      "x": 180,
      "y": 760
    }
  },
  "sync": {
    "mode": "auto_pause_icon",
    "template_path": "templates/stage_01_pause.png",
    "roi": {
      "x": 1450,
      "y": 20,
      "width": 90,
      "height": 90
    },
    "poll_ms": 400,
    "threshold": 0.88,
    "consecutive_matches": 2,
    "timeout_seconds": 25,
    "offset_ms": 120,
    "manual_fallback": true
  },
  "safety": {
    "safe_random_min_gap_ms": 250
  },
  "safe_zones": [
    {
      "id": "zone_001",
      "start": 6.0,
      "end": 7.5,
      "label": "ทางตรงปลอดภัย"
    }
  ],
  "events": [
    {
      "id": "evt_001",
      "at": 2.35,
      "event_class": "required",
      "type": "action",
      "action": "jump",
      "chance": 100,
      "jitter_ms": 0
    },
    {
      "id": "evt_002",
      "at": 4.12,
      "event_class": "required",
      "type": "action",
      "action": "slide",
      "duration_ms": 430,
      "chance": 100,
      "jitter_ms": 0
    },
    {
      "id": "evt_safe_001",
      "at": 6.7,
      "event_class": "safe_random",
      "type": "choice",
      "options": {
        "none": 50,
        "jump": 30,
        "slide": 20
      },
      "duration_ms": 400,
      "jitter_ms": 30,
      "safe_zone_id": "zone_001"
    }
  ]
}
```

ใช้ atomic save:

```text
เขียนไฟล์ .tmp
→ flush
→ replace ไฟล์จริง
```

ทำ backup ไฟล์เดิมก่อน migration:

```text
stage_01.json.bak
```

---

# 13. GUI Requirements

## Connection section

- ADB executable Dropdown
- Browse
- Auto Detect ADB
- Candidate ports input
- Detect MuMu Devices
- Device Dropdown
- Test Connection
- แสดง model, boot status, resolution

## Sync section

- Mode:
  - Auto Pause Icon
  - Manual F9
- Capture Template
- ROI preview
- Threshold
- Poll interval
- Consecutive matches
- Timeout
- Offset
- Test Sync
- Live similarity value
- Progress/status

## Pattern section

- Pattern Dropdown
- New
- Rename
- Duplicate
- Delete
- Record
- Play
- Repeat count
- Stop

## Event table

Columns:

```text
Time
Class
Type
Action/Options
Chance
Jitter
Duration
Safe Zone
Validation
```

สีหรือข้อความ:

```text
REQUIRED
SAFE RANDOM
INVALID
```

อย่าพึ่งสีเพียงอย่างเดียว ต้องมีข้อความด้วย

## Safe Zone section

- Add Safe Zone
- Edit
- Delete
- Start/End
- Label
- แสดงจำนวน Safe Random events ภายใน zone

## Status bar

แสดง:

```text
State
Selected device
Current pattern
Similarity
Round
Last error
Hotkeys
```

---

# 14. Error Handling

ต้องมีข้อความเฉพาะกรณี:

- ไม่พบ ADB
- ADB executable ใช้งานไม่ได้
- ไม่พบ MuMu device
- Device offline
- Boot ยังไม่เสร็จ
- Screenshot timeout
- PNG decode ไม่สำเร็จ
- Template หาย
- ROI เกินขอบภาพ
- Resolution ไม่ตรงกับตอนสร้าง template
- Auto Sync timeout
- Pattern invalid
- Safe Random อยู่นอก Safe Zone
- Safe Random ใกล้ Required มากเกินไป
- Persistent shell ปิด unexpectedly

เมื่อ resolution ปัจจุบันไม่ตรงกับ pattern:

```text
ห้ามเริ่มแบบเงียบ ๆ
```

ให้ผู้ใช้เลือก:

```text
ยกเลิก
ใช้ต่อโดยยอมรับความเสี่ยง
สร้าง template/พิกัดใหม่
```

---

# 15. Performance Requirements

- Idle CPU ควรแทบเป็นศูนย์
- Auto Sync ตรวจภาพเฉพาะตอน `WAITING_FOR_AUTO_SYNC`
- Default poll 400 ms
- หลัง Sync สำเร็จต้องหยุด screenshot worker ทันที
- ไม่เก็บ screenshot ทุกเฟรมลง disk
- ไม่ใช้ busy loop
- Gameplay commands ใช้ persistent ADB shell
- GUI ต้องไม่ค้างระหว่าง connect, screenshot หรือ sync
- Worker thread ต้องหยุดภายในประมาณ 1 วินาทีหลัง F8
- ปิดโปรแกรมแล้วต้องไม่มี ADB/Python child process ค้าง

---

# 16. Tests ที่ต้องมี

ใช้ `unittest` โดยไม่เพิ่ม pytest ก็ได้

## test_adb_manager.py

ทดสอบ parse:

```text
List of devices attached
127.0.0.1:5557    device product:... model:...
127.0.0.1:16416   offline
```

ต้องได้:

```python
["127.0.0.1:5557"]
```

ทดสอบ:

- empty output
- daemon messages
- multiple devices
- unauthorized/offline exclusion
- duplicate serial removal

## test_sync_detector.py

สร้างภาพ Pillow ใน memory แล้วทดสอบ:

- ภาพเหมือนกัน similarity ใกล้ 1.0
- ภาพต่างกันมาก similarity ต่ำ
- grayscale/RGB ทำงาน
- resize ทำงาน
- invalid ROI
- consecutive matching logic
- manual event ยกเลิก Auto Sync ได้

## test_event_model.py

ทดสอบ:

- old event ไม่มี `event_class` → required
- required chance 50 → ถูกแก้เป็น 100
- required jitter 20 → ถูกแก้เป็น 0
- required choice → ถูกแก้หรือ reject อย่างชัดเจน
- safe random นอก zone → invalid
- jitter ออกนอก zone → clamp/reject ตาม design
- negative weight → invalid
- all zero weights → invalid
- safe random ใกล้ required → invalid
- safe random ที่ถูกต้อง → valid

## test_pattern_store.py

ทดสอบ:

- load schema v1
- migrate เป็น v2
- backup
- atomic save
- relative template path
- corrupted JSON error

รัน:

```powershell
python -m unittest discover -s tests -v
```

และ:

```powershell
python -m compileall .
```

---

# 17. Acceptance Criteria

งานถือว่าเสร็จเมื่อ:

1. โปรแกรมค้นหา ADB ได้ หรือให้เลือกเองได้
2. โปรแกรมทดลองพอร์ต `5557` และ `16416` ได้
3. โปรแกรมแสดง serial ที่สถานะพร้อมใช้งาน
4. Capture screenshot จาก MuMu ได้
5. ผู้ใช้ลาก ROI และสร้าง Pause template ได้
6. Auto Sync เริ่มเมื่อ Pause icon พบติดต่อกันตามค่า config
7. Auto Sync หยุด polling หลังเริ่มด่าน
8. F9 เริ่ม Manual Sync ได้ทุกช่วง
9. Timeout แล้วรอ F9 แทนการ Abort
10. F8 หยุดทุก worker/process ได้
11. Event ที่อัดมาเป็น Required เสมอ
12. Required ถูกบังคับ Chance 100 และ Jitter 0
13. Safe Random สร้างได้เฉพาะใน Safe Zone
14. Safe Random มีตัวเลือก `none`
15. Validation ป้องกัน random ใกล้ Required obstacle
16. Pattern เดิมเปิดได้และ migrate ได้
17. Unit tests ผ่าน
18. `compileall` ผ่าน
19. README ภาษาไทยอัปเดตครบ
20. สรุปไฟล์ที่แก้และสิ่งที่ต้องทดสอบกับ MuMu จริง

---

# 18. Codex Execution Instructions

ให้ Codex ทำงานจริง ไม่ใช่เพียงอธิบายแนวทาง

ใช้คำสั่งนี้ในโฟลเดอร์โปรเจกต์:

```powershell
codex
```

จากนั้นสั่ง:

```text
Read CODEX_MUMU_PATTERN_STUDIO_SPEC.md and inspect the entire existing project.

Implement the specification directly in the repository. Do not only provide example snippets.

Work incrementally:
1. Inspect the current files and summarize the existing architecture.
2. Create a short implementation plan.
3. Implement ADB discovery and device probing first.
4. Implement the event model, safe zones, normalization, and tests.
5. Implement lightweight Pillow-based Auto Sync and tests.
6. Implement ROI selection and GUI integration.
7. Integrate recording and playback state machines.
8. Preserve and migrate existing JSON patterns.
9. Update README_TH.md and requirements.txt.
10. Run all tests and compile checks.
11. Fix failures before finishing.
12. Show the final git diff summary.

Important environment facts:
- MuMu Settings shows Android Device-1.
- The ADB port field displays 5557,16416.
- Treat 5557 and 16416 as candidate ports; probe both and verify device state.
- Do not assume port 7555.
- Keep manual ADB path and serial entry as fallback.
- Dependencies must remain limited to pynput and Pillow.
- Required obstacle events must always be deterministic.
- Random actions are allowed only inside explicitly defined safe zones.
- Auto Sync must use the top-right Pause icon and F9 must remain available as manual fallback.

Before finishing, run:
python -m unittest discover -s tests -v
python -m compileall .

List anything that cannot be verified without a real MuMu instance.
```

---

# 19. สิ่งที่ต้องทดสอบบนเครื่องจริง

Codex สามารถเขียนและทดสอบ logic ได้ แต่รายการต่อไปนี้ต้องทดลองกับ MuMu จริง:

- ADB executable path
- พอร์ตใดระหว่าง `5557` และ `16416` ที่เชื่อมต่อ instance นี้ได้
- MuMu แสดง serial รูปแบบใดหลัง `adb connect`
- ความละเอียด Android จริง
- Jump/Slide coordinates
- Screenshot latency
- Pause ROI
- Threshold ที่เหมาะสม
- เวลาระหว่าง Pause icon ปรากฏกับตัวละครเริ่มวิ่ง
- Slide duration ที่เกมรับจริง
- Persistent shell input latency
- F8/F9 global hotkey ขณะ MuMu active

เริ่มทดสอบ Auto Sync ด้วย:

```text
poll_ms = 400
threshold = 0.88
consecutive_matches = 2
timeout_seconds = 25
offset_ms = 0
```

แล้วดู similarity:

```text
หน้า Loading ควรต่ำกว่า threshold อย่างชัดเจน
หน้าด่านพร้อมควรสูงกว่า threshold ติดต่อกัน
```

ถ้าค่าทับกัน ให้เลือก ROI แคบขึ้น หรือปรับ threshold ก่อนเพิ่มความซับซ้อนของ algorithm

---

## Safety boundary

โปรเจกต์นี้ไม่ต้องมีฟีเจอร์:

- หลบ anti-cheat
- ซ่อน process
- เปลี่ยน fingerprint
- ทำ random เพื่อหลบการตรวจจับ
- อ่านหรือแก้ memory ของเกม
- inject DLL
- packet manipulation

ความสุ่มมีไว้เฉพาะ Safe Zone ที่ผู้ใช้กำหนด เพื่อสร้าง variation โดยไม่ทำลายคำสั่งหลบสิ่งกีดขวางที่จำเป็น
