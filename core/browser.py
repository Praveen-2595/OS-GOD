"""
core/browser.py — Voice-driven browser automation for OS GOD.

Provides browser actions (search, type, go_to_url) and YouTube Music control,
routed through the brain's browser_action and music_control tools. Uses
pyautogui for keyboard input and pygetwindow for window focus.

Always fails soft — if the target window or pyautogui/pygetwindow are missing,
it logs the error, speaks a short message, and returns without crashing OS GOD.
"""

import time
from loguru import logger

# Ordered by preference. Substring-matched against open window titles.
_BROWSER_TITLES = ["Opera GX", "Opera", "Google Chrome", "Chrome", "Microsoft Edge", "Firefox"]

# Short pause after focusing a window so the OS finishes the switch
_FOCUS_SETTLE_S = 0.35

_YTM_HOST = "music.youtube.com"

def _is_ytm(text: str) -> bool:
    return _YTM_HOST in text.lower()


def _schedule_autoplay(delay: float = 3.0) -> None:
    """Start a daemon thread that presses SPACE in the YTM window after `delay` s.
    Falls back to any open browser window if the YTM title isn't set yet."""
    import threading

    def _play() -> None:
        time.sleep(delay)
        pag = _require_pyautogui()
        if pag is None:
            return
        # By now the page should have the 'YouTube Music' window title
        if _focus_youtube_music() is None:
            if not _focus_browser():
                logger.debug("browser: autoplay — no YTM/browser window after delay")
                return
        pag.press("space")
        logger.debug(f"browser: autoplay SPACE sent (delay={delay}s)")

    threading.Thread(target=_play, daemon=True).start()


def _get_gw():
    try:
        import pygetwindow as gw
        return gw
    except ImportError:
        logger.warning("browser: pygetwindow not installed — run: pip install pygetwindow")
        return None


def _focus_browser() -> bool:
    """Bring the first matching browser window to the foreground.
    Returns True on success, False if no browser is found."""
    gw = _get_gw()
    if gw is None:
        return False

    for title_fragment in _BROWSER_TITLES:
        try:
            windows = gw.getWindowsWithTitle(title_fragment)
        except Exception:
            continue
        if windows:
            try:
                win = windows[0]
                if win.isMinimized:
                    win.restore()
                    time.sleep(0.2)
                win.activate()
                time.sleep(_FOCUS_SETTLE_S)
                logger.debug(f"browser: focused '{win.title}'")
                return True
            except Exception as e:
                logger.debug(f"browser: activate failed for '{title_fragment}': {e}")
                continue

    logger.info("browser: no open browser window found")
    return False


def _focus_youtube_music():
    """Focus the YouTube Music window and return the previously active window
    (so the caller can restore focus afterwards). Returns None on failure."""
    gw = _get_gw()
    if gw is None:
        return None

    try:
        prev = gw.getActiveWindow()
    except Exception:
        prev = None

    try:
        windows = gw.getWindowsWithTitle("YouTube Music")
    except Exception:
        windows = []

    if not windows:
        return None

    try:
        win = windows[0]
        if win.isMinimized:
            win.restore()
            time.sleep(0.15)
        win.activate()
        time.sleep(0.2)
        logger.debug(f"browser: focused YouTube Music — '{win.title}'")
        return prev
    except Exception as e:
        logger.debug(f"browser: could not focus YouTube Music: {e}")
        return None


def _require_pyautogui():
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        logger.warning("browser: pyautogui not installed — run: pip install pyautogui")
        return None


def _no_browser_msg() -> str:
    try:
        from utils.feedback import speak
        speak("No browser window found")
    except Exception:
        pass
    return "No browser window found."


# ─────────────────────────────────────────────────────────────────────────────
# Public actions
# ─────────────────────────────────────────────────────────────────────────────

def search_in_browser(query: str) -> str:
    """Open a new tab and search for `query`. Returns a status string."""
    pag = _require_pyautogui()
    if pag is None:
        return "pyautogui not installed."
    if not _focus_browser():
        return _no_browser_msg()
    try:
        pag.hotkey("ctrl", "t")          # new tab
        time.sleep(0.5)
        pag.write(query, interval=0.04)  # type the query into the address bar
        pag.press("enter")
        logger.info(f"browser: searched for {query!r}")
        if _is_ytm(query):
            _schedule_autoplay()
        return f"Searching for: {query}"
    except Exception as e:
        logger.error(f"browser: search_in_browser error: {e}")
        return f"Browser action failed: {e}"


