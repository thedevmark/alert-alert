"""Onboarding add-URL must show a busy state on the *visible* EmptyScreen.

Regression test for: during onboarding (and any empty-queue add screen), the
EmptyScreen overlay sits on top of the main editor. Clicking its "Add URL"
button started a background download but the busy feedback (button -> "Adding…",
status -> "Downloading…") was applied to the main editor's widgets *behind* the
overlay — so the visible screen looked frozen even though the download ran.
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import native_app


class _FakeDownloadWorker(QObject):
    """Stand-in for DownloadWorker — never touches the network. The test drives
    its signals by hand to simulate progress/failure."""
    progress = Signal(str)
    finished_ok = Signal(str, str)  # (path, title)
    failed = Signal(str)

    last = None

    def __init__(self, url):
        super().__init__()
        self.url = url
        _FakeDownloadWorker.last = self

    def start(self):
        pass


class OnboardingAddBusyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._real_worker = native_app.DownloadWorker
        native_app.DownloadWorker = _FakeDownloadWorker
        _FakeDownloadWorker.last = None
        self.w = native_app.MainWindow()
        self.w.show()
        self.w._show_add()  # the EmptyScreen add surface (onboarding / empty queue)

    def tearDown(self):
        native_app.DownloadWorker = self._real_worker
        self.w.close()
        self.w.deleteLater()

    def test_empty_screen_reflects_busy_while_adding(self):
        self.assertTrue(self.w.empty.isVisible())
        self.w._submit_url(native_app.SAMPLE_URL)
        # The button the user actually clicked (on the overlay) must show
        # progress — not the hidden main-editor button behind it.
        self.assertEqual(self.w.empty.b_url.text(), "Adding…")
        self.assertFalse(self.w.empty.b_url.isEnabled())
        self.assertTrue(self.w.empty.status_lbl.isVisible())

    def test_failure_restores_empty_screen_and_shows_error(self):
        self.w._submit_url(native_app.SAMPLE_URL)
        _FakeDownloadWorker.last.failed.emit("boom")
        self.assertEqual(self.w.empty.b_url.text(), "Add URL")
        self.assertTrue(self.w.empty.b_url.isEnabled())
        self.assertIn("boom", self.w.empty.status_lbl.text())


if __name__ == "__main__":
    unittest.main()
