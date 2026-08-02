"""
core/auto_capture.py — hands-free voice capture with simple energy VAD.

After the wake word fires, we start recording immediately (no key press), watch
the mic energy to tell when the user starts and stops talking, and return the
captured PCM so it can be transcribed. This is the true "Hey Jarvis → beep →
speak → act" feel, with no hands required.

Contract matches core.listener.listen(): returns raw PCM bytes (int16, mono,
16 kHz) ready for core.recognizer.recognize(), or None if nothing was said.
SPACE push-to-talk (core.listener.listen) is untouched and works independently.
"""

import numpy as np
import sounddevice as sd
from loguru import logger

# Reuse the listener's sample rate, device selection, and measured silence floor
# so capture matches push-to-talk on the same mic.
from core.listener import SAMPLE_RATE, _get_device, SILENCE_MIN

# ── VAD tuning ───────────────────────────────────────────────────────────────
_FRAME_SAMPLES    = 512    # ~32 ms per frame at 16 kHz — fine-grained silence timing
_MAX_SECONDS      = 5.0    # hard cap on total capture
_TRAILING_SILENCE = 1.5    # stop this long after speech ends
_PRIME_SECONDS    = 0.12   # discard at start (mic settle + tail of the wake beep)
# Mean-abs amplitude that counts as speech. Tuned to the same mic scale as
# listener.SILENCE_MIN (the measured silence floor); sit clearly above it.
_SPEECH_LEVEL     = max(SILENCE_MIN * 2, 25)
# Reject captures whose speech span (first→last speech frame) is shorter than
# this. Real commands run ~0.8 s+ ("Open VS Code" ≈ 0.9 s); brief sub-second
# background bursts (audio bleeding from a video) are too short and would
# otherwise fire actions on a false wake. Tune with the brain's SAFETY RULE.
_MIN_SPEECH_SECONDS = 0.8


def _level(samples: np.ndarray) -> float:
    """Mean absolute amplitude of an int16 block — a cheap energy/RMS proxy."""
    return float(np.abs(samples.astype(np.float32)).mean())


def capture(
    max_seconds: float = _MAX_SECONDS,
    trailing_silence: float = _TRAILING_SILENCE,
    speech_level: float = _SPEECH_LEVEL,
    min_speech_seconds: float = _MIN_SPEECH_SECONDS,
    device=None,
) -> bytes | None:
    """Record hands-free until the user stops speaking or the cap is hit.

    Stops when EITHER:
      - speech has been detected and `trailing_silence` seconds of quiet follow, or
      - `max_seconds` of audio have been captured (hard cap; also covers the
        case where the user never spoke).

    Returns int16 / mono / 16 kHz PCM bytes, or None if no speech was detected.
    """
    if device is None:
        device = _get_device()

    frame_dt = _FRAME_SAMPLES / SAMPLE_RATE
    captured: list[np.ndarray] = []
    speech_started = False
    silence_run    = 0.0
    elapsed        = 0.0
    first_speech_at = None   # elapsed at first speech frame
    last_speech_at  = 0.0    # elapsed at most recent speech frame

    print("🎙️  Listening...")
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=_FRAME_SAMPLES,
            dtype="int16",
            channels=1,
            device=device,
        ) as stream:
            # Drop the first few frames so the mic can settle and any tail/echo of
            # the wake-word confirmation beep isn't mistaken for speech.
            primed = 0.0
            while primed < _PRIME_SECONDS:
                stream.read(_FRAME_SAMPLES)
                primed += frame_dt

            while True:
                block, _overflow = stream.read(_FRAME_SAMPLES)
                captured.append(block.copy())
                elapsed += frame_dt

                if _level(block[:, 0]) >= speech_level:
                    speech_started = True
                    silence_run = 0.0
                    if first_speech_at is None:
                        first_speech_at = elapsed
                    last_speech_at = elapsed
                elif speech_started:
                    silence_run += frame_dt

                # Natural end: speech happened, then enough trailing silence.
                if speech_started and silence_run >= trailing_silence:
                    break
                # Hard cap.
                if elapsed >= max_seconds:
                    break

    except sd.PortAudioError as e:
        logger.error(f"Auto-capture mic error: {e}")
        print(f"❌ Mic error: {e}")
        return None
    except Exception as e:
        logger.error(f"Auto-capture error: {e}")
        return None

    if not speech_started:
        print("⚠️  Didn't hear anything.")
        return None

    # Reject captures too short to be a real command — guards against a false
    # wake on a brief noise burst triggering a (possibly destructive) action.
    speech_span = (last_speech_at - first_speech_at + frame_dt) \
        if first_speech_at is not None else 0.0
    if speech_span < min_speech_seconds:
        logger.info(
            f"Auto-capture rejected: speech {speech_span:.2f}s "
            f"< {min_speech_seconds:.1f}s minimum — likely a false trigger."
        )
        print("⚠️  Too short — ignoring (say a full command).")
        return None

    audio = np.concatenate(captured, axis=0)
    logger.debug(
        f"Auto-capture: {elapsed:.1f}s captured "
        f"(speech {speech_span:.1f}s, trailing silence {silence_run:.1f}s)."
    )
    return audio.tobytes()
