"""Native Open dialogs started from the desktop shell."""

import unittest

import webview

from atlas_toolkit.app.bridge import Api


class _FakeWindow:
    def __init__(self, result):
        self.result = result
        self.dialog_type = None
        self.kwargs = None

    def create_file_dialog(self, dialog_type, **kwargs):
        self.dialog_type = dialog_type
        self.kwargs = kwargs
        return self.result


class PickSkelFileTests(unittest.TestCase):
    def test_opens_at_the_atlas_folder_filtered_to_skel(self):
        api = Api()
        window = _FakeWindow((r"C:\atlas\hero.skel",))
        api.set_window(window)

        path = api.pick_skel_file(r"C:\atlas")

        self.assertEqual(path, r"C:\atlas\hero.skel")
        self.assertEqual(window.dialog_type, webview.FileDialog.OPEN)
        self.assertEqual(window.kwargs["directory"], r"C:\atlas")
        self.assertFalse(window.kwargs["allow_multiple"])
        self.assertIn("*.skel", window.kwargs["file_types"][0])
        self.assertIn("All files", window.kwargs["file_types"][1])

    def test_cancel_returns_none(self):
        api = Api()
        api.set_window(_FakeWindow(None))
        self.assertIsNone(api.pick_skel_file(""))

    def test_non_skel_selection_returns_none(self):
        api = Api()
        api.set_window(_FakeWindow((r"C:\atlas\hero.png",)))
        self.assertIsNone(api.pick_skel_file(r"C:\atlas"))


class PickSaveFileTests(unittest.TestCase):
    def test_png_save_offers_png_filter(self):
        api = Api()
        window = _FakeWindow((r"C:\out\hero.png",))
        api.set_window(window)

        path = api.pick_save_file("hero.png", r"C:\out")

        self.assertEqual(path, r"C:\out\hero.png")
        self.assertEqual(window.dialog_type, webview.FileDialog.SAVE)
        self.assertEqual(window.kwargs["directory"], r"C:\out")
        self.assertEqual(window.kwargs["save_filename"], "hero.png")
        self.assertIn("*.png", window.kwargs["file_types"][0])
        self.assertIn("All files", window.kwargs["file_types"][1])

    def test_non_png_save_does_not_force_a_png_filter(self):
        api = Api()
        window = _FakeWindow((r"C:\out\pack.zip",))
        api.set_window(window)

        api.pick_save_file("pack.zip", r"C:\out")

        self.assertNotIn("file_types", window.kwargs)

    def test_cancel_returns_none(self):
        api = Api()
        api.set_window(_FakeWindow(None))
        self.assertIsNone(api.pick_save_file("hero.png", r"C:\out"))


if __name__ == "__main__":
    unittest.main()
