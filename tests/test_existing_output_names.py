"""Folder saves must be able to see which output names already exist."""

import tempfile
import unittest
from pathlib import Path

from atlas_toolkit.app.bridge import Api


class ExistingOutputNamesTests(unittest.TestCase):
    def test_lists_files_and_directories_that_already_occupy_output_names(self):
        api = Api()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "page.png").write_bytes(b"old")
            (root / "notes").mkdir()

            found = api.existing_output_names(folder, ["page.png", "notes", "fresh.png", ""])

        self.assertEqual(found, ["page.png", "notes"])

    def test_missing_folder_lists_nothing(self):
        api = Api()
        with tempfile.TemporaryDirectory() as folder:
            missing = str(Path(folder) / "no-such-dir")
            self.assertEqual(api.existing_output_names(missing, ["page.png"]), [])


if __name__ == "__main__":
    unittest.main()
