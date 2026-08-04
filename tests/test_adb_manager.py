import unittest
import struct

from adb_manager import ADBManager, ADBError, DeviceInfo, parse_adb_devices, parse_wm_size


class ParseAdbDevicesTests(unittest.TestCase):
    def test_excludes_offline_and_unauthorized(self):
        output = """List of devices attached
127.0.0.1:5557 device product:emu model:Android_Device_1
127.0.0.1:16416 offline
emulator-5554 unauthorized usb:1-1
"""
        self.assertEqual(parse_adb_devices(output), ["127.0.0.1:5557"])

    def test_empty_output(self):
        self.assertEqual(parse_adb_devices("List of devices attached\n\n"), [])

    def test_ignores_daemon_messages_and_removes_duplicates(self):
        output = """* daemon not running; starting now at tcp:5037
* daemon started successfully
List of devices attached
127.0.0.1:5557 device
127.0.0.1:5557 device product:x
"""
        self.assertEqual(parse_adb_devices(output), ["127.0.0.1:5557"])

    def test_multiple_ready_devices(self):
        output = "List of devices attached\na device model:A\nb device model:B\n"
        self.assertEqual(parse_adb_devices(output), ["a", "b"])

    def test_override_wm_size_wins(self):
        self.assertEqual(parse_wm_size("Physical size: 1600x900\nOverride size: 1280x720"), (1280, 720))

    def test_discover_devices_limits_results_to_requested_port(self):
        manager = ADBManager("adb.exe")
        connected_ports = []
        manager.connect_candidate_ports = lambda ports: connected_ports.extend(ports)
        manager.devices_output = lambda: (
            "List of devices attached\n"
            "127.0.0.1:5557 device model:Other\n"
            "127.0.0.1:16416 device model:Target\n"
        )
        manager.probe_device = lambda serial, _output=None: DeviceInfo(
            serial, "device", "MuMu", "", True, 1280, 720
        )

        devices = manager.discover_devices([16416])

        self.assertEqual(connected_ports, [16416])
        self.assertEqual([device.serial for device in devices], ["127.0.0.1:16416"])

    def test_discover_devices_can_lock_to_saved_serial(self):
        manager = ADBManager("adb.exe")
        manager.connect_candidate_ports = lambda _ports: None
        manager.devices_output = lambda: (
            "List of devices attached\n"
            "127.0.0.1:5557 device model:Other\n"
            "127.0.0.1:16416 device model:Target\n"
        )
        manager.probe_device = lambda serial, _output=None: DeviceInfo(
            serial, "device", "MuMu", "", True, 1280, 720
        )

        devices = manager.discover_devices([5557], target_serial="127.0.0.1:16416")

        self.assertEqual([device.serial for device in devices], ["127.0.0.1:16416"])

    def test_decode_raw_screencap_with_16_byte_header(self):
        pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255))
        image = ADBManager.decode_raw_screencap(struct.pack("<IIII", 2, 1, 1, 1) + pixels)
        self.assertEqual(image.size, (2, 1))
        self.assertEqual(list(image.getdata()), [(255, 0, 0), (0, 255, 0)])

    def test_decode_raw_screencap_rejects_short_payload(self):
        with self.assertRaises(ADBError):
            ADBManager.decode_raw_screencap(struct.pack("<III", 1280, 720, 1))


if __name__ == "__main__":
    unittest.main()
