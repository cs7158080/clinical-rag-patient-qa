"""
desktop.py — Desktop launcher: runs the app in its own window, without a console.

Started by the desktop shortcut (created by setup.bat):
    .venv\\Scripts\\pythonw.exe -m app.desktop

- The window opens immediately with a loading page; the server starts in the
  background and the window switches to the app once it answers.
- Closing the window stops the server and exits the process.
- A second launch brings the existing window to the front instead of starting
  a second server (Windows named mutex).
- Startup problems are shown as Hebrew message boxes and written to
  logs/desktop.log. Developers can still use start.bat (app.main, with console).
"""

import ctypes
import logging
import os
import socket
import sys
import time
import traceback
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOT_LOG = os.path.join(ROOT_DIR, "logs", "desktop.log")
BOOT_LOG_MAX_BYTES = 1_000_000
ICON_PATH = os.path.join(ROOT_DIR, "app", "assets", "app.ico")

WINDOW_TITLE = "מערכת שאלות ותשובות — תיקי מטופלים"
MUTEX_NAME = "Local\\ClinicalRAG.Desktop"
STARTUP_TIMEOUT_S = 60

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x10000
MB_RIGHT = 0x80000
MB_RTLREADING = 0x100000

# ---------------------------------------------------------------------------
# User-facing messages (Hebrew)
# ---------------------------------------------------------------------------

MSG_ALREADY_OPEN = (
    "המערכת כבר פתוחה או בתהליך פתיחה.\n"
    "אם החלון לא מופיע תוך דקה, חפשי אותו בשורת המשימות."
)
MSG_ENV_FILE_MISSING = (
    "קובץ ההגדרות ‎.env‎ חסר בתיקיית המערכת.\n"
    "יש להריץ את setup.bat או לפנות לתמיכה."
)
MSG_CONFIG_INVALID = "יש בעיה בהגדרות המערכת:\n{error}\n\nיש לפנות לתמיכה."
MSG_PORT_BUSY = (
    "תוכנה אחרת במחשב משתמשת בפורט {port}, ולכן לא ניתן לפתוח את המערכת.\n"
    "סגרי תוכנות אחרות או הפעילי מחדש את המחשב ונסי שוב."
)
MSG_SERVER_TIMEOUT = (
    "המערכת לא הצליחה לעלות תוך {seconds} שניות.\n"
    "נסי שוב. אם הבעיה חוזרת, יש לפנות לתמיכה עם הקובץ logs\\desktop.log."
)
MSG_WINDOW_FAILED = (
    "לא ניתן לפתוח את חלון המערכת:\n{error}\n\n"
    "ייתכן שרכיב Microsoft Edge WebView2 חסר. יש לפנות לתמיכה."
)
MSG_GENERIC = (
    "אירעה שגיאה בהפעלת המערכת:\n{error}\n\n"
    "פרטים מלאים בקובץ logs\\desktop.log. יש לפנות לתמיכה."
)
MSG_PINECONE_UNAVAILABLE = (
    "החיפוש החכם אינו זמין כרגע (בעיית חיבור לאינטרנט או לשירות).\n"
    "חלק מהשאלות עשויות לקבל תשובות חלקיות.\n"
    "מומלץ לסגור ולפתוח מחדש את המערכת כשהחיבור חוזר."
)
MSG_NER_MISSING = (
    "מודל זיהוי השמות חסר (התיקייה models).\n"
    "אפשר לשאול שאלות, אבל קליטת מסמכים חדשים תיחסם עד שהמודל יועתק לתיקיית המערכת."
)

LOADING_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<style>
body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:28px;font-family:'Segoe UI',Arial,sans-serif;
  background:#F8F9FA;color:#2D3748}
h1{margin:0;font-size:28px;font-weight:700}
.spinner{width:44px;height:44px;border:4px solid #E2E8F0;border-top-color:#5B9BB5;
  border-radius:50%;animation:spin 0.9s linear infinite}
