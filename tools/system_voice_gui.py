"""
System Voice GUI — paste text, click Generate, get a saved audio file.
No terminal needed after launch. Double-click this file (or the .bat below)
to open it.

Reuses the synthesis + effect pipeline from tools/system_voice.py.
"""
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from system_voice import OUTPUT_DIR, VOICE_FEMALE, VOICE_MALE, speak_to_file  # noqa: E402


class SystemVoiceApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("System Voice")
        root.geometry("520x420")
        root.minsize(420, 340)

        self.voice_var = tk.StringVar(value="male")
        self.status_var = tk.StringVar(value="Paste text below and click Generate.")
        self.last_output: Path | None = None

        pad = {"padx": 10, "pady": 6}

        ttk.Label(root, text="Text").pack(anchor="w", **pad)

        text_frame = ttk.Frame(root)
        text_frame.pack(fill="both", expand=True, padx=10)
        self.text_box = tk.Text(text_frame, wrap="word", height=10, undo=True)
        self.text_box.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(text_frame, command=self.text_box.yview)
        scrollbar.pack(side="right", fill="y")
        self.text_box["yscrollcommand"] = scrollbar.set
        self.text_box.focus_set()

        voice_frame = ttk.Frame(root)
        voice_frame.pack(anchor="w", **pad)
        ttk.Label(voice_frame, text="Voice:").pack(side="left")
        ttk.Radiobutton(voice_frame, text="Male", variable=self.voice_var, value="male").pack(side="left", padx=6)
        ttk.Radiobutton(voice_frame, text="Female", variable=self.voice_var, value="female").pack(side="left")

        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill="x", **pad)
        self.generate_btn = ttk.Button(btn_frame, text="Generate", command=self._on_generate)
        self.generate_btn.pack(side="left")
        self.play_btn = ttk.Button(btn_frame, text="Play", command=self._on_play, state="disabled")
        self.play_btn.pack(side="left", padx=6)
        self.folder_btn = ttk.Button(btn_frame, text="Open Folder", command=self._on_open_folder, state="disabled")
        self.folder_btn.pack(side="left")

        ttk.Label(root, textvariable=self.status_var, wraplength=480, foreground="#444").pack(
            anchor="w", fill="x", **pad
        )

    def _on_generate(self) -> None:
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            self.status_var.set("Type or paste some text first.")
            return

        self.generate_btn["state"] = "disabled"
        self.play_btn["state"] = "disabled"
        self.folder_btn["state"] = "disabled"
        self.status_var.set("Generating...")

        voice = VOICE_FEMALE if self.voice_var.get() == "female" else VOICE_MALE
        threading.Thread(target=self._generate_worker, args=(text, voice), daemon=True).start()

    def _generate_worker(self, text: str, voice: str) -> None:
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = OUTPUT_DIR / f"{stamp}.mp3"
            result = speak_to_file(text, out_path, voice=voice)
            self.root.after(0, self._on_done, result, None)
        except Exception as e:
            self.root.after(0, self._on_done, None, e)

    def _on_done(self, result: Path | None, error: Exception | None) -> None:
        self.generate_btn["state"] = "normal"
        if error is not None:
            self.status_var.set(f"Failed: {error}")
            return
        self.last_output = result
        self.status_var.set(f"Saved: {result}")
        self.play_btn["state"] = "normal"
        self.folder_btn["state"] = "normal"

    def _on_play(self) -> None:
        if self.last_output is None:
            return
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", str(self.last_output)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_open_folder(self) -> None:
        if self.last_output is None:
            return
        os.startfile(self.last_output.parent)  # noqa: S606 — Windows-only tool


if __name__ == "__main__":
    root = tk.Tk()
    SystemVoiceApp(root)
    root.mainloop()
