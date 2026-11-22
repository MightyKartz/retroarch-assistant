"""Host the existing workbench in a system WebView."""
from contextlib import ExitStack
import json
import os
import sys
import time
from threading import Event, Thread

import app_paths
from app_brand import NAME
import server


def _capture_drop_urls(queue, window=None):
    """Record every Cocoa drop's native file URLs before pywebview consumes them.

    `WebKitHost.performDragOperation_` already reads the drop pasteboard; this
    wrapper snapshots the same URLs once via `filePathURL().path()`, which
    decodes percent escapes exactly once. pywebview's later DOM-file matching
    unquotes stored paths a second time, so a literal '%20' inside a filename
    would resolve to a different file — its results are ignored here.

    Each capture also stamps the pasteboard with a per-drop nonce and pushes
    {nonce, paths} to the page. The DOM drop event can read the same nonce
    from its dataTransfer, giving source and landing zone a shared, single-use
    identity that no timing window can counterfeit."""
    from webview.platforms.cocoa import BrowserView
    import AppKit
    import uuid

    klass = BrowserView.WebKitHost
    if getattr(klass, '_plcn_drop_capture', False):
        return True
    original = klass.performDragOperation_

    def wrapped(self, sender):
        pboard = sender.draggingPasteboard()
        urls = pboard.readObjectsForClasses_options_(
            [AppKit.NSURL],
            {AppKit.NSPasteboardURLReadingFileURLsOnlyKey: AppKit.NSNumber.numberWithBool_(True)}) or []
        paths = [url.filePathURL().path() for url in urls]
        # The nonce rides inside the drop itself: the DOM side reads it from
        # dataTransfer (getData or the types list), so a capture can only be
        # claimed by the very drop it arrived with.
        nonce = uuid.uuid4().hex
        try:
            pboard.setString_forType_(nonce, 'org.plcn.dropid')
            pboard.setString_forType_('1', 'org.plcn.dropid.' + nonce)
        except Exception:
            pass
        queue.append({'at': time.monotonic(), 'nonce': nonce, 'paths': paths})
        del queue[:-8]
        # Deliver asynchronously: pywebview's evaluate_js/run_js both post to
        # the main loop and then wait on a semaphore — calling either on the
        # AppKit main thread deadlocks the drag. WKWebView's own completion-
        # handler API returns immediately, so performDragOperation_ never
        # blocks the event loop.
        script = ('window.gameFilesDropSource && gameFilesDropSource(%s)'
                  % json.dumps({'nonce': nonce, 'paths': paths}, ensure_ascii=False))
        try:
            self.evaluateJavaScript_completionHandler_(script, None)
        except Exception:
            try:
                self.evaluateJavaScript_completionHandler_(script, lambda result, error: None)
            except Exception:
                pass
        return original(self, sender)

    klass.performDragOperation_ = wrapped
    klass._plcn_drop_capture = True
    return True





def run_window():
    if sys.platform not in ('darwin', 'win32'):
        raise RuntimeError('独立窗口预览仅支持 macOS 和 Windows。')

    import webview

    with ExitStack() as launch:
        httpd, url = launch.enter_context(server.local_service(activate_window=True))
        if httpd is None:
            return

        window = webview.create_window(NAME, url, width=1280, height=820, min_size=(720, 540))
        service = Thread(target=httpd.serve_forever, name='retroarch-assistant-http')
        closing = Event()

        def stop_service():
            httpd.shutdown()
            service.join()
            launch.close()

        def activate_window():
            if window.events.minimized.is_set():
                window.restore()
            window.show()

        def pick_items_darwin(initial):
            """NSOpenPanel that accepts files AND folders in one multi-select panel."""
            import AppKit
            from Foundation import NSURL
            from PyObjCTools import AppHelper
            selected, finished = [], Event()
            def run():
                panel = AppKit.NSOpenPanel.openPanel()
                panel.setTitle_(NAME + ' — 选择游戏文件或文件夹')
                panel.setCanChooseFiles_(True)
                panel.setCanChooseDirectories_(True)
                panel.setAllowsMultipleSelection_(True)
                if initial:
                    panel.setDirectoryURL_(NSURL.fileURLWithPath_(initial))
                if panel.runModal() == AppKit.NSFileHandlingPanelOKButton:
                    selected.extend(panel.filenames())
                finished.set()
            AppHelper.callAfter(run)
            finished.wait()
            return selected

        def pick_file(kind, initial):
            if kind == 'items' and sys.platform == 'darwin':
                return pick_items_darwin(initial)
            dialog_type = webview.FileDialog.FOLDER if kind == 'directory' else webview.FileDialog.OPEN
            file_types = ('RetroArch playlist (*.lpl)',) if kind == 'file' else ()
            paths = window.create_file_dialog(
                dialog_type, directory=initial, allow_multiple=kind in ('files', 'items'), file_types=file_types,
            )
            if kind in ('files', 'items'):
                return list(paths or ())
            return paths[0] if paths else ''

        def allow_close():
            if server.job_manager.prepare_shutdown():
                closing.set()
                # AppKit can terminate the process directly after accepting Cmd+Q.
                stop_service()
                return True
            Thread(target=window.run_js, args=(
                "showStatus(desktopText('仍有任务运行，请取消或等待完成后再退出', "
                "'Active tasks must finish or be cancelled before quitting.'), 'error')",
            ), daemon=True).start()
            return False

        def close_when_service_stops():
            service.join()
            if not closing.is_set() and not window.events.closed.is_set():
                window.destroy()

        def bind_finder_drops():
            """Forward Finder file drops to the page as verified native paths.

            The Cocoa drop pasteboard yields each item's own file URL (captured
            before pywebview's name matching runs); unresolved items stay None
            so the page can ask for the picker instead of writing a wrong path."""
            from webview.dom import DOMEventHandler, _dnd_state

            captured = []
            try:
                _capture_drop_urls(captured, window)
            except Exception as error:
                print('拖放路径捕获不可用：' + str(error), flush=True)

            def on_drop(event):
                # Sources arrive with the drop's own nonce via the capture's
                # async delivery; this callback only drains pywebview's state.
                # Nothing is paired by time here — a missing capture can never
                # borrow another drop's record.
                del _dnd_state['paths'][:]

            window.dom.document.events.dragover += DOMEventHandler(lambda event: event, True, True, debounce=200)
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)

        def started():
            if sys.platform == 'darwin':
                try:
                    bind_finder_drops()
                except Exception as error:
                    print('拖放绑定失败：' + str(error), flush=True)
            close_when_service_stops()

        window.events.restored += window.events.minimized.clear
        window.events.maximized += window.events.minimized.clear
        window.events.closing += allow_close
        httpd.activate_window = activate_window
        httpd.pick_file = pick_file
        service.start()
        try:
            webview.start(
                started, gui='edgechromium' if sys.platform == 'win32' else None,
                private_mode=False, storage_path=str(app_paths.cache_dir() / 'webview'),
            )
        finally:
            if not closing.is_set():
                stop_service()
