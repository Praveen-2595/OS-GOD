"""
core/wake_word.py — Offline wake-word detection for OS GOD ("hey jarvis").

Replaces pvporcupine (which required a Picovoice access key) with openWakeWord:
fully offline, open source, no API key, no per-keyword training. Uses the
bundled "hey_jarvis" pretrained model.

Design
------
- Runs in a daemon thread, continuously reading the mic via sounddevice and
  scoring 80 ms frames with openWakeWord.
- On a score >= sensitivity it fires the `on_wake` callback.
- Coordinates with SPACE push-to-talk through a shared `threading.Event`
  ("busy"): while the mic is reserved for a push-to-talk capture, detection
  pauses and the input stream is stopped so the two don't fight over the
  device or double-trigger.
- Fails soft: if openWakeWord or the model can't load, it logs a warning,
  stays disabled, and never raises — the rest of OS GOD keeps working.

Config (config.yaml)
--------------------
    wakeword:
      enabled: true
      keyword: hey_jarvis
      sensitivity: 0.5
      noise_gate_rms: 200   # frames quieter than this RMS are zeroed before scoring
"""

from __future__ import annotations

import time
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from loguru import logger

# Reuse the listener's mic selection + sample rate so wake word and push-to-talk
# always capture from the same device.
from core.listener import _get_device, SAMPLE_RATE

# openWakeWord operates on 16 kHz mono int16 audio. 1280 samples = 80 ms is the
# engine's recommended frame size (lowest latency, best-tested path).
FRAME_SAMPLES        = 1280
DEFAULT_KEYWORD      = "hey_jarvis"
DEFAULT_SENSITIVITY  = 0.5
# After a detection, keep the mic reserved this long so a single "hey jarvis"
# doesn't re-fire while the score decays.
_REFRACTORY_SEC      = 1.5
# Self-healing reopen: if the mic stream keeps failing to (re)open or read, back
# off between tries and give up after this many consecutive failures rather than
# spinning forever.
_MAX_STREAM_FAILURES    = 10
_REOPEN_BACKOFF_MS      = 200
# Watchdog: checks thread liveness every N seconds, restarts on death, gives up
# after this many consecutive restarts (prevents thrashing on broken hardware).
_WATCHDOG_INTERVAL_S    = 5
_WATCHDOG_BACKOFF_S     = 2
_MAX_WATCHDOG_RESTARTS  = 5
# Heartbeat: log "Wake word active" at this interval so background health is visible.
_HEARTBEAT_INTERVAL_S   = 60


