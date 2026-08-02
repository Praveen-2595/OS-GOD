import os
import re
import numpy as np
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# faster-whisper setup
# ─────────────────────────────────────────────────────────────────────────────
# Replaces the old Vosk small model (audit: Critical Fix — it mmisheard most
# commands, e.g. "open opera gx" → "open for but idea"). faster-whisper runs
# OpenAI Whisper locally via CTranslate2 — far better accuracy, still offline,
# and needs NO torch. First run downloads the model (~145 MB) to the HuggingFace
# cache; after that it is fully offline.
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
MIN_WORDS   = 1        # discard transcriptions shorter than this

# Model size / compute can be overridden via env vars.
#   WHISPER_MODEL   — model id (default "base.en"; try "small.en" for more accuracy)
#   WHISPER_DEVICE  — "cpu" (default) or "cuda"
#   WHISPER_COMPUTE — "int8" (default, CPU-friendly) / "float16" (GPU) / "float32"
_MODEL_NAME   = os.environ.get("WHISPER_MODEL", "base.en")
_DEVICE       = os.environ.get("WHISPER_DEVICE", "cpu")
_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "int8")


# ─────────────────────────────────────────────────────────────────────────────
# Load model once at import (download happens here on first ever run)
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: ASCII-only prints here on purpose — this runs at *import* time, before
# main.py configures the console, so it must not depend on a UTF-8 code page.
try:
    logger.info(f"Loading Whisper model '{_MODEL_NAME}' ({_DEVICE}/{_COMPUTE_TYPE})...")
    print(f"Loading speech model '{_MODEL_NAME}' (first run downloads ~145 MB)...")
    _model = WhisperModel(_MODEL_NAME, device=_DEVICE, compute_type=_COMPUTE_TYPE)
    logger.info("Whisper model loaded.")
    print("Speech model ready.")
except Exception as e:
    logger.error(f"Failed to load Whisper model: {e}")
    print(
        f"[ERROR] Could not load Whisper model '{_MODEL_NAME}': {e}\n"
        "   First run needs internet to download the model. After that it's offline.\n"
        "   Override the model with the WHISPER_MODEL env var if needed."
    )
    _model = None


def _normalize(text: str) -> str:
    """Whisper emits 'Open Opera GX.' — downstream expects 'open opera gx'."""
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)          # collapse whitespace
    text = text.strip(" .!?,…\"'")        # strip wrapping punctuation
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Main recognizer  (same signature/contract as the old Vosk version)
# ─────────────────────────────────────────────────────────────────────────────
def recognize(audio: bytes | None) -> str:
    """
    Transcribe raw PCM bytes (int16, mono, 16 kHz) → lowercase text string.
    Returns "" on failure or garbage input.
    """
    if not audio:
        return ""

    if _model is None:
        print("❌ Cannot recognize — Whisper model not loaded.")
        return ""

    try:
        # int16 PCM bytes → float32 in [-1, 1], which is what Whisper expects.
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

        segments, _info = _model.transcribe(
            samples,
            language="en",
            beam_size=5,
            vad_filter=True,                 # suppress silence-driven hallucinations
            condition_on_previous_text=False,  # each command is independent
        )

        text = _normalize(" ".join(seg.text for seg in segments))

        logger.debug(f"Whisper raw result: {text!r}")

        if not text:
            print("⚠️  Nothing recognised — try speaking more clearly.")
            return ""

        if len(text.split()) < MIN_WORDS:
            logger.debug(f"Discarded short result: {text!r}")
            return ""

        print(f"🗣️  Heard: {text!r}")
        return text

    except Exception as e:
        logger.error(f"Recognizer error: {e}")
        print(f"⚠️  Recognizer error: {e}")
        return ""
