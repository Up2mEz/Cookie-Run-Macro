import unittest
from unittest.mock import patch

from ui.recording_save_dialog import RecordingSaveDialog


class _Window:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class RecordingSaveDialogTests(unittest.TestCase):
    def test_discard_requires_confirmation_and_returns_explicit_decision(self):
        dialog = object.__new__(RecordingSaveDialog)
        dialog.result = None
        dialog.window = _Window()

        with patch("ui.recording_save_dialog.messagebox.askyesno", return_value=False):
            dialog._discard()
        self.assertIsNone(dialog.result)
        self.assertFalse(dialog.window.destroyed)

        with patch("ui.recording_save_dialog.messagebox.askyesno", return_value=True):
            dialog._discard()
        self.assertEqual(dialog.result, ("discard", ""))
        self.assertTrue(dialog.window.destroyed)


if __name__ == "__main__":
    unittest.main()
