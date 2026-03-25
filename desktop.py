import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QStatusBar, QToolBar
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from app import INTERNAL_DIR, find_available_port, start_server


APP_TITLE = "deutschmark's Alert! Alert!"
APP_HOST = "127.0.0.1"
APP_PORT = find_available_port(APP_HOST, 5000)
APP_URL = f"http://{APP_HOST}:{APP_PORT}"


def wait_for_server(timeout_seconds=25):
    health_url = f"{APP_URL}/api/health"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            request = Request(health_url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except URLError:
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
        if url.host() not in {APP_HOST, "", "localhost"} and url.scheme() in {"http", "https"}:
            webbrowser.open(url.toString())
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class DesktopWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1360, 860)
        icon_path = INTERNAL_DIR / "static" / "favicon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.view = QWebEngineView(self)
        self.page = DesktopPage(self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)
        self._build_toolbar()
        self._build_statusbar()
        self.view.page().profile().downloadRequested.connect(self.handle_download_requested)
        self.view.loadFinished.connect(self._on_loaded)

    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        back_action = QAction("Back", self)
        back_action.triggered.connect(self.view.back)
        toolbar.addAction(back_action)

        forward_action = QAction("Forward", self)
        forward_action.triggered.connect(self.view.forward)
        toolbar.addAction(forward_action)

        reload_action = QAction("Reload", self)
        reload_action.triggered.connect(self.view.reload)
        toolbar.addAction(reload_action)

        open_browser_action = QAction("Open in Browser", self)
        open_browser_action.triggered.connect(lambda: webbrowser.open(APP_URL))
        toolbar.addAction(open_browser_action)

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
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Download",
            str(Path.home() / suggested_name),
            "Videos (*.mp4 *.mov *.mkv *.webm);;All Files (*.*)",
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
