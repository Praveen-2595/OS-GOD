import sys
import os
import time
import argparse
import ctypes
import threading


# Windows Command Prompt can default to a legacy code page (such as cp1252),
# which cannot print the Unicode text used by the startup UI. Configure the
# streams before any startup banner is emitted so launching never fails here.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from core.listener   import listen
from core.auto_capture import capture
from core.recognizer import recognize
from core.parser     import parse_command, _TIMER_RE
from core.router     import route_command
from core            import brain, memory
from utils.logger    import logger
from utils.feedback  import beep, speak
from utils.server    import start_server

# ─────────────────────────────────────────────────────────────────────────────
# Resource path
# ─────────────────────────────────────────────────────────────────────────────
def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative)


# ─────────────────────────────────────────────────────────────────────────────
# Terminal window control
# ─────────────────────────────────────────────────────────────────────────────
def _get_hwnd():
    return ctypes.windll.kernel32.GetConsoleWindow()

def minimize_terminal() -> None:
    hwnd = _get_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE

def restore_terminal() -> None:
    hwnd = _get_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE

def is_terminal_visible() -> bool:
    hwnd = _get_hwnd()
    if not hwnd:
        return False
    return ctypes.windll.user32.IsWindowVisible(hwnd) != 0


# ─────────────────────────────────────────────────────────────────────────────
# Expose terminal control to server
# ─────────────────────────────────────────────────────────────────────────────
import utils.server as _server_module
_server_module._minimize_terminal_fn   = minimize_terminal
_server_module._restore_terminal_fn    = restore_terminal
_server_module._is_terminal_visible_fn = is_terminal_visible


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
COOLDOWN            = 1.5
VERSION             = "1.0.0"
AUTO_MINIMIZE_DELAY = 8   # seconds before auto-minimize after startup


# ─────────────────────────────────────────────────────────────────────────────
# State tracking
# ─────────────────────────────────────────────────────────────────────────────
current_mode      = None
mode_activated_at = None


_overlay_instance = None   # set after overlay starts; used by set_mode


def set_mode(mode_name: str) -> None:
    global current_mode, mode_activated_at
    # Persist duration of the outgoing mode before switching
    if current_mode is not None and mode_activated_at is not None:
        elapsed_min = (time.time() - mode_activated_at) / 60.0
        memory.update_mode_usage(current_mode, elapsed_min)
    current_mode      = mode_name
    mode_activated_at = time.time()
    if _overlay_instance is not None:
        try:
            _overlay_instance.notify_mode_change(mode_name)
        except Exception:
            pass


def get_mode_status() -> str:
    if current_mode is None or mode_activated_at is None:
        return "No mode active yet."
    elapsed    = int(time.time() - mode_activated_at)
    mins, secs = divmod(elapsed, 60)
    return f"Active mode: [{current_mode}] — {mins}m {secs}s"


# ─────────────────────────────────────────────────────────────────────────────
# Config validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_config():
    import yaml
    config_path = resource_path("config.yaml")

    if not os.path.exists(config_path):
        print("❌ config.yaml not found!")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    modes = config.get("modes", {})

    print(f"\n{'─'*50}")
    print(f"  🖥️  OS GOD  v{VERSION}")
    print(f"{'─'*50}")
    print(f"  Loaded {len(modes)} mode(s): {', '.join(modes.keys())}")

    warnings = []
    for mode_name, mode_cfg in modes.items():
        if not mode_cfg:
            continue
        sound = mode_cfg.get("sound")
        if sound and not os.path.exists(resource_path(sound)):
            warnings.append(f"  ⚠️  [{mode_name}] sound missing: {sound}")

    if warnings:
        print("\n  Asset warnings:")
        for w in warnings:
            print(w)

    print(f"{'─'*50}\n")
    return config


# ─────────────────────────────────────────────────────────────────────────────
# Args
# ─────────────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="OS GOD")
    parser.add_argument("--text",        action="store_true", help="Start in text mode (default)")
    parser.add_argument("--voice",       action="store_true", help="Start in voice mode")
    parser.add_argument("--no-minimize", action="store_true", help="Disable auto-minimize")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Auto-minimize thread
