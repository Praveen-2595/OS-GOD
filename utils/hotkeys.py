import threading
from pynput import keyboard
from loguru import logger
from utils.feedback import play_sound, resource_path
import os

# ─────────────────────────────────────────────────────────────────────────────
# DJ Pawnin Hotkey Controller
#
# Only active when DJ Pawnin mode is running.
# Hotkeys:
#   CTRL + 1-5   → play that track number directly
#   CTRL + →     → next track
#   CTRL + ←     → previous track
#   CTRL + 0     → stop current track
# ─────────────────────────────────────────────────────────────────────────────

class DJHotkeyController:

    def __init__(self, tracks: list[str]):
        self.tracks        = tracks           # list of file paths from config
        self.current_index = 0
        self._active       = False
        self._listener     = None
        self._lock         = threading.Lock()

        # validate tracks exist on init — warn but don't crash
        for i, t in enumerate(self.tracks):
            path = resource_path(t)
            if not os.path.exists(path):
                logger.warning(f"DJ track {i+1} not found: {path}")
                print(f"⚠️  DJ track {i+1} missing: {t}")

    # ─────────────────────────────────────────────────────────────────────────
    # Active flag — thread-safe property
    # ─────────────────────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    # ─────────────────────────────────────────────────────────────────────────
    # Start / Stop
    # ─────────────────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start listening for hotkeys. Safe to call multiple times."""
        with self._lock:
            if self._active:
                return
            self._active = True

        print("🎧 DJ Hotkeys ACTIVE")
        print("   CTRL+1~5 = play track  |  CTRL+← → = prev/next  |  CTRL+0 = stop")

        # BUG FIX: GlobalHotKeys.start() launches its own internal thread.
        # Wrapping it in another thread caused a double-thread issue.
        # Correct approach: call .run() (blocking) inside a single daemon thread.
        self._listener = keyboard.GlobalHotKeys({
            "<ctrl>+1":         lambda: self._play_index(0),
            "<ctrl>+2":         lambda: self._play_index(1),
            "<ctrl>+3":         lambda: self._play_index(2),
            "<ctrl>+4":         lambda: self._play_index(3),
            "<ctrl>+5":         lambda: self._play_index(4),
            "<ctrl>+0":         self._stop_track,
            "<ctrl>+<right>":   self._next_track,
            "<ctrl>+<left>":    self._prev_track,
        })

        t = threading.Thread(target=self._listener.run, daemon=True, name="dj-hotkey-listener")
        t.start()
        logger.info("DJ hotkey listener started.")

    def stop(self) -> None:
        """Stop the hotkey listener and stop any playing track."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            listener, self._listener = self._listener, None

        # Stop playback first, then kill the listener — both outside the lock
        # to avoid deadlocking callbacks that also try to acquire it.
        self._stop_track()

        if listener:
            try:
                listener.stop()
            except Exception as e:
                logger.debug(f"Hotkey listener stop error: {e}")

        print("🎧 DJ Hotkeys DEACTIVATED")
        logger.info("DJ hotkey listener stopped.")

    # ─────────────────────────────────────────────────────────────────────────
    # Track controls
    # ─────────────────────────────────────────────────────────────────────────
    def _play_index(self, index: int) -> None:
        if not self.active:
            return

        if index >= len(self.tracks):
            print(f"⚠️  No track {index + 1} configured — add it to config.yaml tracks list.")
            return

        track = self.tracks[index]
        path  = resource_path(track)

        if not os.path.exists(path):
            print(f"⚠️  Track {index + 1} file not found: {path}")
            return

        # Update index only after all validation passes
        with self._lock:
            self.current_index = index

        print(f"🎵 Playing track {index + 1}: {os.path.basename(track)}")
        logger.info(f"DJ playing track {index + 1}: {track}")
        play_sound(track)    # non-blocking — starts immediately, kills previous

    def _next_track(self) -> None:
        if not self.active or not self.tracks:
            return
        with self._lock:
            self.current_index = (self.current_index + 1) % len(self.tracks)
            index = self.current_index
        print(f"⏭️  Next track ({index + 1}/{len(self.tracks)})")
        self._play_index(index)

    def _prev_track(self) -> None:
        if not self.active or not self.tracks:
            return
        with self._lock:
            self.current_index = (self.current_index - 1) % len(self.tracks)
            index = self.current_index
        print(f"⏮️  Previous track ({index + 1}/{len(self.tracks)})")
        self._play_index(index)

    def _stop_track(self) -> None:
        """Stop whatever is currently playing via the shared process handle."""
        # BUG FIX: Import the module, not the name. The name `_current_process`
        # is a module-level variable that gets reassigned; importing it directly
        # captures the value at import time (None) and never sees updates.
        import utils.feedback as fb
        proc = fb._current_process
        if proc is not None:
            try:
                proc.kill()
                fb._current_process = None  # clear the reference after killing
                print("⏹️  Track stopped.")
                logger.info("DJ track stopped via hotkey.")
            except Exception as e:
                logger.debug(f"Stop track error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton — one controller instance shared across the app
# ─────────────────────────────────────────────────────────────────────────────
_dj_controller: DJHotkeyController | None = None


def start_dj_hotkeys(tracks: list[str]) -> None:
    """Call this when DJ Pawnin mode activates."""
    global _dj_controller

    # Stop previous instance if somehow still running
    if _dj_controller is not None:
        _dj_controller.stop()
        _dj_controller = None

    # BUG FIX: Create the controller before assigning to the global so that
    # a crash in __init__ doesn't leave _dj_controller pointing at a broken object.
    controller = DJHotkeyController(tracks)
    controller.start()
    _dj_controller = controller


def stop_dj_hotkeys() -> None:
    """Call this when any other mode activates."""
    global _dj_controller
    if _dj_controller is not None:
        _dj_controller.stop()
        _dj_controller = None


# ─────────────────────────────────────────────────────────────────────────────
# Alt+J — Jarvis wake hotkey
#
# Alternative to "hey jarvis" wake word. Useful when music is playing and
# openWakeWord has trouble detecting through the audio bleed. Fires the same
# on_wake callback as the wake word listener — beep + VAD capture + route.
# ─────────────────────────────────────────────────────────────────────────────

_jarvis_hotkey_listener = None


def start_jarvis_hotkey(on_wake_fn) -> None:
    """Register Alt+J as a global always-on hotkey that fires on_wake_fn.
    Fails soft — if pynput isn't installed, logs a warning and returns."""
    global _jarvis_hotkey_listener

    if _jarvis_hotkey_listener is not None:
        return  # already running

    def _on_alt_j():
        logger.info("Alt+J pressed — firing wake callback.")
        try:
            on_wake_fn()
        except Exception as e:
            logger.error(f"Jarvis hotkey callback error: {e}")

    try:
        listener = keyboard.GlobalHotKeys({"<alt>+j": _on_alt_j})
        t = threading.Thread(target=listener.run, daemon=True, name="jarvis-hotkey")
        t.start()
        _jarvis_hotkey_listener = listener
        print("⌨️  Alt+J hotkey active — press Alt+J to trigger Jarvis hands-free.")
        logger.info("Jarvis Alt+J hotkey listener started.")
    except Exception as e:
        logger.warning(f"Jarvis hotkey disabled — could not start listener: {e}")


