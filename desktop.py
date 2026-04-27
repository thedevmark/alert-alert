import os
import sys
import threading
import time
import webbrowser
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QStatusBar
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from app import DEFAULT_APP_PORT, INTERNAL_DIR, find_available_port, get_output_dir, start_server


APP_TITLE = "Alert! Alert!"
APP_HOST = "localhost"
APP_PORT = find_available_port(APP_HOST, DEFAULT_APP_PORT)
APP_URL = f"http://{APP_HOST}:{APP_PORT}"
INTERNAL_NAV_HOSTS = {APP_HOST, "", "localhost"}


def wait_for_server(timeout_seconds=60):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        connection = None
        try:
            connection = HTTPConnection(APP_HOST, APP_PORT, timeout=1.5)
            connection.request("GET", "/api/health", headers={
                "Cache-Control": "no-cache",
                "Connection": "close",
            })
            response = connection.getresponse()
            response.read()
            if 200 <= response.status < 400:
                return True
        except (OSError, HTTPException, TimeoutError, URLError):
            pass
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        QThreadSleeper.sleep(150)
    return False


class QThreadSleeper:
    @staticmethod
    def sleep(milliseconds):
        loop = QApplication.instance()
        if loop:
            loop.processEvents()
        threading.Event().wait(milliseconds / 1000)


class DesktopPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.host() not in INTERNAL_NAV_HOSTS and url.scheme() in {"http", "https"}:
            webbrowser.open(url.toString())
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class DesktopWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1100, 720)
        icon_path = INTERNAL_DIR / "static" / "favicon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.view = QWebEngineView(self)
        self.page = DesktopPage(self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)
        self._build_menubar()
        self._build_statusbar()
        self.view.page().profile().downloadRequested.connect(self.handle_download_requested)
        self.view.loadFinished.connect(self._on_loaded)

    def _build_menubar(self):
        mb = self.menuBar()

        app_menu = mb.addMenu("&App")

        deps_action = QAction("Dependency Setup", self)
        deps_action.setStatusTip("Check FFmpeg, ffprobe, and yt-dlp")
        deps_action.triggered.connect(self._open_dep_settings)
        app_menu.addAction(deps_action)

        reload_action = QAction("Reload App", self)
        reload_action.setShortcut(QKeySequence("F5"))
        reload_action.triggered.connect(self.view.reload)
        app_menu.addAction(reload_action)

        open_browser = QAction("Open in Browser", self)
        open_browser.setShortcut(QKeySequence("Ctrl+Shift+B"))
        open_browser.triggered.connect(lambda: webbrowser.open(APP_URL))
        app_menu.addAction(open_browser)

        reset_action = QAction("Reset Settings", self)
        reset_action.triggered.connect(self._reset_settings)
        app_menu.addAction(reset_action)

        app_menu.addSeparator()

        open_folder = QAction("Open Output Folder", self)
        open_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_folder.setStatusTip("Open the folder where finished alerts are saved")
        open_folder.triggered.connect(self._open_output_folder)
        app_menu.addAction(open_folder)

        app_menu.addSeparator()

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        app_menu.addAction(about_action)

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        app_menu.addAction(exit_action)

    def _open_output_folder(self):
        folder = get_output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _reset_settings(self):
        self.view.page().runJavaScript("App.resetSettings()")

    def _open_dep_settings(self):
        self.view.page().runJavaScript(
            "if (typeof App !== 'undefined' && App.openSettingsPanel) App.openSettingsPanel('dependency-settings-panel');"
        )
        self.statusBar().showMessage("Opened dependency setup.", 3000)

    def _show_about(self):
        QMessageBox.about(
            self,
            "About Alert! Alert!",
            "<b>Alert! Alert!</b><br><br>"
            "A desktop tool for trimming, cropping, and exporting<br>"
            "short alert clips quickly.<br><br>"
            "Built with Flask + PySide6.",
        )

    def _build_statusbar(self):
        status = QStatusBar(self)
        status.showMessage("Starting local app server...")
        self.setStatusBar(status)

    def load_app(self):
        self.view.setUrl(QUrl(APP_URL))

    def _on_loaded(self, ok):
        if ok:
            self.statusBar().showMessage("Ready", 3000)
        else:
            self.statusBar().showMessage("Failed to load app UI.")

    def handle_download_requested(self, download: QWebEngineDownloadRequest):
        suggested_name = download.downloadFileName() or "alert-alert-download.bin"
        ext = Path(suggested_name).suffix.lower()
        if ext in {".mp3", ".wav", ".aac", ".flac", ".ogg"}:
            file_filter = "Audio (*.mp3 *.wav *.aac *.flac *.ogg);;All Files (*.*)"
        else:
            file_filter = "Videos (*.mp4 *.mov *.mkv *.webm);;All Files (*.*)"
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Download",
            str(Path.home() / suggested_name),
            file_filter,
        )
        if not target_path:
            download.cancel()
            return
        download.setDownloadDirectory(str(Path(target_path).parent))
        download.setDownloadFileName(Path(target_path).name)
        download.accept()
        self.statusBar().showMessage(f"Downloading to {target_path}")

    def closeEvent(self, event):
        try:
            request = Request(f"{APP_URL}/api/shutdown", method="POST")
            urlopen(request, timeout=1)
        except Exception:
            pass
        event.accept()


def launch_server():
    start_server(host=APP_HOST, port=APP_PORT, open_browser=False)


def main():
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_TITLE)
    qt_app.setOrganizationName("deutschmark")

    server_thread = threading.Thread(target=launch_server, daemon=True)
    server_thread.start()

    window = DesktopWindow()
    window.show()

    if wait_for_server():
        window.load_app()
    else:
        QMessageBox.critical(
            window,
            "Startup Failed",
            "The local app server did not start in time. Check the console output for details.",
        )
        return 1

    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
