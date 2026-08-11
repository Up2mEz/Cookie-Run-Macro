import threading
import unittest
from pathlib import Path

from PIL import Image

from popup_cleanup import PopupCleanup, PopupRule, default_popup_cleanup_config


def _frame(template: Image.Image, roi: dict) -> Image.Image:
    image = Image.new("RGB", (1280, 720), "black")
    image.paste(template, (roi["x"], roi["y"]))
    return image


class PopupCleanupTests(unittest.TestCase):
    def test_bundled_templates_follow_independent_popup_rules(self):
        project_dir = Path(__file__).resolve().parents[1]
        config = default_popup_cleanup_config()
        config["poll_ms"] = 20
        with Image.open(project_dir / config["level_up"]["template_path"]) as level_source:
            level_template = level_source.convert("RGB")
        with Image.open(project_dir / config["send_life"]["template_path"]) as life_source:
            life_template = life_source.convert("RGB")
        frames = [
            _frame(level_template, config["level_up"]["roi"]),
            _frame(level_template, config["level_up"]["roi"]),
            _frame(life_template, config["send_life"]["roi"]),
            _frame(life_template, config["send_life"]["roi"]),
        ]
        taps = []
        cleanup = PopupCleanup.from_config(
            project_dir,
            lambda: frames.pop(0) if frames else Image.new("RGB", (1280, 720), "black"),
            lambda x, y: taps.append((x, y)),
            config,
            on_status=None,
        )

        result = cleanup.run(threading.Event(), 1.0)

        self.assertEqual(result.actions, ("level_up", "send_life"))
        self.assertEqual(taps, [(640, 627), (480, 452)])

    def test_independent_rules_clear_popups_and_never_confirm_life(self):
        level_template = Image.new("RGB", (40, 20), "yellow")
        life_template = Image.new("RGB", (50, 20), "red")
        level_roi = {"x": 100, "y": 100, "width": 40, "height": 20}
        life_roi = {"x": 200, "y": 100, "width": 50, "height": 20}
        frames = [
            _frame(level_template, level_roi),
            _frame(level_template, level_roi),
            _frame(life_template, life_roi),
            _frame(life_template, life_roi),
        ]
        taps = []

        def capture():
            return frames.pop(0) if frames else Image.new("RGB", (1280, 720), "black"), "test"

        cleanup = PopupCleanup(
            capture,
            lambda x, y: taps.append((x, y)),
            [
                PopupRule("level_up", level_template, level_roi, (640, 627), 0.99, 2),
                PopupRule("send_life", life_template, life_roi, (480, 452), 0.99, 2),
            ],
            poll_ms=1,
        )

        result = cleanup.run(threading.Event(), 0.2)

        self.assertEqual(result.actions, ("level_up", "send_life"))
        self.assertEqual(taps, [(640, 627), (480, 452)])
        self.assertNotIn((790, 452), taps)

    def test_send_life_can_appear_before_level_up(self):
        level_template = Image.new("RGB", (40, 20), "yellow")
        life_template = Image.new("RGB", (50, 20), "red")
        level_roi = {"x": 100, "y": 100, "width": 40, "height": 20}
        life_roi = {"x": 200, "y": 100, "width": 50, "height": 20}
        frames = [
            _frame(life_template, life_roi),
            _frame(life_template, life_roi),
            _frame(level_template, level_roi),
            _frame(level_template, level_roi),
        ]
        taps = []

        cleanup = PopupCleanup(
            lambda: frames.pop(0) if frames else Image.new("RGB", (1280, 720), "black"),
            lambda x, y: taps.append((x, y)),
            [
                PopupRule("level_up", level_template, level_roi, (640, 627), 0.99, 2),
                PopupRule("send_life", life_template, life_roi, (480, 452), 0.99, 2),
            ],
            poll_ms=1,
        )

        result = cleanup.run(threading.Event(), 0.2)

        self.assertEqual(result.actions, ("send_life", "level_up"))
        self.assertEqual(taps, [(480, 452), (640, 627)])

    def test_empty_window_does_not_tap(self):
        cleanup = PopupCleanup(
            lambda: Image.new("RGB", (1280, 720), "black"),
            lambda *_point: self.fail("popup cleanup tapped without a match"),
            [PopupRule(
                "send_life", Image.new("RGB", (40, 20), "red"),
                {"x": 200, "y": 100, "width": 40, "height": 20},
                (480, 452), 0.99, 2,
            )],
            poll_ms=1,
        )

        result = cleanup.run(threading.Event(), 0.01)

        self.assertEqual(result.actions, ())


if __name__ == "__main__":
    unittest.main()
