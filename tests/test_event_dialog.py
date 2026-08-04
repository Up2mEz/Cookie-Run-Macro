import unittest

from ui.event_dialog import wheel_scroll_units


class EventDialogScrollTests(unittest.TestCase):
    def test_standard_mouse_wheel_scrolls_both_directions(self):
        self.assertEqual(wheel_scroll_units(120), -1)
        self.assertEqual(wheel_scroll_units(-120), 1)
        self.assertEqual(wheel_scroll_units(240), -2)

    def test_high_resolution_touchpad_delta_still_scrolls(self):
        self.assertEqual(wheel_scroll_units(1), -1)
        self.assertEqual(wheel_scroll_units(-1), 1)
        self.assertEqual(wheel_scroll_units(0), 0)


if __name__ == "__main__":
    unittest.main()
