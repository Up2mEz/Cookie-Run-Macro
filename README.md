# MuMu Pattern Studio

โปรแกรม Windows สำหรับอัดและเล่นแพตเทิร์น Jump/Slide/Tap/Hold บน MuMu Player ผ่าน ADB พร้อม Auto Sync, Safe Zone, การตรวจหน้า Result และปุ่มหยุดฉุกเฉิน

เอกสารนี้เป็นคู่มือเริ่มต้นสำหรับคนที่ clone โปรเจกต์ไปใช้ครั้งแรก ส่วนคู่มือฟีเจอร์เชิงลึกและรายละเอียดการแก้ปัญหาอยู่ที่ [README_TH.md](README_TH.md)

## สิ่งที่ต้องมี

- Windows 10/11
- Python 3.10 ขึ้นไป พร้อม Tkinter
- MuMu Player ที่เปิด ADB ได้
- อินเทอร์เน็ตสำหรับติดตั้ง dependency ครั้งแรก

โปรเจกต์ติดตั้งแพ็กเกจ Python เพียง:

- `Pillow>=10.0,<12`
- `pynput>=1.7,<2`

ไม่ต้องติดตั้ง OpenCV, NumPy หรือ framework ขนาดใหญ่เพิ่มเอง โดย `adb.exe` ต้องมาจาก MuMu Player และตัวโปรแกรมจะพยายามค้นหาให้ หรือให้เลือก path เองในหน้าเริ่มต้น

## วิธีติดตั้งบนเครื่องใหม่

### วิธีแนะนำ: clone แล้วเปิดไฟล์เดียว

ติดตั้ง Git และ Python ก่อน แล้วเปิด PowerShell:

```powershell
git clone https://github.com/Up2mEz/Cookie-Run-Macro.git
cd Cookie-Run-Macro
```

จากนั้นดับเบิลคลิก `START_HERE.bat`

ครั้งแรก launcher จะ:

1. สร้าง virtual environment ในโฟลเดอร์ `.venv`
2. ติดตั้งแพ็กเกจจาก `requirements.txt`
3. เปิด `app.py`

ครั้งต่อไปให้ดับเบิลคลิก `START_HERE.bat` ได้เลย ไม่ต้องติดตั้งซ้ำ

### วิธีติดตั้งผ่าน PowerShell

