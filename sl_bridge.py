"""
sl_bridge.py — OS God → Solo Leveling System Event Bridge
Dev 1 deliverable | Phase 1 | Week 1–2

Fires events from OS God to the SL System webhook (port 5055).
Non-blocking — NEVER slows down OS God's mode pipeline.
Falls back to a local queue if SL System isn't running yet.

EVENTS EMITTED:
  mode_started        → fired at top of _run_mode(), before any steps
  mode_ready          → fired at bottom of _run_mode(), after Step 12
  timer_started       → fired when a timed mode begins
  timer_complete      → fired when a timed mode ends naturally
  command_recognized  → fired on every valid command in route_command()
  app_exit            → fired when OS God shuts down

HOW TO USE IN router.py:
  from sl_bridge import bridge
  bridge.emit("mode_started", {"mode": mode_name})
"""

import os
import time
import json
import queue
import threading
import requests
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Config — must match sl_config.yaml
# ─────────────────────────────────────────────────────────────────────────────
SL_WEBHOOK_URL  = "http://127.0.0.1:5055/bridge/event"
EMIT_TIMEOUT    = 0.8   # seconds — never block OS God longer than this
MAX_RETRIES     = 2     # retry failed emits this many times
QUEUE_MAX_SIZE  = 100   # local fallback queue depth


# ─────────────────────────────────────────────────────────────────────────────
# Valid event types — matches SCHEMA_CONTRACT.md Section 3
# ─────────────────────────────────────────────────────────────────────────────
VALID_EVENTS = {
    "mode_started",
    "mode_ready",
    "timer_started",
    "timer_complete",
    "command_recognized",
    "app_exit",
}


# ─────────────────────────────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────────────────────────────
class SLBridge:
    """
    Singleton event bridge. Call bridge.emit() from anywhere in OS God.
    All HTTP calls are fire-and-forget in daemon threads — zero blocking.
    """

    def __init__(self):
        # Opt-in: only active when OSGOD_SL_BRIDGE is truthy. Off by default so we
        # don't spawn a worker thread or ping 127.0.0.1:5055 forever when the
        # Solo Leveling System isn't part of the user's setup.
        self._enabled       = os.environ.get("OSGOD_SL_BRIDGE", "").lower() in ("1", "true", "yes", "on")
        self._fallback_q    = queue.Queue(maxsize=QUEUE_MAX_SIZE)
        self._sl_online     = False   # set to True once first successful ping
        self._lock          = threading.Lock()

        if not self._enabled:
            logger.debug("SL Bridge disabled (set OSGOD_SL_BRIDGE=1 to enable).")
            return

        # start background worker that drains the fallback queue
        # once SL System comes online
        self._worker = threading.Thread(
            target=self._queue_worker, daemon=True
        )
        self._worker.start()

        logger.info("SL Bridge initialised — listening for OS God events.")
        print("🌉 SL Bridge: ready (SL System offline — queueing events)")

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────
    def emit(self, event: str, payload: dict | None = None) -> None:
        """
        Emit an event to the SL System. Non-blocking — returns immediately.

        Args:
            event   : one of VALID_EVENTS
            payload : dict of event data — will have 'ts' injected automatically
        """
        if not self._enabled:
            return

        if event not in VALID_EVENTS:
            logger.warning(f"SL Bridge: unknown event type '{event}' — skipping.")
            return

        packet = {
            "event":   event,
            "payload": payload or {},
            "ts":      time.time(),
        }

        # fire-and-forget in a daemon thread — OS God never waits
        t = threading.Thread(
            target=self._send,
            args=(packet,),
            daemon=True,
        )
        t.start()

    def disable(self) -> None:
        """Disable bridge entirely — useful for testing OS God in isolation."""
        self._enabled = False
        logger.info("SL Bridge disabled.")

    def enable(self) -> None:
        self._enabled = True
        logger.info("SL Bridge enabled.")

    # ─────────────────────────────────────────────────────────────────────────
    # Internal
    # ─────────────────────────────────────────────────────────────────────────
    def _send(self, packet: dict, attempt: int = 0) -> None:
        """
        Try to POST the packet. On failure, push to fallback queue.
        Retries up to MAX_RETRIES times with 0.5s backoff.
        """
        try:
            resp = requests.post(
                SL_WEBHOOK_URL,
                json=packet,
                timeout=EMIT_TIMEOUT,
            )
            if resp.status_code == 200:
                with self._lock:
                    if not self._sl_online:
                        self._sl_online = True
                        print("✅ SL Bridge: SL System is online — live events active.")
                        logger.info("SL Bridge: SL System online.")
                logger.debug(f"SL event sent: {packet['event']}")
            else:
                logger.warning(
                    f"SL Bridge: event '{packet['event']}' got HTTP {resp.status_code}"
                )
                self._queue_fallback(packet)

        except requests.exceptions.ConnectionError:
            # SL System not running yet — queue silently
            self._queue_fallback(packet)

        except requests.exceptions.Timeout:
            logger.warning(f"SL Bridge: timeout on '{packet['event']}'")
            if attempt < MAX_RETRIES:
                time.sleep(0.5)
                self._send(packet, attempt + 1)
            else:
                self._queue_fallback(packet)

        except Exception as e:
            logger.error(f"SL Bridge unexpected error: {e}")
            self._queue_fallback(packet)

    def _queue_fallback(self, packet: dict) -> None:
        """Push a failed packet to the local queue so it's not lost."""
        try:
            self._fallback_q.put_nowait(packet)
        except queue.Full:
            # drop oldest and retry — we never block OS God
            try:
                self._fallback_q.get_nowait()
                self._fallback_q.put_nowait(packet)
            except Exception:
                pass

    def _queue_worker(self) -> None:
        """
        Background thread — drains the fallback queue once SL System is online.
        Pings every 10s to detect when SL System starts up.
        """
        while True:
            try:
                # check if SL System has come online
                if not self._sl_online:
                    try:
                        r = requests.get(
                            "http://127.0.0.1:5055/ping",
                            timeout=0.5,
                        )
                        if r.status_code == 200:
                            with self._lock:
                                self._sl_online = True
                            print("✅ SL Bridge: SL System detected — draining queue.")
                            logger.info("SL Bridge: SL System came online.")
                    except Exception:
                        pass

                # drain queue if online
                if self._sl_online and not self._fallback_q.empty():
                    try:
                        packet = self._fallback_q.get_nowait()
                        self._send(packet)
                        logger.debug(
                            f"SL Bridge: replayed queued event '{packet['event']}'"
                        )
                    except queue.Empty:
                        pass

            except Exception as e:
                logger.debug(f"SL Bridge queue worker error: {e}")

            time.sleep(10)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton — import this everywhere
# ─────────────────────────────────────────────────────────────────────────────
bridge = SLBridge()