# ─────────────────────────────────────────────────────────────────────────────
def _auto_minimize_after(seconds: int) -> None:
    import threading
    def _do():
        time.sleep(seconds)
        print(f"\n  💤 Auto-minimizing terminal in 3 seconds...")
        print(f"  Use iPad controller or type a command to restore.")
        time.sleep(3)
        minimize_terminal()
    threading.Thread(target=_do, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# stdin flush
# ─────────────────────────────────────────────────────────────────────────────
def _flush_stdin() -> None:
    """Discard any keystrokes buffered in stdin before the first prompt.

    Flask's startup banner prints from a background thread; anything the user
    types during that output would otherwise sit in the input buffer and get
    concatenated onto the next `input()` command. Drain it here. (Windows console.)
    """
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Command routing — shared by text, push-to-talk, and wake word so every input
# path uses identical logic: brain first, offline parser as fallback.
# ─────────────────────────────────────────────────────────────────────────────
def process_command(text: str) -> None:
    # ── BRAIN (LLM, online) ─────────────────────────────────────────────────
    # Try the natural-language brain first. If it's unavailable (no API key /
    # disabled) or errors, fall through to the offline parser.
    if brain.is_available():
        beep()
        if brain.think(text, route_command, set_mode_fn=set_mode, speak_fn=speak):
            return

    # ── PARSE (offline fallback) ────────────────────────────────────────────
    command, payload = parse_command(text)

    if command is None:
        print(f"❓ Didn't recognise: {text!r}")
        print("   Try: activate [mode] | list modes | help")
        return

    logger.info(f"CMD={command!r}  PAYLOAD={payload!r}")

    beep()

    # ── EXECUTE ─────────────────────────────────────────────────────────────
    if command == "activate":
        minimize_terminal()

        # FIX: timer must be extracted from the original raw `text` because
        # parse_command already stripped it from `payload`.
        # payload = clean mode key e.g. "alpha state"
        # text    = full utterance e.g. "activate alpha state for 10 min"
        timer_match = _TIMER_RE.search(text)
        if timer_match:
            minutes = int(timer_match.group(1))
            # Pass mode key and minutes together so router can handle both
            route_command("activate_timed", f"{payload}|{minutes}")
        else:
            route_command("activate", payload)

        set_mode(payload)
        restore_terminal()

    else:
        # All non-activate commands: pass payload directly
        route_command(command, payload)


# ─────────────────────────────────────────────────────────────────────────────
# Wake word ("hey jarvis") — offline, hands-free trigger
# ─────────────────────────────────────────────────────────────────────────────
def _on_wake() -> None:
    """Fired by the wake-word listener. Hands-free: beep, then auto-record with
    VAD (no SPACE) until the user stops speaking, transcribe, and route it like
    any other input. The listener has already paused its own mic stream, so
    capture() owns the device here."""
    print('\n🔔 "jarvis" — go ahead...')
    beep()
    audio = capture()           # hands-free VAD capture (up to 5s, stops on silence)
    if not audio:
        return
    text = recognize(audio)
    if not text:
        return
    logger.info(f"[wake] Input: {text!r}")
    try:
        process_command(text)
    except Exception as e:
        logger.error(f"Command crashed: {e}", exc_info=True)


def _trigger_voice(mic_lock: threading.Event) -> bool:
    """Start a dashboard voice capture while exclusively reserving the mic."""
    if mic_lock.is_set():
        logger.info("Voice trigger ignored: microphone is already in use.")
        return False
    mic_lock.set()
    try:
        _on_wake()
    finally:
        mic_lock.clear()
    return True


def _start_wake_word(config, mic_lock: threading.Event):
    """Start the offline wake-word listener if enabled in config. Fail-soft:
    returns None and logs on any problem so OS GOD keeps running without it."""
    cfg = config.get("wakeword") or {}
    if not cfg.get("enabled", False):
        logger.info("Wake word disabled in config.")
        return None

    try:
        from core.wake_word import WakeWordListener
    except Exception as e:
        logger.warning(f"Wake word unavailable ({e}).")
        return None

    listener = WakeWordListener(
        on_wake=_on_wake,
        keyword=cfg.get("keyword", "hey_jarvis"),
        sensitivity=cfg.get("sensitivity", 0.5),
        busy_event=mic_lock,   # shared lock so SPACE push-to-talk pauses detection
        noise_gate_rms=cfg.get("noise_gate_rms", 200),
    )
    listener.start()   # fails soft internally
    return listener


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown coordination
# ─────────────────────────────────────────────────────────────────────────────
_shutdown_event = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# Input loop — runs in a background thread so QApplication can own main thread
# ─────────────────────────────────────────────────────────────────────────────
def _input_loop(config: dict, text_mode: bool, mic_lock: threading.Event) -> None:
    """Voice/text command loop. Signals _shutdown_event instead of sys.exit()."""
    TEXT_MODE = text_mode

    def _exit() -> None:
        from utils.timer import on_app_exit
        on_app_exit()
        _shutdown_event.set()

    while not _shutdown_event.is_set():
        try:
            # ── INPUT ─────────────────────────────────────────────────────────
            if TEXT_MODE:
                raw = input("⌨️  Command: ").strip()
                if not raw:
                    continue
                text = raw.lower()
            else:
                mic_lock.set()
                try:
                    audio = listen()
                finally:
                    mic_lock.clear()
                if not audio:
                    continue
                text = recognize(audio)
                if not text:
                    continue

            logger.info(f"Input: {text!r}")

            # ── META COMMANDS ─────────────────────────────────────────────────
            if text in ("text mode",):
                TEXT_MODE = True
                print("⌨️  Switched to TEXT MODE")
                continue

            if text in ("voice mode",):
                TEXT_MODE = False
                print("🎤 Switched to VOICE MODE")
                continue

            if text in ("show terminal", "restore terminal"):
                restore_terminal()
                continue

            if text in ("hide terminal", "minimize terminal"):
                minimize_terminal()
                continue

            if text in ("what mode", "current mode", "status"):
                print(f"ℹ️  {get_mode_status()}")
                continue

            if text in ("list modes", "modes"):
                mode_list = list(config.get("modes", {}).keys())
                print(f"📋 Available modes ({len(mode_list)}): {', '.join(mode_list)}")
                continue

            if text in ("history", "mode history"):
                from utils.timer import print_history
                print_history()
                continue

            if text in ("cancel timer", "stop timer"):
                from utils.timer import cancel_mode_timer
                cancel_mode_timer()
                print("⏱️  Timer cancelled.")
                continue

            if text in ("help",):
                print(
                    "\n📖 Commands:\n"
                    "  activate [mode]                  — switch mode\n"
                    "  activate [mode] for X minutes    — timed mode\n"
                    "  list modes                       — show all modes\n"
                    "  what mode                        — current mode + elapsed time\n"
                    "  history                          — mode usage log\n"
                    "  cancel timer                     — cancel running timer\n"
                    "  show/hide terminal               — toggle terminal window\n"
                    "  text mode / voice mode           — switch input method\n"
                    "  exit                             — quit OS GOD\n"
                )
                continue

            if text in ("exit", "quit"):
                logger.info("Stopped by user.")
                print("👋 Shutting down OS GOD.")
                _exit()
                return

            # ── ROUTE (brain first, offline parser as fallback) ───────────────
            try:
                process_command(text)
            except Exception as e:
                logger.error(f"Command crashed: {e}", exc_info=True)
            time.sleep(COOLDOWN)

        except KeyboardInterrupt:
            print("\n👋 Shutting down OS GOD.")
            _exit()
            return

        except Exception as e:
            logger.exception(f"Main loop error: {e}")
            print(f"⚠️  Error: {e} — still running.")
            restore_terminal()
            time.sleep(1)


# ─────────────────────────────────────────────────────────────────────────────
# Main — QApplication owns main thread; input loop runs in background thread
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    args   = parse_args()
    config = validate_config()

    import uuid, signal
    brain._session_id = str(uuid.uuid4())
    logger.info(f"OS GOD v{VERSION} Started (session {brain._session_id[:8]})")

    mic_lock = threading.Event()
    start_server(route_command, voice_fn=lambda: _trigger_voice(mic_lock))

    text_mode = not args.voice
    if args.voice:
        print("🎤 Starting in VOICE MODE")
    else:
        print("⌨️  Starting in TEXT MODE")

    print("  Commands: activate [mode] | list modes | what mode | history | help\n")

    if not args.no_minimize:
        _auto_minimize_after(AUTO_MINIMIZE_DELAY)

    time.sleep(0.3)
    _flush_stdin()

    _start_wake_word(config, mic_lock)

    from utils.hotkeys import start_jarvis_hotkey
    start_jarvis_hotkey(_on_wake)

    overlay_cfg     = config.get("overlay", {}) or {}
    overlay_enabled = bool(overlay_cfg.get("enabled", True))

    # ── LIGHTWEIGHT PATH — overlay disabled (standalone dashboard is the UI) ──
    # No Qt, no QApplication, no empty event loop. Flask, wake word and hotkeys
    # are already running; we just run the input loop directly in the main thread
    # and block on it. Dashboard commands arrive via the Flask /command endpoint.
    if not overlay_enabled:
        logger.info("Overlay disabled — running lightweight (no Qt).")
        signal.signal(signal.SIGINT, lambda *_: _shutdown_event.set())
        try:
            _input_loop(config, text_mode, mic_lock)   # blocks until exit/shutdown
        except KeyboardInterrupt:
            _shutdown_event.set()
        from utils.timer import on_app_exit
        on_app_exit()
        sys.exit(0)

    # ── Start input loop in background thread (Qt owns the main thread) ────────
    input_thread = threading.Thread(
        target=_input_loop, args=(config, text_mode, mic_lock),
        name="input-loop", daemon=True
    )
    input_thread.start()

    # ── PyQt6 Command Center in main thread ──────────────────────────────────
    try:
        import queue as _queue_mod
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore    import QTimer
        from core            import overlay as _overlay_mod
        from core.overlay    import JarvisCommandCenter
        from utils.hotkeys   import start_overlay_hotkey

        # Wire brain responses → overlay feed
        brain._response_hook = _overlay_mod.add_feed_message

        qt_app  = QApplication(sys.argv)
        overlay = None
        global _overlay_instance

        try:
            overlay = JarvisCommandCenter(config, on_wake_fn=_on_wake)
            _overlay_instance = overlay
            if not overlay_cfg.get("start_minimized", False):
                overlay.show()
            start_overlay_hotkey(overlay.toggle)
            logger.info("Command Center started.")
        except Exception as e:
            logger.warning(f"Overlay failed to start ({e}) — continuing without it.")

        # Worker thread: drains overlay command queue → process_command()
        def _cmd_worker() -> None:
            cmd_q = _overlay_mod.get_command_queue()
            while not _shutdown_event.is_set():
                try:
                    text = cmd_q.get(timeout=0.5)
                except _queue_mod.Empty:
                    continue
                try:
                    process_command(text)
                except Exception as e:
                    logger.exception(f"cmd worker: {e}")

        threading.Thread(target=_cmd_worker, name="cmd-worker", daemon=True).start()

        # Ctrl+C in Qt context
        signal.signal(
            signal.SIGINT,
            lambda *_: (_shutdown_event.set(), qt_app.quit())
        )

        # Poll _shutdown_event and quit Qt when the input loop signals exit
        _watch = QTimer()
        _watch.timeout.connect(
            lambda: qt_app.quit() if _shutdown_event.is_set() else None
        )
        _watch.start(400)

        sys.exit(qt_app.exec())

    except ImportError:
        logger.warning("PyQt6 not installed — overlay disabled. Running without GUI.")
        try:
            while not _shutdown_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        from utils.timer import on_app_exit
        on_app_exit()
        sys.exit(0)


if __name__ == "__main__":
    main()
