from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.main import _frontend_path_within_dist


class FrontendStaticSecurityTests(unittest.TestCase):
    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            root = parent / "dist"
            root.mkdir()
            (root / "index.html").write_text("<html></html>", encoding="utf-8")
            secret = parent / "secret.txt"
            secret.write_text("secret", encoding="utf-8")
            with patch("backend.main.frontend_dist", root):
                self.assertIsNone(_frontend_path_within_dist("../secret.txt"))
                self.assertIsNone(_frontend_path_within_dist("..\\secret.txt"))

    def test_absolute_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch("backend.main.frontend_dist", root):
                self.assertIsNone(_frontend_path_within_dist(str(root.parent / "secret.txt")))

    def test_internal_file_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            app_js = assets / "app.js"
            app_js.write_text("console.log(1)", encoding="utf-8")
            with patch("backend.main.frontend_dist", root):
                self.assertEqual(_frontend_path_within_dist("assets/app.js"), app_js.resolve())
                missing = _frontend_path_within_dist("missing/app.js")
                self.assertIsNotNone(missing)
                self.assertTrue(missing.is_relative_to(root.resolve()))

    def test_missing_dist_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "no-dist"
            with patch("backend.main.frontend_dist", root):
                self.assertIsNone(_frontend_path_within_dist("index.html"))


if __name__ == "__main__":
    unittest.main()
