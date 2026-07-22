import unittest

from mumu_window import Viewport, fit_aspect_viewport, map_viewport_point, native_roi_to_screen_box
from ui.roi_selector import fixed_roi_from_center


class MuMuWindowMappingTests(unittest.TestCase):
    def test_maps_resized_window_to_1280x720(self):
        viewport = Viewport(100, 50, 740, 410)
        self.assertEqual(map_viewport_point(viewport, 420, 230), (640, 360))

    def test_outside_viewport_is_ignored(self):
        viewport = Viewport(100, 50, 740, 410)
        self.assertIsNone(map_viewport_point(viewport, 99, 230))

    def test_native_pause_roi_maps_to_scaled_window_without_cutting_edges(self):
        viewport = Viewport(100, 50, 740, 410)
        box = native_roi_to_screen_box(
            viewport, {"x": 1165, "y": 5, "width": 62, "height": 62},
        )
        self.assertEqual(box, (682, 52, 714, 84))

    def test_portrait_display_child_letterboxes_from_content_not_root(self):
        content = Viewport(0, 60, 960, 1200)
        self.assertEqual(fit_aspect_viewport(content), Viewport(0, 360, 960, 900))

    def test_pause_crop_stays_exactly_62_square_at_screen_edges(self):
        self.assertEqual(fixed_roi_from_center(1279, 719, (1280, 720), (62, 62)).box, (1218, 658, 1280, 720))
        self.assertEqual(fixed_roi_from_center(0, 0, (1280, 720), (62, 62)).box, (0, 0, 62, 62))


if __name__ == "__main__":
    unittest.main()
