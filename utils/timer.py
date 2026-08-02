"""
utils/timer.py — Mode Timer + History Log

Features:
- "activate alpha for 90 minutes" — auto switches to zen after timer
- 10 minute warning spoken before timer ends
- Every mode logged to CSV with start time, end time, duration
- History readable any time with get_history()
"""

import threading
import time
import csv
import os
from datetime import datetime
from loguru import logger
from sl_bridge import bridge as _bridge
# ─────────────────────────────────────────────────────────────────────────────
# History log — CSV file in project root
# ─────────────────────────────────────────────────────────────────────────────
_LOG_PATH = os.path.join(os.path.abspath("."), "mode_history.csv")
_CSV_HEADERS = ["date", "mode", "start_time", "end_time", "duration_minutes"]

def _ensure_csv() -> None:
    if not os.path.exists(_LOG_PATH):
        with open(_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADERS)

_ensure_csv()


# ─────────────────────────────────────────────────────────────────────────────
# Internal state
# ─────────────────────────────────────────────────────────────────────────────
_current_mode:       str | None = None
_mode_start_time:    float | None = None
_timer_thread:       threading.Thread | None = None
_timer_cancel_event: threading.Event = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# History log
# ─────────────────────────────────────────────────────────────────────────────
def _log_mode_end(mode: str, start: float, end: float) -> None:
    """Write a completed mode session to the CSV log."""
    try:
        duration_mins = round((end - start) / 60, 2)
        start_dt      = datetime.fromtimestamp(start)
        end_dt        = datetime.fromtimestamp(end)

        with open(_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                start_dt.strftime("%Y-%m-%d"),
                mode,
                start_dt.strftime("%H:%M:%S"),
                end_dt.strftime("%H:%M:%S"),
                duration_mins,
            ])
        logger.info(f"Logged mode: {mode} — {duration_mins} min")
    except Exception as e:
        logger.error(f"History log error: {e}")


def get_history(last_n: int = 10) -> list[dict]:
    """Return last N mode sessions as list of dicts."""
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows[-last_n:]
    except Exception as e:
        logger.error(f"History read error: {e}")
        return []


def print_history(last_n: int = 10) -> None:
    """Print a formatted history table to terminal."""
    rows = get_history(last_n)
    if not rows:
        print("📋 No mode history yet.")
        return
    print(f"\n{'─'*55}")
    print(f"  📋 Mode History (last {last_n})")
    print(f"{'─'*55}")
    print(f"  {'DATE':<12} {'MODE':<18} {'START':<10} {'MINS':>6}")
    print(f"{'─'*55}")
    for row in rows:
        print(f"  {row['date']:<12} {row['mode']:<18} {row['start_time']:<10} {row['duration_minutes']:>6}")
    print(f"{'─'*55}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Mode tracking — call on every mode switch
# ─────────────────────────────────────────────────────────────────────────────
def on_mode_start(mode_name: str) -> None:
    """
    Call this when a new mode activates.
    Logs the previous mode's duration and resets the timer.
    """
    global _current_mode, _mode_start_time

    now = time.time()

    # log previous mode if one was running
    if _current_mode and _mode_start_time:
        _log_mode_end(_current_mode, _mode_start_time, now)

    _current_mode    = mode_name
    _mode_start_time = now
    logger.info(f"Mode timer started: {mode_name}")


def on_app_exit() -> None:
    """Call this when OS GOD shuts down to log the final mode."""
    if _current_mode and _mode_start_time:
        _log_mode_end(_current_mode, _mode_start_time, time.time())


# ─────────────────────────────────────────────────────────────────────────────
# Timed mode — "activate alpha for 90 minutes"
# ─────────────────────────────────────────────────────────────────────────────
def start_mode_timer(
    minutes: int,
    current_mode: str,
    fallback_mode: str,
    activate_fn,       # route_command from router
    speak_fn,          # speak from feedback
) -> None:
    """
    Start a countdown timer.
    - Warns at 10 minutes remaining
    - Switches to fallback_mode when timer ends
    - Cancels any previous timer
    """
    global _timer_thread, _timer_cancel_event

    # cancel any existing timer
    cancel_mode_timer()
    _timer_cancel_event = threading.Event()

    def _timer_worker():
        total_seconds    = minutes * 60
        warn_at          = 10 * 60   # warn 10 mins before end
        warned           = False

        print(f"⏱️  Mode timer: {minutes} minutes — will switch to [{fallback_mode}]")
        logger.info(f"Timer started: {minutes}min in {current_mode} → {fallback_mode}")

        start = time.time()

        while True:
            elapsed   = time.time() - start
            remaining = total_seconds - elapsed

            if _timer_cancel_event.is_set():
                logger.info("Mode timer cancelled.")
                print("⏱️  Timer cancelled.")
                return

            # 10 minute warning
            if not warned and remaining <= warn_at:
                warned = True
                mins_left = int(remaining / 60)
                speak_fn(f"{mins_left} minutes remaining in {current_mode}")
                print(f"⏱️  {mins_left} minutes remaining.")

            # timer done
            if remaining <= 0:
                speak_fn(f"{current_mode} timer complete. Switching to {fallback_mode}.")

                _bridge.emit("timer_complete", {
                    "mode":     current_mode,
                    "fallback": fallback_mode,
                })
                
                print(f"⏱️  Timer done — activating {fallback_mode}")
                logger.info(f"Timer complete — switching to {fallback_mode}")
                activate_fn("activate", fallback_mode)
                return

            time.sleep(5)   # check every 5 seconds

    _timer_thread = threading.Thread(target=_timer_worker, daemon=True)
    _timer_thread.start()


def cancel_mode_timer() -> None:
    """Cancel any running timer. Called automatically on mode switch."""
    global _timer_cancel_event
    if _timer_cancel_event:
        _timer_cancel_event.set()


def get_timer_status() -> str:
    """Return human-readable timer status."""
    if _timer_thread and _timer_thread.is_alive():
        return "Timer running"
    return "No timer active"