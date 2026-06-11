"""Save-folder setting + 'Get started' busy animation.

- The App menu's "Set Save Folder…" persists the export dir via set_output_dir.
- Clicking "Get started" must animate immediately and run the (blocking)
  dependency check off the UI thread, then show the setup overlay.
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import native_app


class _FakeDepsCheckWorker(QObject):
    """Stand-in for DepsCheckWorker — records start(), never runs subprocesses."""
    done = Signal(dict)
    last = None

    def __init__(self):
        super().__init__()
        self.started = False
        _FakeDepsCheckWorker.last = self

    def start(self):
        self.started = True


_DEPS_OK = {n: {"installed": True, "version": "1.0", "path": "x"}
            for n in ("ffmpeg", "ffprobe", "yt-dlp", "deno")}


class SaveFolderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = native_app.MainWindow()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()

    def test_set_save_folder_persists_chosen_dir(self):
        with mock.patch.object(native_app.QFileDialog, "getExistingDirectory",
                               return_value=r"C:\clips"), \
             mock.patch.object(native_app, "set_output_dir") as set_dir:
            set_dir.return_value = r"C:\clips"
            self.w._set_save_folder()
        set_dir.assert_called_once_with(r"C:\clips")
        self.assertIn("C:\\clips", self.w.status.text())

    def test_set_save_folder_cancel_is_noop(self):
        with mock.patch.object(native_app.QFileDialog, "getExistingDirectory",
                               return_value=""), \
             mock.patch.object(native_app, "set_output_dir") as set_dir:
            self.w._set_save_folder()
        set_dir.assert_not_called()


class GetStartedAnimationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._real_worker = native_app.DepsCheckWorker
        native_app.DepsCheckWorker = _FakeDepsCheckWorker
        _FakeDepsCheckWorker.last = None
        self.w = native_app.MainWindow()
        self.w.show()

    def tearDown(self):
        native_app.DepsCheckWorker = self._real_worker
        self.w.close()
        self.w.deleteLater()

    def test_welcome_set_busy_toggles_button(self):
        wel = self.w.welcome
        wel.set_busy(True)
        self.assertFalse(wel.start_btn.isEnabled())
        self.assertTrue(wel.start_btn.text().startswith("Setting up"))
        wel.set_busy(False)
        self.assertTrue(wel.start_btn.isEnabled())
        self.assertEqual(wel.start_btn.text(), "Get started  →")

    def test_get_started_animates_and_defers_check(self):
        self.w._begin_onboarding()
        # Button shows the animated busy state and the check runs off-thread.
        self.assertFalse(self.w.welcome.start_btn.isEnabled())
        self.assertTrue(self.w.welcome.start_btn.text().startswith("Setting up"))
        self.assertIsNotNone(_FakeDepsCheckWorker.last)
        self.assertTrue(_FakeDepsCheckWorker.last.started)

    def test_on_deps_checked_clears_busy_and_shows_setup(self):
        self.w._begin_onboarding()
        # Simulate the worker finishing.
        self.w._on_deps_checked(_DEPS_OK)
        self.assertTrue(self.w.welcome.start_btn.isEnabled())
        self.assertEqual(self.w.welcome.start_btn.text(), "Get started  →")
        self.assertIsNotNone(self.w.deps)
        self.assertTrue(self.w.deps.isVisible())


if __name__ == "__main__":
    unittest.main()