p{margin:0;font-size:18px;color:#718096}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head><body>
<h1>👤 מערכת שאלות ותשובות — תיקי מטופלים</h1>
<div class="spinner"></div>
<p>המערכת נטענת, רק רגע…</p>
</body></html>"""

logger = logging.getLogger(__name__)


class StartupError(Exception):
    """A startup failure whose message is already user-facing Hebrew text."""


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------

def redirect_std_streams(log_path: str = BOOT_LOG) -> None:
    """pythonw.exe has no console, so sys.stdout/stderr are None and any library
    that writes to them would crash. Send them to a log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    too_big = os.path.isfile(log_path) and os.path.getsize(log_path) > BOOT_LOG_MAX_BYTES
    stream = open(log_path, "w" if too_big else "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def show_message(text: str, icon: int = MB_ICONERROR) -> None:
    flags = icon | MB_SETFOREGROUND | MB_RIGHT | MB_RTLREADING
    ctypes.windll.user32.MessageBoxW(None, text, WINDOW_TITLE, flags)


def acquire_single_instance_lock(name: str = MUTEX_NAME):
    """Return (handle, already_running). The caller keeps the handle for the whole
    process lifetime; Windows releases the mutex when the process exits, even on a crash."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def focus_existing_window() -> bool:
    """Restore and bring the running app's window to the front. False if not found."""
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def wait_until_up(url: str, timeout_s: float = STARTUP_TIMEOUT_S) -> bool:
    # Bypass system proxies: a filtering proxy must not see (or block) localhost traffic.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def load_config(root_dir: str = ROOT_DIR):
    if not os.path.isfile(os.path.join(root_dir, ".env")):
        raise StartupError(MSG_ENV_FILE_MISSING)
    from app.config import get_config
    try:
        return get_config()
    except (ValueError, FileNotFoundError) as e:
        raise StartupError(MSG_CONFIG_INVALID.format(error=e)) from e


def startup_warnings(config, pinecone_ok: bool) -> list[str]:
    """Non-fatal problems: the app runs, but the user must know what is degraded."""
    warnings = []
    if not pinecone_ok:
        warnings.append(MSG_PINECONE_UNAVAILABLE)
    ner_model = os.path.join(config.models_dir, "heBERT_NER_onnx", "model.onnx")
    if config.run_mode == "production" and not os.path.isfile(ner_model):
        warnings.append(MSG_NER_MISSING)
    return warnings


def _log_failure() -> None:
    traceback.print_exc()  # logs/desktop.log under pythonw (works before logging is set up)
    logger.exception("Desktop startup failed")


def boot(window, state: dict) -> None:
    """Runs in a pywebview worker thread once the (loading) window is shown."""
    try:
        config = load_config()
        from app.logging_setup import setup_logging
        setup_logging(config)
        from app.main import SERVER_HOST, SERVER_PORT, create_app, launch_kwargs

        # We hold the single-instance mutex, so a busy port means another program.
        if port_in_use(SERVER_HOST, SERVER_PORT):
            raise StartupError(MSG_PORT_BUSY.format(port=SERVER_PORT))

        app, pinecone_ok = create_app(config)
        state["app"] = app
        app.launch(**launch_kwargs(config), prevent_thread_lock=True, inbrowser=False, quiet=True)

        url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
        if not wait_until_up(url):
            raise StartupError(MSG_SERVER_TIMEOUT.format(seconds=STARTUP_TIMEOUT_S))
        window.load_url(url)
        logger.info("Desktop window loaded %s", url)

        for warning in startup_warnings(config, pinecone_ok):
            show_message(warning, MB_ICONWARNING)
    except StartupError as e:
        _log_failure()
        show_message(str(e))
        window.destroy()
    except Exception as e:
        _log_failure()
        show_message(MSG_GENERIC.format(error=e))
        window.destroy()


def run() -> int:
    os.chdir(ROOT_DIR)  # config.yaml, .env, data/ are resolved relative to the repo
    redirect_std_streams()

    _lock, already_running = acquire_single_instance_lock()
    if already_running:
        if not focus_existing_window():
            show_message(MSG_ALREADY_OPEN, MB_ICONINFORMATION)
        return 0

    state: dict = {}
    try:
        import webview
        window = webview.create_window(
            WINDOW_TITLE, html=LOADING_HTML, width=1280, height=860, min_size=(800, 600)
        )
        webview.start(boot, (window, state), icon=ICON_PATH)  # blocks until the window closes
        return 0
    except Exception as e:
        _log_failure()
        show_message(MSG_WINDOW_FAILED.format(error=e))
        return 1
    finally:
        if "app" in state:
            state["app"].close()
            logger.info("Gradio server stopped")


if __name__ == "__main__":
    exit_code = run()
    logging.shutdown()
    # Hard exit: lingering non-daemon threads (Pinecone, gradio) must not keep a
    # hidden pythonw process alive — it would hold the mutex and block the next launch.
    os._exit(exit_code)
