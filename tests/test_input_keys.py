import unittest

from input_keys import recorder_key_name


class _Key:
    def __init__(self, *, char=None, vk=None):
        self.char = char
        self.vk = vk


class InputKeyTests(unittest.TestCase):
    def test_reads_character_keys(self):
        self.assertEqual(recorder_key_name(_Key(char="J")), "j")
        self.assertEqual(recorder_key_name(_Key(char="k")), "k")

    def test_reads_mumu_virtual_keys(self):
        self.assertEqual(recorder_key_name(_Key(vk=74)), "j")
        self.assertEqual(recorder_key_name(_Key(vk=75)), "k")

    def test_ignores_other_keys(self):
        self.assertIsNone(recorder_key_name(_Key(char="x", vk=88)))


if __name__ == "__main__":
    unittest.main()
