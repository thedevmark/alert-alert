import os
import sys
import threading
import time
import webbrowser
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QStatusBar
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from app import DEFAULT_APP_PORT, INTERNAL_DIR, find_available_port, get_output_dir, start_server


APP_TITLE = "deutschmark's Alert! Alert!"
# Shared auth only allows localhost desktop origins, so keep the embedded app on localhost.
APP_HOST = "localhost"
APP_PORT = find_available_port(APP_HOST, DEFAULT_APP_PORT)
APP_URL = f"http://{APP_HOST}:{APP_PORT}"
INTERNAL_NAV_HOSTS = {APP_HOST, "", "localhost", "auth.deutschmark.online", "id.twitch.tv", "passport.twitch.tv"}


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
        self.setMinimumSize(1360, 860)
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
        project_menu = mb.addMenu("&Project")

        open_editor = QAction("Open Video Editor", self)
        open_editor.setShortcut(QKeySequence("Ctrl+1"))
        open_editor.setStatusTip("Switch into the streamer workflow workspace")
        open_editor.triggered.connect(lambda: self._run_reel_action(step=1, status_message="Opened Video Editor workspace."))
        project_menu.addAction(open_editor)

        open_alerts = QAction("Open Alert Creator", self)
        open_alerts.setShortcut(QKeySequence("Ctrl+2"))
        open_alerts.setStatusTip("Switch back to Alert Creator")
        open_alerts.triggered.connect(lambda: self._switch_mode("alert"))
        project_menu.addAction(open_alerts)

        project_menu.addSeparator()

        new_project = QAction("New Project", self)
        new_project.setShortcut(QKeySequence("Ctrl+N"))
        new_project.setStatusTip("Start a fresh stream-to-shorts workflow project")
        new_project.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.startNewProject();", step=1, status_message="Starting a new workflow project.")
        )
        project_menu.addAction(new_project)

        refresh_projects = QAction("Refresh Projects", self)
        refresh_projects.setStatusTip("Reload recent workflow projects")
        refresh_projects.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.reloadRecentProjects();", step=1, status_message="Refreshing workflow projects.")
        )
        project_menu.addAction(refresh_projects)

        open_session = QAction("Open Session Workspace", self)
        open_session.setShortcut(QKeySequence("Alt+1"))
        open_session.setStatusTip("Jump to the session workspace and source ingest panel")
        open_session.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('session');", status_message="Opened Session workspace.")
        )
        project_menu.addAction(open_session)

        open_inbox_workspace = QAction("Open Inbox Workspace", self)
        open_inbox_workspace.setShortcut(QKeySequence("Alt+2"))
        open_inbox_workspace.setStatusTip("Jump to the inbox workspace")
        open_inbox_workspace.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('inbox');", status_message="Opened Inbox workspace.")
        )
        project_menu.addAction(open_inbox_workspace)

        open_inspector_workspace = QAction("Open Inspector Workspace", self)
        open_inspector_workspace.setStatusTip("Jump to the inspector workspace")
        open_inspector_workspace.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('inspector');", status_message="Opened Inspector workspace.")
        )
        project_menu.addAction(open_inspector_workspace)

        open_captions_workspace = QAction("Open Captions Workspace", self)
        open_captions_workspace.setShortcut(QKeySequence("Alt+3"))
        open_captions_workspace.setStatusTip("Jump to the captions workspace")
        open_captions_workspace.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('captions');", status_message="Opened Captions workspace.")
        )
        project_menu.addAction(open_captions_workspace)

        open_output_workspace = QAction("Open Output Workspace", self)
        open_output_workspace.setShortcut(QKeySequence("Alt+4"))
        open_output_workspace.setStatusTip("Jump to the output workspace")
        open_output_workspace.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('output');", status_message="Opened Output workspace.")
        )
        project_menu.addAction(open_output_workspace)

        project_menu.addSeparator()

        app_menu = project_menu.addMenu("App")

        deps_action = QAction("Dependency Setup", self)
        deps_action.setStatusTip("Check runtime tools and captioning packages")
        deps_action.triggered.connect(self._open_dep_settings)
        app_menu.addAction(deps_action)

        shortcuts_action = QAction("Keyboard Shortcuts", self)
        shortcuts_action.setShortcut(QKeySequence("?"))
        shortcuts_action.setStatusTip("Show keyboard shortcut reference")
        shortcuts_action.triggered.connect(self._open_shortcuts)
        app_menu.addAction(shortcuts_action)

        reload_action = QAction("Reload App", self)
        reload_action.setShortcut(QKeySequence("F5"))
        reload_action.setStatusTip("Reload the app UI")
        reload_action.triggered.connect(self.view.reload)
        app_menu.addAction(reload_action)

        open_browser = QAction("Open in Browser", self)
        open_browser.setShortcut(QKeySequence("Ctrl+Shift+B"))
        open_browser.setStatusTip("Open the app in your default web browser")
        open_browser.triggered.connect(lambda: webbrowser.open(APP_URL))
        app_menu.addAction(open_browser)

        reset_action = QAction("Reset Settings", self)
        reset_action.setStatusTip("Reset all app settings to their defaults")
        reset_action.triggered.connect(self._reset_settings)
        app_menu.addAction(reset_action)

        app_menu.addSeparator()

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        app_menu.addAction(about_action)

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.setStatusTip("Quit Alert! Alert!")
        exit_action.triggered.connect(self.close)
        app_menu.addAction(exit_action)

        ingest_menu = mb.addMenu("&Ingest")

        open_ingest = QAction("Open Session Workspace", self)
        open_ingest.setShortcut(QKeySequence("Alt+1"))
        open_ingest.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('session');", status_message="Opened Session workspace.")
        )
        ingest_menu.addAction(open_ingest)

        login_twitch = QAction("Login with Twitch", self)
        login_twitch.setStatusTip("Start the shared Twitch auth flow")
        login_twitch.triggered.connect(
            lambda: self._run_reel_action("if (typeof DmAuth !== 'undefined' && DmAuth.login) DmAuth.login();", step=1, status_message="Opening Twitch login.")
        )
        ingest_menu.addAction(login_twitch)

        sync_twitch = QAction("Use Connected Twitch", self)
        sync_twitch.setStatusTip("Apply the shared Twitch session to the current project")
        sync_twitch.triggered.connect(
            lambda: self._run_reel_action(
                "if (typeof DmAuth !== 'undefined' && DmAuth.applyToVideoEditor) DmAuth.applyToVideoEditor();",
                step=1,
                status_message="Applying connected Twitch session."
            )
        )
        ingest_menu.addAction(sync_twitch)

        ingest_menu.addSeparator()

        use_url = QAction("Use URL Source", self)
        use_url.setStatusTip("Switch source ingest to a VOD URL")
        use_url.triggered.connect(
            lambda: self._run_reel_action("ReelMaker.setSourceType('url');", step=1, status_message="Switched ingest to URL source.")
        )
        ingest_menu.addAction(use_url)

        use_local_file = QAction("Use Local File Source", self)
        use_local_file.setStatusTip("Switch source ingest to a local video file")
        use_local_file.triggered.connect(
            lambda: self._run_reel_action("ReelMaker.setSourceType('file');", step=1, status_message="Switched ingest to local file source.")
        )
        ingest_menu.addAction(use_local_file)

        browse_file = QAction("Browse Local File...", self)
        browse_file.setShortcut(QKeySequence("Ctrl+O"))
        browse_file.setStatusTip("Pick a local source video for this workflow")
        browse_file.triggered.connect(
            lambda: self._run_reel_action(
                "ReelMaker.setSourceType('file'); ReelMaker.openLocalVodPicker();",
                step=1,
                status_message="Opening local source picker."
            )
        )
        ingest_menu.addAction(browse_file)

        ingest_menu.addSeparator()

        refresh_vods = QAction("Refresh Twitch VODs", self)
        refresh_vods.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.loadConnectedTwitchVideos();", step=1, status_message="Refreshing Twitch VODs.")
        )
        ingest_menu.addAction(refresh_vods)

        load_selected_vod = QAction("Load Selected Twitch VOD", self)
        load_selected_vod.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.loadSelectedConnectedTwitchVod();", step=1, status_message="Loading selected Twitch VOD.")
        )
        ingest_menu.addAction(load_selected_vod)

        load_twitch_clips = QAction("Load Twitch Clips", self)
        load_twitch_clips.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.loadConnectedTwitchClips();", step=1, status_message="Loading Twitch clips.")
        )
        ingest_menu.addAction(load_twitch_clips)

        import_twitch_clips = QAction("Import Twitch Clips", self)
        import_twitch_clips.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.importClipsFromConnectedVod();", step=1, status_message="Importing Twitch clips into the inbox.")
        )
        ingest_menu.addAction(import_twitch_clips)

        import_twitch_markers = QAction("Import Twitch Markers", self)
        import_twitch_markers.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.importMarkersFromConnectedVod();", step=1, status_message="Importing Twitch markers into the inbox.")
        )
        ingest_menu.addAction(import_twitch_markers)

        import_marker_clips = QAction("Import Marker Clips", self)
        import_marker_clips.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.importStreamMarkers();", step=1, status_message="Importing marker-based starter clips.")
        )
        ingest_menu.addAction(import_marker_clips)

        inbox_menu = mb.addMenu("&Inbox")

        open_inbox = QAction("Open Inbox Workspace", self)
        open_inbox.setShortcut(QKeySequence("Alt+2"))
        open_inbox.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('inbox');", status_message="Opened Inbox workspace.")
        )
        inbox_menu.addAction(open_inbox)

        open_inspector = QAction("Open Inspector Workspace", self)
        open_inspector.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('inspector');", status_message="Opened Inspector workspace.")
        )
        inbox_menu.addAction(open_inspector)

        add_clip = QAction("Add Clip", self)
        add_clip.setStatusTip("Add a manual clip to the current project")
        add_clip.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.addClip();", step=2, status_message="Adding a clip to the inbox.")
        )
        inbox_menu.addAction(add_clip)

        import_moments = QAction("Import Moments", self)
        import_moments.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.importSourceMoments();", step=2, status_message="Importing source moments.")
        )
        inbox_menu.addAction(import_moments)

        detect_moments = QAction("Detect Moments", self)
        detect_moments.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.detectMoments();", step=2, status_message="Detecting source moments.")
        )
        inbox_menu.addAction(detect_moments)

        inbox_menu.addSeparator()

        clip_preview = QAction("Clip Preview", self)
        clip_preview.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.previewSource();", step=2, status_message="Opened clip preview.")
        )
        inbox_menu.addAction(clip_preview)

        project_preview = QAction("Project Preview", self)
        project_preview.triggered.connect(
            lambda: self._run_reel_action("ReelMaker.previewSequence();", step=2, status_message="Opened project preview.")
        )
        inbox_menu.addAction(project_preview)

        inbox_menu.addSeparator()

        prep_active = QAction("Prep Active Clip", self)
        prep_active.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.prepareActiveClipAsShort();", step=2, status_message="Preparing the active clip as a short.")
        )
        inbox_menu.addAction(prep_active)

        prep_all = QAction("Prep All Inbox", self)
        prep_all.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.bulkPrepareShorts('all');", step=2, status_message="Preparing all inbox clips as shorts.")
        )
        inbox_menu.addAction(prep_all)

        prep_twitch = QAction("Prep Twitch Clips", self)
        prep_twitch.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.bulkPrepareShorts('twitch_clips');", step=2, status_message="Preparing imported Twitch clips as shorts.")
        )
        inbox_menu.addAction(prep_twitch)

        shorts_menu = mb.addMenu("&Shorts")

        open_shorts = QAction("Open Captions Workspace", self)
        open_shorts.setShortcut(QKeySequence("Alt+3"))
        open_shorts.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('captions');", status_message="Opened Captions workspace.")
        )
        shorts_menu.addAction(open_shorts)

        download_and_stitch = QAction("Download & Stitch", self)
        download_and_stitch.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.downloadAllClips();", step=3, status_message="Downloading and stitching the current short stack.")
        )
        shorts_menu.addAction(download_and_stitch)

        run_captions = QAction("Run Caption Pass", self)
        run_captions.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.transcribe();", step=3, status_message="Running caption pass.")
        )
        shorts_menu.addAction(run_captions)

        shorts_menu.addSeparator()

        install_runtime = QAction("Install Caption Runtime", self)
        install_runtime.setStatusTip("Install faster-whisper and torch for captions")
        install_runtime.triggered.connect(
            lambda: self._run_reel_action(
                "if (typeof App !== 'undefined' && App.installCaptioningDeps) App.installCaptioningDeps(false);",
                step=3,
                status_message="Installing caption runtime."
            )
        )
        shorts_menu.addAction(install_runtime)

        install_speaker_labels = QAction("Install Speaker Labels Runtime", self)
        install_speaker_labels.setStatusTip("Install pyannote.audio for optional speaker labeling")
        install_speaker_labels.triggered.connect(
            lambda: self._run_reel_action(
                "if (typeof App !== 'undefined' && App.installCaptioningDeps) App.installCaptioningDeps(true);",
                step=3,
                status_message="Installing speaker-labeling runtime."
            )
        )
        shorts_menu.addAction(install_speaker_labels)

        output_menu = mb.addMenu("&Output")

        open_output = QAction("Open Output Workspace", self)
        open_output.setShortcut(QKeySequence("Alt+4"))
        open_output.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setWorkspace('output');", status_message="Opened Output workspace.")
        )
        output_menu.addAction(open_output)

        queue_all = QAction("Queue All Prepared", self)
        queue_all.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setLongformQueueForPrepared(true);", step=4, status_message="Queued all prepared shorts for longform.")
        )
        output_menu.addAction(queue_all)

        skip_all = QAction("Skip All Prepared", self)
        skip_all.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.setLongformQueueForPrepared(false);", step=4, status_message="Removed prepared shorts from the longform queue.")
        )
        output_menu.addAction(skip_all)

        build_longform = QAction("Build Longform Project", self)
        build_longform.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.createLongformVersion();", step=4, status_message="Building longform project.")
        )
        output_menu.addAction(build_longform)

        open_longform = QAction("Open Longform Project", self)
        open_longform.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.openPendingLongformProject();", step=4, status_message="Opening longform project.")
        )
        output_menu.addAction(open_longform)

        output_menu.addSeparator()

        format_menu = output_menu.addMenu("Render Format")

        for label, preset in (
            ("Shorts / Reels", "shorts"),
            ("4:5 Feed", "portrait_feed"),
            ("Square", "square"),
            ("16:9 Landscape", "landscape"),
        ):
            action = QAction(label, self)
            action.triggered.connect(
                lambda checked=False, preset_name=preset, label_text=label: self._run_reel_action(
                    f"ReelMaker.setExportFormatPreset('{preset_name}');",
                    step=4,
                    status_message=f"Set render format to {label_text}."
                )
            )
            format_menu.addAction(action)

        output_menu.addSeparator()

        render_video = QAction("Render Video", self)
        render_video.setShortcut(QKeySequence("Ctrl+Shift+R"))
        render_video.triggered.connect(
            lambda: self._run_reel_action("void ReelMaker.exportReel();", step=4, status_message="Rendering video output.")
        )
        output_menu.addAction(render_video)

        download_output = QAction("Download Export", self)
        download_output.setShortcut(QKeySequence("Ctrl+Shift+D"))
        download_output.triggered.connect(
            lambda: self._run_reel_action("ReelMaker.downloadReel();", step=4, status_message="Downloading rendered export.")
        )
        output_menu.addAction(download_output)

        output_menu.addSeparator()

        open_folder = QAction("Open Output Folder", self)
        open_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_folder.setStatusTip("Open the folder where finished exports are saved")
        open_folder.triggered.connect(self._open_output_folder)
        output_menu.addAction(open_folder)

    # ── Menu action helpers ──────────────────────────────────────────────────

    def _run_app_script(self, script, status_message=""):
        if status_message:
            self.statusBar().showMessage(status_message, 3000)
        self.view.page().runJavaScript(script)

    def _run_reel_action(self, action_script="", step=None, status_message="", delay_ms=80):
        focus_script = ""
        if step is not None:
            focus_script = f"if (typeof ReelMaker !== 'undefined' && ReelMaker.focusStep) ReelMaker.focusStep({step});"

        script = f"""
(() => {{
    if (typeof switchMode === 'function') switchMode('reel');
    const run = () => {{
        try {{
            {focus_script}
            {action_script}
        }} catch (error) {{
            console.error(error);
        }}
    }};
    window.setTimeout(run, {delay_ms});
}})();
"""
        self._run_app_script(script, status_message=status_message)

    def _open_output_folder(self):
        folder = get_output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _reset_settings(self):
        self.view.page().runJavaScript("App.resetSettings()")

    def _zoom_in(self):
        self.view.setZoomFactor(min(self.view.zoomFactor() + 0.1, 3.0))

    def _zoom_out(self):
        self.view.setZoomFactor(max(self.view.zoomFactor() - 0.1, 0.3))

    def _zoom_reset(self):
        self.view.setZoomFactor(1.0)

    def _switch_mode(self, mode):
        self._run_app_script(f"if (typeof switchMode === 'function') switchMode('{mode}')")

    def _open_dep_settings(self):
        self._run_app_script(
            "if (typeof App !== 'undefined' && App.openSettingsPanel) App.openSettingsPanel('dependency-settings-panel');",
            status_message="Opened dependency setup."
        )

    def _open_shortcuts(self):
        self._run_app_script(
            "if (typeof App !== 'undefined' && App.toggleShortcutHelp) App.toggleShortcutHelp();",
            status_message="Opened keyboard shortcuts."
        )

    def _show_about(self):
        QMessageBox.about(
            self,
            "About Alert! Alert!",
            "<b>deutschmark's Alert! Alert!</b><br><br>"
            "A desktop tool for streamers to quickly trim, crop, and export<br>"
            "short-form clips and reels from stream VODs.<br><br>"
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
        if ext in {".srt", ".ass", ".vtt", ".sub"}:
            file_filter = "Subtitles (*.srt *.ass *.vtt *.sub);;All Files (*.*)"
        elif ext in {".mp3", ".wav", ".aac", ".flac", ".ogg"}:
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