def type_in_browser(text: str) -> str:
    """Type `text` into whichever element currently has focus in the browser.
    Does NOT press Enter — user decides when to submit."""
    pag = _require_pyautogui()
    if pag is None:
        return "pyautogui not installed."
    if not _focus_browser():
        return _no_browser_msg()
    try:
        pag.write(text, interval=0.05)
        logger.info(f"browser: typed {len(text)} chars")
        return f"Typed: {text}"
    except Exception as e:
        logger.error(f"browser: type_in_browser error: {e}")
        return f"Browser action failed: {e}"


def go_to_url(url: str) -> str:
    """Navigate the active browser tab to `url`."""
    pag = _require_pyautogui()
    if pag is None:
        return "pyautogui not installed."
    if not _focus_browser():
        return _no_browser_msg()
    try:
        pag.hotkey("ctrl", "l")          # focus address bar
        time.sleep(0.3)
        pag.write(url, interval=0.03)
        pag.press("enter")
        logger.info(f"browser: navigating to {url!r}")
        if _is_ytm(url):
            _schedule_autoplay()
        return f"Navigating to: {url}"
    except Exception as e:
        logger.error(f"browser: go_to_url error: {e}")
        return f"Browser action failed: {e}"


def open_and_play(url: str, delay: float = 3.0) -> str:
    """Navigate to `url` and press SPACE after `delay` seconds to start playback.
    Distinct from go_to_url so the brain can call it with an explicit play intent
    and a custom delay without triggering the passive YTM autoplay twice."""
    pag = _require_pyautogui()
    if pag is None:
        return "pyautogui not installed."
    if not _focus_browser():
        return _no_browser_msg()
    try:
        pag.hotkey("ctrl", "l")
        time.sleep(0.3)
        pag.write(url, interval=0.03)
        pag.press("enter")
        logger.info(f"browser: open_and_play navigating to {url!r}")
    except Exception as e:
        logger.error(f"browser: open_and_play error: {e}")
        return f"Browser action failed: {e}"
    _schedule_autoplay(delay)
    return f"Opening {url} — autoplay in {int(delay)}s"


# YouTube Music keyboard shortcuts:
#   K          → play / pause toggle
#   Shift+N    → next track
#   Shift+P    → previous track
#   Up / Down  → volume +/- (press ×3 for a noticeable step)
#   S          → toggle shuffle
_MUSIC_ACTIONS: dict[str, tuple] = {
    "play":        ("press",  "k"),
    "pause":       ("press",  "k"),
    "next":        ("hotkey", "shift", "n"),
    "previous":    ("hotkey", "shift", "p"),
    "volume_up":   ("repeat", "up",   3),
    "volume_down": ("repeat", "down", 3),
    "shuffle":     ("press",  "s"),
}

_MUSIC_LABELS: dict[str, str] = {
    "play": "Playing", "pause": "Paused",
    "next": "Next track", "previous": "Previous track",
    "volume_up": "Volume up", "volume_down": "Volume down",
    "shuffle": "Shuffle toggled",
}


def music_control(action: str) -> str:
    """Send a playback shortcut to the YouTube Music browser window.
    Refocuses the previously active window afterwards so the user doesn't
    lose their place. Fails soft with a spoken message if YTM isn't open."""
    pag = _require_pyautogui()
    if pag is None:
        return "pyautogui not installed."

    if action not in _MUSIC_ACTIONS:
        return f"Unknown music action: {action!r}"

    prev = _focus_youtube_music()
    if prev is None:
        try:
            from utils.feedback import speak
            speak("YouTube Music not found, sir")
        except Exception:
            pass
        logger.info("browser: music_control — YouTube Music window not found")
        return "YouTube Music not found."

    try:
        cmd = _MUSIC_ACTIONS[action]
        if cmd[0] == "press":
            pag.press(cmd[1])
        elif cmd[0] == "hotkey":
            pag.hotkey(*cmd[1:])
        elif cmd[0] == "repeat":
            for _ in range(cmd[2]):
                pag.press(cmd[1])
                time.sleep(0.05)

        logger.info(f"browser: music_control action={action!r}")
    except Exception as e:
        logger.error(f"browser: music_control error: {e}")
        return f"Music control failed: {e}"
    finally:
        # Always try to restore the previous window, even on error
        time.sleep(0.1)
        try:
            if prev is not None:
                prev.activate()
        except Exception:
            pass

    return _MUSIC_LABELS.get(action, action)
