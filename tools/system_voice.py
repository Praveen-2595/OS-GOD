"""
System Voice — a cold, low-emotion "game system" TTS voice (Solo-Leveling style),
for narrating "[The System]" style lines over YouTube content or anywhere else.

Not wired into the OSGOD assistant loop — this is a standalone reusable tool.
Reuses the same Edge-TTS + ffmpeg approach as utils/feedback.py, but tuned for
a deliberate, emotionless, slightly deepened "system announcement" tone instead
of a natural conversational one, and always renders to a saved audio file.

Usage:
    python tools/system_voice.py "A new quest has been assigned."
    python tools/system_voice.py "Level up." -o clips/levelup.mp3
    python tools/system_voice.py "Level up." --play          # also play after saving
    echo "Quest complete." | python tools/system_voice.py    # read text from stdin

Requires: edge-tts (pip), ffmpeg + ffplay on PATH (ffplay only needed for --play).
"""
import argparse
import asyncio
import sys
from pathlib import Path

import edge_tts

# Low-warmth, "authority"/"confident" voices read closer to a system/narrator
# than a person. rate/pitch pulled down for a deliberate, unhurried cadence.
VOICE_MALE   = "en-US-ChristopherNeural"   # News/Novel, Reliable, Authority
VOICE_FEMALE = "en-US-AriaNeural"          # News/Novel, Positive, Confident
VOICE = VOICE_MALE
RATE  = "-15%"
PITCH = "-5Hz"

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "system_voice"


async def _synthesize(text: str, raw_path: Path, voice: str, rate: str, pitch: str) -> None:
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(raw_path))


def _apply_system_effect(raw_path: Path, out_path: Path) -> None:
    """Light post-processing for a flat, digital, faintly resonant 'system' timbre:
    a presence boost + low-end cleanup for crispness, and a subtle chorus for
    mechanical shimmer. No echo/reverb — add that yourself in editing."""
    import subprocess

    audio_filter = (
        "highpass=f=120,"
        "equalizer=f=4500:width_type=h:width=3000:g=4,"
        "chorus=0.5:0.9:45|55:0.35|0.3:0.25|0.4:2|2.3"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(raw_path),
            "-af", audio_filter,
            str(out_path),
        ],
        check=True,
    )


def speak_to_file(text: str, out_path: Path, voice: str = VOICE, rate: str = RATE, pitch: str = PITCH) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_suffix(".raw.mp3")

    asyncio.run(_synthesize(text, raw_path, voice, rate, pitch))
    _apply_system_effect(raw_path, out_path)
    raw_path.unlink(missing_ok=True)
    return out_path


def _default_output_path(text: str) -> Path:
    slug = "".join(c if c.isalnum() else "_" for c in text[:40]).strip("_").lower()
    return OUTPUT_DIR / f"{slug or 'line'}.mp3"


def main() -> None:
    parser = argparse.ArgumentParser(description="Speak text in a cold 'game system' voice.")
    parser.add_argument("text", nargs="?", help="Text to speak (reads stdin if omitted).")
    parser.add_argument("-o", "--out", type=Path, help="Output audio file path (.mp3).")
    parser.add_argument("--female", action="store_true", help="Use the female voice instead of the default male one.")
    parser.add_argument("--play", action="store_true", help="Play the result after saving.")
    args = parser.parse_args()

    text = args.text or sys.stdin.read().strip()
    if not text:
        parser.error("no text given (pass as an argument or pipe via stdin)")

    out_path = args.out or _default_output_path(text)
    voice = VOICE_FEMALE if args.female else VOICE_MALE
    result = speak_to_file(text, out_path, voice=voice)
    print(f"Saved: {result}")

    if args.play:
        import subprocess
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", str(result)],
        )


if __name__ == "__main__":
    main()
