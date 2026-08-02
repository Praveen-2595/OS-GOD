import sounddevice as sd
import queue
import numpy as np
from pynput import keyboard
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# Audio config
# ─────────────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000
BLOCK_SIZE  = 8000
SILENCE_MIN = 15       # RMS below this = silence / garbage

# Override with int index to force a specific mic. None = system default.
# NOTE: system default (idx 1) is "tranScreen Audio", a virtual/phantom device
# that captures silence. Pin to the real Realtek mic instead.
# Alternatives if 2 misbehaves: 15 (Realtek WASAPI) or 23 (Realtek WDM-KS).
DEVICE = 2


def _get_device() -> int | None:
    """Return DEVICE if set, otherwise the system default input device index."""
    if DEVICE is not None:
        return DEVICE
    try:
        idx = sd.default.device[0]
        # BUG FIX: sd.default.device can return -1 when no default is set
        return idx if idx >= 0 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main listen function
# ─────────────────────────────────────────────────────────────────────────────
def listen() -> bytes | None:
    """
    Hold SPACE to record.
    Returns raw PCM bytes (int16, mono, 16 kHz) or None if nothing captured.
    """
    q:        queue.Queue       = queue.Queue()
    captured: list[np.ndarray] = []
    recording = False
    done      = False

    # ── audio callback ────────────────────────────────────────────────────────
    def audio_cb(indata, frames, t, status):
        if status:
            logger.debug(f"Audio stream status: {status}")
        if recording:
            q.put(indata.copy())

    # ── keyboard handlers ─────────────────────────────────────────────────────
    def on_press(key):
        nonlocal recording
        if key == keyboard.Key.space and not recording:
            # Flush any stale audio queued before this press
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            print("🎤 Recording... (release SPACE to stop)")
            recording = True

    def on_release(key):
        nonlocal recording, done
        if key == keyboard.Key.space and recording:
            recording = False
            done = True
            return False  # stop keyboard listener

    print("🟢 Hold SPACE to speak...")

    device = _get_device()

    # BUG FIX: validate device index exists before opening stream
    if device is not None:
        try:
            sd.check_input_settings(device=device, samplerate=SAMPLE_RATE, channels=1)
        except Exception as e:
            logger.warning(f"Device {device} check failed ({e}), falling back to default.")
            device = None

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            dtype="int16",
            channels=1,
            callback=audio_cb,
            device=device,
        ):
            with keyboard.Listener(on_press=on_press, on_release=on_release) as lst:
                lst.join()

    except sd.PortAudioError as e:
        logger.error(f"Microphone error: {e}")
        print(f"❌ Mic error: {e}")
        print("   Check your mic is connected. Set DEVICE to a valid index in listener.py if needed.")
        return None

    except Exception as e:
        logger.error(f"Listener error: {e}")
        print(f"❌ Listener error: {e}")
        return None

    # ── drain queue ───────────────────────────────────────────────────────────
    while not q.empty():
        try:
            chunk = q.get_nowait()
            captured.append(chunk)
        except queue.Empty:
            break

    if not captured:
        print("⚠️  No audio captured — did you hold SPACE?")
        return None

    audio = np.concatenate(captured, axis=0)

    # ── silence check ─────────────────────────────────────────────────────────
    # BUG FIX: cast to float before mean to avoid int16 overflow on abs()
    overall_vol = int(np.abs(audio.astype(np.float32)).mean())
    logger.debug(f"Captured audio RMS: {overall_vol}")

    if overall_vol < SILENCE_MIN:
        print("⚠️  Audio too quiet — move closer to your mic or speak louder.")
        return None

    return audio.tobytes()