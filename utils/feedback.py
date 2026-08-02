import sys
import os
import time
import subprocess
import threading
import winsound
from loguru import logger
import comtypes
comtypes.CoInitialize()


# ─────────────────────────────────────────────────────────────────────────────
# Resource path helper
# ─────────────────────────────────────────────────────────────────────────────
def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


# ─────────────────────────────────────────────────────────────────────────────
# TTS — Edge-TTS (natural neural voice, needs internet) with pyttsx3 fallback
# (offline, robotic, always available). Engine/voice picked from config.yaml's
# `tts:` block; any failure in Edge-TTS (offline, ffplay missing, etc.) falls
# back to pyttsx3 for that utterance.
# ─────────────────────────────────────────────────────────────────────────────
_tts_engine = None
_tts_lock   = threading.Lock()


def _load_tts_config() -> dict:
    try:
        import yaml
        with open(resource_path("config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("tts", {}) or {}
    except Exception as e:
        logger.debug(f"Could not load tts config, defaulting to pyttsx3: {e}")
        return {}


_TTS_CONFIG = _load_tts_config()


def _get_tts_engine():
    global _tts_engine
    if _tts_engine is None:
        import pyttsx3
        _tts_engine = pyttsx3.init()
        _tts_engine.setProperty("rate", 165)
        _tts_engine.setProperty("volume", 1.0)
    return _tts_engine


def _speak_pyttsx3(text: str) -> None:
    global _tts_engine
    try:
        engine = _get_tts_engine()
        engine.say(text)
        engine.runAndWait()
    except SystemExit:
        pass   # never let TTS kill the process
    except Exception as e:
        logger.error(f"TTS error: {e}")
        _tts_engine = None  # reset so next call reinitializes cleanly


def _speak_edge_tts(text: str, voice: str) -> bool:
    """Synthesize with Edge-TTS and play via ffplay. Returns False on any
    failure (no internet, ffplay missing, etc.) so the caller can fall back."""
    import asyncio
    import tempfile
    import uuid
    import edge_tts

    path = os.path.join(tempfile.gettempdir(), f"osgod_tts_{uuid.uuid4().hex}.mp3")
    try:
        asyncio.run(edge_tts.Communicate(text, voice).save(path))
        result = subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"Edge-TTS unavailable, falling back to pyttsx3: {e}")
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def speak(text: str) -> None:
    print(f"🗣️  {text}")

    def _speak_thread():
        with _tts_lock:
            engine_name = _TTS_CONFIG.get("engine", "pyttsx3")
            voice       = _TTS_CONFIG.get("voice", "en-US-GuyNeural")
            if engine_name == "edge_tts" and _speak_edge_tts(text, voice):
                return
            _speak_pyttsx3(text)

    threading.Thread(target=_speak_thread, daemon=True).start()
    # NO join() — returns immediately. _tts_lock ensures one utterance at a time.


# ─────────────────────────────────────────────────────────────────────────────
# Beep
# ─────────────────────────────────────────────────────────────────────────────
def beep() -> None:
    try:
        winsound.Beep(1000, 150)
    except Exception as e:
        logger.debug(f"Beep failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Sound playback
# ─────────────────────────────────────────────────────────────────────────────
_current_process: subprocess.Popen | None = None


def _kill_current() -> None:
    global _current_process
    if _current_process is not None:
        try:
            _current_process.kill()
        except Exception:
            pass
        _current_process = None


def play_sound(file: str) -> None:
    """Non-blocking — starts sound and returns immediately."""
    global _current_process
    _kill_current()

    path = resource_path(file)
    if not os.path.exists(path):
        logger.warning(f"Sound not found: {path}")
        print(f"⚠️  Sound not found: {file}")
        return

    try:
        _current_process = subprocess.Popen(
            ["powershell", "-c",
             f'(New-Object Media.SoundPlayer "{path}").PlaySync();'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"Playing: {file}")
    except Exception as e:
        logger.error(f"Sound error: {e}")


def play_sound_blocking(file: str) -> None:
    """Blocking — waits until sound fully finishes."""
    global _current_process
    _kill_current()

    path = resource_path(file)
    if not os.path.exists(path):
        logger.warning(f"Sound not found: {path}")
        print(f"⚠️  Sound not found: {file}")
        return

    try:
        proc = subprocess.Popen(
            ["powershell", "-c",
             f'(New-Object Media.SoundPlayer "{path}").PlaySync();'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _current_process = proc
        proc.wait()
        _current_process = None
        logger.info(f"Sound finished: {file}")
    except Exception as e:
        logger.error(f"Sound blocking error: {e}")


def fade_out_sound(duration: float = 2.0) -> None:
    """Fade out current sound over duration seconds then kill it."""
    global _current_process
    if _current_process is None:
        return
    try:
        steps    = 20
        interval = duration / steps
        current  = get_volume()
        for i in range(steps):
            if _current_process is None:
                break
            _set_volume_internal(max(0.0, current * (1 - (i + 1) / steps)))
            time.sleep(interval)
    except Exception as e:
        logger.debug(f"Fade error: {e}")
    finally:
        _kill_current()


# ─────────────────────────────────────────────────────────────────────────────
# Volume control
#
# COM threading fix (Python 3.14): every pycaw call runs in a fresh thread
# with pythoncom.CoInitialize()/CoUninitialize() so COM objects are always
# created and used in the same apartment. Uses Activate()+cast() rather than
# QueryInterface() — the former works reliably across Python versions.
#
# _volume_available is set to False after the first failure so repeated callers
# (e.g. fade loops, /status polling) don't spam the log with the same error.
# ─────────────────────────────────────────────────────────────────────────────
# One-time failure flag. After the first failure we go silent FOREVER: no more
# COM attempts, no more log lines. The whole codebase speaks the 0.0–1.0 scalar
# scale (server.py *100, router.py restores get_volume()→set_volume()), so the
# silent fallback is 0.5, not 50.
_volume_available = True


def _run_in_com_thread(fn, *args, timeout: float = 3.0):
    """Execute fn(*args) in a new thread with COM initialized. Returns result."""
    from typing import Any
    result: list[Any]                  = [None]
    exc:    list[BaseException | None] = [None]
    done   = threading.Event()

    def _worker() -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            result[0] = fn(*args)
        except BaseException as e:
            exc[0] = e
        finally:
            pythoncom.CoUninitialize()
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    if not done.wait(timeout=timeout):
        raise TimeoutError("COM thread timed out")
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def _get_volume_controller():
    """Return an IAudioEndpointVolume interface for the default render device.

    pycaw's GetSpeakers() may return either a raw IMMDevice COM pointer (which
    has .Activate) or an AudioDevice Python wrapper (which does not). We unwrap
    the Python wrapper via ._dev before calling Activate so both cases work.
    Returns the casted interface directly — it is NOT subscriptable, so call
    methods on it straight (no [0]).
    """
    from ctypes import cast, POINTER                                          # type: ignore[import-untyped]
    from comtypes import CLSCTX_ALL                                           # type: ignore[import-untyped]
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume              # type: ignore[import-untyped]
    device = AudioUtilities.GetSpeakers()
    # AudioDevice Python wrapper — unwrap to raw IMMDevice COM pointer
    if not hasattr(device, "Activate") and hasattr(device, "_dev"):
        device = device._dev
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))  # pyright: ignore[reportDeprecated, reportArgumentType]


def _set_volume_internal(level: float) -> None:
    """Internal — sets volume without logging. Used by fade_out_sound."""
    global _volume_available
    if not _volume_available:
        return
    level = max(0.0, min(1.0, float(level)))
    def _do() -> None:
        _get_volume_controller().SetMasterVolumeLevelScalar(level, None)
    try:
        _run_in_com_thread(_do)
    except Exception:
        _volume_available = False   # stay in sync; get/set already logged once


def get_volume() -> float:
    """Return current master volume as a 0.0–1.0 scalar. Silent 0.5 fallback."""
    global _volume_available
    if not _volume_available:
        return 0.5
    def _do() -> float:
        return _get_volume_controller().GetMasterVolumeLevelScalar()
    try:
        v = _run_in_com_thread(_do)
        return float(v) if v is not None else 0.5
    except Exception as e:
        _volume_available = False
        logger.warning(f"Volume control unavailable: {e}")
        return 0.5   # silent fallback forever after


def set_volume(level: float) -> None:
    """Set master volume from a 0.0–1.0 scalar. Silent no-op after first failure."""
    global _volume_available
    if not _volume_available:
        return
    level = max(0.0, min(1.0, float(level)))
    def _do() -> None:
        _get_volume_controller().SetMasterVolumeLevelScalar(level, None)
    try:
        _run_in_com_thread(_do)
        logger.info(f"Volume set to {int(level * 100)}%")
    except Exception as e:
        _volume_available = False
        logger.warning(f"Volume control unavailable: {e}")