```powershell
cd "โฟลเดอร์ที่ clone โปรเจกต์ไว้"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

ถ้าเครื่องมี Python Launcher แต่คำสั่ง `python` ใช้ไม่ได้:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

ถ้าเปิดไม่ได้ ให้ตรวจว่า Python ที่ใช้เป็น 3.10 ขึ้นไป:

```powershell
.\.venv\Scripts\python.exe --version
```

### การถอนหรือสร้าง environment ใหม่

`.venv` เป็นไฟล์สร้างใหม่ได้และไม่ถูกส่งขึ้น GitHub หากต้องการติดตั้งใหม่ ให้ปิดโปรแกรมก่อน แล้วลบโฟลเดอร์ `.venv` จากโฟลเดอร์โปรเจกต์ จากนั้นเปิด `START_HERE.bat` อีกครั้ง

## ตั้งค่า MuMu ครั้งแรก

1. เปิด MuMu Player และเข้า Android ให้เรียบร้อย
2. เปิด ADB ใน MuMu Settings
3. เปิดโปรแกรม แล้วไปที่แท็บ **เริ่มใช้งานง่าย**
4. กด **ค้นหา ADB และเชื่อมต่ออัตโนมัติ**
5. หากค้นหาไม่เจอ กด **หาไม่เจอ? เลือก ADB เอง** แล้วเลือก `adb.exe` ของ MuMu
6. เลือก device ที่สถานะพร้อมใช้งาน และตรวจว่า Resolution ตรงกับเกม
7. กด **ใช้ค่าจากภาพนี้** เพื่อใส่พิกัดเริ่มต้นของจอ 1280×720

ค่าเริ่มต้นของโปรเจกต์ตั้งไว้ที่ 1280×720 และพอร์ตที่พบบ่อย `5557,16416` แต่แต่ละเครื่องอาจใช้พอร์ตหรือ path ต่างกัน โปรแกรมจะบันทึกค่าที่เลือกไว้ใน `config.json` บนเครื่องนั้น

หาก MuMu อยู่คนละไดรฟ์หรือคนละโฟลเดอร์ ให้ใช้ Browse ไม่ต้องแก้โค้ด

## วิธีอัด Pattern แรก

1. เชื่อมต่อ MuMu และตรวจภาพด้วย **เปิด Preview (ไม่ใช้เล่น)** หากต้องการ
2. กด `F6` เพื่อเริ่มอัด
3. เล่นบนหน้าต่าง MuMu จริง การคลิกจะถูกบันทึกเป็น Tap/Hold และปุ่ม Jump/Slide จะถูกจำเป็น Event ตามตำแหน่งหรือ hotkey
4. รอ Auto Sync หรือกด `F9` เพื่อกำหนดจุดเริ่มช่วง `synced`
5. เล่นด่านจนถึงหน้า Result; หลังระบบเข้า `POST-GAME` ให้คลิกปุ่ม OK/เมนูต่อที่ต้องการบันทึก
6. กด `F7` เพื่อหยุดอัด
7. เลือก **สร้าง Pattern ใหม่** แล้วตั้งชื่อ จากนั้นบันทึก

Event ที่อัดใหม่เป็น `Required` โดยค่าเริ่มต้น เพื่อรักษาจังหวะจริงก่อนที่จะเพิ่มความสุ่มภายหลัง

## วิธีเล่น Pattern

1. เลือก Pattern จาก Library
2. ตรวจ device, ความละเอียด, Jump/Slide coordinates และ Pause Stage
3. ตั้งจำนวนรอบหรือเปิด Loop
4. กด **เล่น**
5. กด `F8` ได้ทุกเวลาเพื่อหยุดฉุกเฉิน

ระบบจะตรวจ Auto Sync ก่อนเล่นช่วงหลัก และจะไม่ยิง Post-game Tap หากตรวจหน้า Result ไม่สำเร็จ เพื่อป้องกันการกดผิดหน้า

## Safe Zone และความสุ่ม

ไปที่แท็บ **Random และ Safe Zone (ขั้นสูง)** แล้ว:

1. โหลด Pattern ที่ต้องการ
2. สร้าง Safe Zone ครอบช่วง Event ที่อนุญาตให้สุ่ม
3. เพิ่มหรือแก้ Event เป็น `safe_random`
4. ตรวจว่า Event อยู่ใน Zone, ไม่ชน Required event, Jitter/Slide ไม่ล้นขอบ และ weight ถูกต้อง
5. บันทึกแล้วทดสอบด้วย Pattern สำเนาก่อนใช้จริง

Safe Zone แยกเก็บใน JSON ของแต่ละ Pattern การสร้าง Pattern ใหม่จะเริ่มแบบไม่มี Safe Zone; การบันทึกทับ Pattern เดิมจะรักษา Safe Zone ของ Pattern ปลายทางไว้ รายละเอียดกฎและโหมดซ่อมอยู่ใน [README_TH.md](README_TH.md)

## Hotkeys

| ปุ่ม | การทำงาน |
| --- | --- |
| `F6` | เริ่มอัด |
| `F7` | หยุดอัด/เปิดหน้าต่างเลือกการบันทึก |
| `F8` | Emergency Stop |
| `F9` | Manual Sync |
| `J` | Required Jump ระหว่างอัด |
| `K` กดค้าง | Required Slide ระหว่างอัด |

`pynput` ใช้สำหรับ global mouse/keyboard listener ตอน MuMu เป็นหน้าต่าง active

## ไฟล์สำคัญ

- `app.py` — จุดเริ่มโปรแกรม
- `requirements.txt` — Python dependencies
- `START_HERE.bat` — ติดตั้งและเปิดโปรแกรม
- `config.json` — ค่าที่ผูกกับเครื่อง เช่น ADB path, serial และขนาดหน้าต่าง
- `patterns/*.json` — Pattern ที่บันทึก
- `templates/*.png` — Pause/Result templates
- `pause_profiles.json` — โปรไฟล์ Pause ของแต่ละ Stage
- `tests/` — unit tests

ไฟล์ `.venv`, `.json.repair.bak` และ `patterns/*_test.json` เป็นไฟล์สร้างระหว่างใช้งาน/ทดสอบและไม่ควรส่งให้เพื่อน

## ตรวจสอบการติดตั้งและโค้ด

หลังติดตั้ง dependency แล้ว ใช้คำสั่งนี้:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q .
```

การทดสอบอัตโนมัติไม่แทนการทดสอบบน MuMu จริง ควรทดลองอัด → บันทึก → โหลด → เล่นด้วยด่านจริงก่อนใช้งานต่อเนื่อง

## แก้ปัญหาเร็ว

- **Python not found:** ติดตั้ง Python 3.10+ และเลือก `Add python.exe to PATH` หรือใช้ `py -3`
- **ติดตั้ง Pillow/pynput ไม่ได้:** ตรวจอินเทอร์เน็ตและลอง `install_dependencies.bat` ใหม่
- **ไม่พบ ADB:** เปิด ADB ใน MuMu หรือ Browse ไปที่ `adb.exe`
- **ไม่พบ device:** เปิด MuMu ให้เข้า Android แล้วตรวจพอร์ต/สถานะ device
- **Resolution ไม่ตรง:** ใช้ค่าและ template ให้ตรงกับจอจริง อย่าฝืนเล่นด้วยพิกัดของ 1280×720
- **Hotkey ไม่ทำงาน:** ตรวจว่า `.venv` มี `pynput` และเปิดโปรแกรมผ่าน `START_HERE.bat`
- **โปรแกรมหยุดก่อนเล่น:** ตรวจ Pause template, ROI และ Auto Sync; ใช้ปุ่มทดสอบจริง 8 เฟรมตามคู่มือเต็ม
- **ไม่พบหน้า Result:** Capture XP ROI ใหม่จากหน้า Result จริง และเพิ่มเวลารอแทนการลด threshold มากเกินไป

## ข้อควรระวัง

โปรแกรมส่ง input ผ่าน ADB และใช้ global mouse/keyboard listener ควรใช้กับเครื่องและเกมที่ผู้ใช้มีสิทธิ์ใช้งาน และควรทดสอบ Pattern สำเนาก่อนเล่นจริง

## License

ยังไม่ได้กำหนด license สำหรับการนำไปเผยแพร่ต่อ หากต้องการให้คนอื่น fork/แก้ไข/แจกต่ออย่างชัดเจน ควรเพิ่มไฟล์ license ที่ตรงกับความตั้งใจของเจ้าของ repo