class WakeWordListener:
    """Always-on, offline wake-word detector. See module docstring."""

    def __init__(
        self,
        on_wake: Callable[[], None],
        keyword: str = DEFAULT_KEYWORD,
        sensitivity: float = DEFAULT_SENSITIVITY,
        busy_event: Optional[threading.Event] = None,
        noise_gate_rms: int = 200,
    ) -> None:
        self.on_wake        = on_wake
        self.keyword        = keyword or DEFAULT_KEYWORD
        self.sensitivity    = float(sensitivity)
        self.noise_gate_rms = int(noise_gate_rms)
        # Shared with the push-to-talk path: when set, the mic is reserved and
        # detection must pause. Created here if the caller doesn't supply one.
        self.busy        = busy_event if busy_event is not None else threading.Event()
        self.enabled     = False
        self._model      = None
        self._stop       = threading.Event()
        self._thread:          Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    # ── model loading (fail-soft) ──────────────────────────────────────────────
    def _load_model(self) -> bool:
        try:
            import openwakeword
            from openwakeword.model import Model
        except Exception as e:
            logger.warning(
                f"Wake word disabled — openwakeword not installed ({e}). "
                "Run: pip install openwakeword"
            )
            return False

        # Ensure the pretrained + feature models are present. Cached inside the
        # package, so this is a one-time download and idempotent thereafter.
        try:
            openwakeword.utils.download_models([self.keyword])
        except Exception as e:
            logger.debug(f"Wake word: model download skipped/failed ({e}).")

        try:
            # ONNX backend: onnxruntime ships with the project (pulled in by
            # openwakeword). tflite-runtime is NOT installed, so never use it.
            self._model = Model(
                wakeword_models=[self.keyword],
                inference_framework="onnx",
            )
        except Exception as e:
            logger.warning(
                f"Wake word disabled — could not load model '{self.keyword}' ({e}). "
                "Try: python -c \"import openwakeword; openwakeword.utils.download_models()\""
            )
            self._model = None
            return False

        logger.info(
            f"Wake word ready — '{self.keyword}' (sensitivity {self.sensitivity})."
        )
        return True

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Load the model and spin up the detection thread. Returns True if the
        listener is running, False if it failed soft (disabled)."""
        if not self._load_model():
            self.enabled = False
            return False
        self.enabled = True
        self._thread = threading.Thread(target=self._run, name="wakeword", daemon=True)
        self._thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, name="wakeword-watchdog", daemon=True
        )
        self._watchdog_thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    # ── detection loop ─────────────────────────────────────────────────────────
    def _run(self) -> None:
        device     = _get_device()
        # Display "jarvis" not "hey jarvis" — sensitivity 0.4 catches the short form too.
        spoken     = "jarvis"
        announced  = False
        fail_count = 0

        # Outer loop: open a FRESH input stream each cycle. We deliberately reopen
        # after every detection instead of stop()/start()-ing one long-lived
        # stream, because on_wake opens its own capture stream on the same device
        # in between — restarting the old stream after that contention is
        # unreliable on Windows and was leaving the listener silent after the
        # first hit. A clean reopen is robust and self-healing.
        while not self._stop.is_set():
            try:
                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=FRAME_SAMPLES,
                    dtype="int16",
                    channels=1,
                    device=device,
                ) as stream:
                    if not announced:
                        print(f'👂 Wake word active — say "{spoken}".')
                        logger.info(f"Wake word listening on device {device}.")
                        announced = True
                    # Drop stale audio + score history on every fresh open.
                    self._reset()
                    _drain(stream)

                    while not self._stop.is_set():
                        # Push-to-talk reserved the mic → break so the `with`
                        # closes our stream and frees the device; we idle below
                        # until it's released, then reopen.
                        if self.busy.is_set():
                            break

                        frame, _overflowed = stream.read(FRAME_SAMPLES)
                        fail_count = 0   # a clean read means the stream is healthy
                        samples = frame[:, 0]
                        if _rms(samples) < self.noise_gate_rms:
                            samples = np.zeros(FRAME_SAMPLES, dtype=np.int16)
                        if self._score(samples) >= self.sensitivity:
                            self._handle_detection(stream)
                            break        # reopen a fresh stream next cycle

            except Exception as e:
                fail_count += 1
                logger.warning(
                    f"Wake word stream error ({e}); reopening "
                    f"[{fail_count}/{_MAX_STREAM_FAILURES}]."
                )
                if fail_count >= _MAX_STREAM_FAILURES:
                    logger.error("Wake word disabled — mic stream kept failing.")
                    break
                sd.sleep(_REOPEN_BACKOFF_MS)
                continue

            if self._stop.is_set():
                break

            # Wait out any push-to-talk reservation with our stream now closed,
            # so listen() has sole use of the mic, then loop back to reopen.
            while self.busy.is_set() and not self._stop.is_set():
                sd.sleep(50)

            if not self._stop.is_set():
                logger.info("Wake word resuming...")

        self.enabled = False
        logger.info("Wake word listener stopped.")

    def _watchdog(self) -> None:
        """Separate daemon thread: polls liveness every 5 s, restarts the
        detection thread if it has died without a stop() call, and logs a
        heartbeat every 60 s so background health is visible in the log."""
        last_heartbeat = time.time()
        restarts       = 0

        while not self._stop.is_set():
            # Use wait() so stop() wakes us immediately instead of sleeping a full tick.
            self._stop.wait(timeout=_WATCHDOG_INTERVAL_S)
            if self._stop.is_set():
                break

            now = time.time()

            if self._thread is not None and self._thread.is_alive():
                # Detection thread is healthy.
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
                    logger.info("Wake word active.")
                    last_heartbeat = now
                restarts = 0   # reset counter — thread has been alive since last check
                continue

            # Thread is dead and we weren't asked to stop → restart.
            restarts += 1
            if restarts > _MAX_WATCHDOG_RESTARTS:
                logger.error(
                    "Wake word watchdog: too many restarts — giving up. "
                    "Check microphone availability."
                )
                self.enabled = False
                break

            logger.warning(
                f"Wake word watchdog: restarting stream "
                f"[attempt {restarts}/{_MAX_WATCHDOG_RESTARTS}]."
            )
            self._stop.wait(timeout=_WATCHDOG_BACKOFF_S)
            if self._stop.is_set():
                break

            self.enabled = True
            self._thread = threading.Thread(target=self._run, name="wakeword", daemon=True)
            self._thread.start()
            last_heartbeat = time.time()

    def _handle_detection(self, stream) -> None:
        logger.info("Wake word detected.")
        # Reserve the mic (mic_lock) and stop our stream so the callback's
        # capture() can take the device. The outer loop reopens a fresh stream
        # once we return, so we don't try to restart this one.
        self.busy.set()
        self._pause(stream)
        try:
            self.on_wake()
        except Exception as e:
            logger.error(f"Wake word on_wake callback failed: {e}")
        finally:
            # Refractory pause so the decaying score can't immediately re-fire,
            # then release the mic_lock for push-to-talk and our own reopen.
            time.sleep(_REFRACTORY_SEC)
            self.busy.clear()

    # ── helpers ────────────────────────────────────────────────────────────────
    def _score(self, samples: np.ndarray) -> float:
        try:
            scores = self._model.predict(samples)
        except Exception as e:
            logger.debug(f"Wake word predict error: {e}")
            return 0.0
        # Only one model is loaded, so the max over scores is that model's score.
        return max(scores.values()) if scores else 0.0

    def _reset(self) -> None:
        try:
            if self._model is not None:
                self._model.reset()
        except Exception:
            pass

    @staticmethod
    def _pause(stream) -> None:
        try:
            if not stream.stopped:
                stream.stop()
        except Exception:
            pass

    @staticmethod
    def _resume(stream) -> None:
        try:
            if stream.stopped:
                stream.start()
        except Exception:
            pass


def _drain(stream) -> None:
    """Discard any audio buffered while detection was paused."""
    try:
        while stream.read_available >= FRAME_SAMPLES:
            stream.read(FRAME_SAMPLES)
    except Exception:
        pass


def _rms(samples: np.ndarray) -> float:
    """Root-mean-square energy of an int16 audio frame."""
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