def stop_jarvis_hotkey() -> None:
    global _jarvis_hotkey_listener
    if _jarvis_hotkey_listener is not None:
        try:
            _jarvis_hotkey_listener.stop()
        except Exception:
            pass
        _jarvis_hotkey_listener = None
        logger.info("Jarvis Alt+J hotkey listener stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# Alt+H — HUD overlay toggle
# ─────────────────────────────────────────────────────────────────────────────

_overlay_hotkey_listener = None


def start_overlay_hotkey(toggle_fn) -> None:
    """Register Alt+H as a global hotkey that calls toggle_fn (show/hide HUD).
    Fails soft — logs a warning if pynput can't register the key."""
    global _overlay_hotkey_listener

    if _overlay_hotkey_listener is not None:
        return

    def _on_alt_h():
        logger.info("Alt+H pressed — toggling overlay.")
        try:
            toggle_fn()
        except Exception as e:
            logger.error(f"Overlay hotkey callback error: {e}")

    try:
        listener = keyboard.GlobalHotKeys({"<alt>+h": _on_alt_h})
        t = threading.Thread(target=listener.run, daemon=True, name="overlay-hotkey")
        t.start()
        _overlay_hotkey_listener = listener
        print("⌨️  Alt+H hotkey active — press Alt+H to show/hide overlay.")
        logger.info("Overlay Alt+H hotkey listener started.")
    except Exception as e:
        logger.warning(f"Overlay hotkey disabled — could not start listener: {e}")


def stop_overlay_hotkey() -> None:
    global _overlay_hotkey_listener
    if _overlay_hotkey_listener is not None:
        try:
            _overlay_hotkey_listener.stop()
        except Exception:
            pass
        _overlay_hotkey_listener = None
        logger.info("Overlay Alt+H hotkey listener stopped.")