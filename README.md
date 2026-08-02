# 🖥️ OS GOD — One Command. Total Control.

> Switch your entire PC into a different mode — apps, websites, wallpaper, volume, and sound — with a single voice or text command.

---

## ⚡ What It Does

You type (or say) **"activate zen state"** and OS GOD:

- Closes Discord, WhatsApp, Notepad
- Opens Notion and Opera GX
- Launches your focus websites
- Changes your wallpaper (Wallpaper Engine)
- Sets your volume to 30%
- Plays a calm sound

One command. Your PC is in a completely different environment in seconds.

---

## 🎬 Demo

> *(Add your GIF here — record with ScreenToGif)*

---

## 🚀 Quick Start

**1. Clone the repo**
```bash
git clone https://github.com/yourusername/os-god.git
cd os-god
```

**2. Run it** (double-click or terminal)
```bash
run.bat
```
The bat file auto-creates a virtual environment and installs dependencies on first run.

**3. Type a command**
```
⌨️ Command: activate zen state
```

---

## 🧠 Commands

| Command | What happens |
|---|---|
| `activate [mode name]` | Switch to that mode |
| `list modes` | Show all available modes |
| `what mode` | Show current mode + time active |
| `voice mode` | Switch to microphone input |
| `text mode` | Switch to keyboard input |
| `system gg` | Plays the GG sound |
| `help` | Show all commands |
| `exit` | Shut down OS GOD |

In voice mode, prefix commands with a trigger word: **system**, **nyra**, or **jarvis**

---

## 🧠 Jarvis Brain (natural language)

OS GOD has a **hybrid brain**. When it's enabled it understands free-form language
("open spotify and turn the volume down to 20", "put me in zen for half an hour")
instead of only fixed commands — Claude figures out the intent and calls the right
actions. It runs in two modes automatically:

| State | When | Behaviour |
|---|---|---|
| **Online (smart)** | `ANTHROPIC_API_KEY` is set + `brain.enabled: true` | Natural language → Claude with tool-use |
| **Offline (fallback)** | no key / disabled / API error | The built-in fuzzy command parser |

**Enable it:**

1. `pip install -r requirements.txt` (installs the `anthropic` SDK)
2. Set your key in the environment before launching:
   ```bash
   set ANTHROPIC_API_KEY=sk-ant-...     # Windows CMD
   $env:ANTHROPIC_API_KEY="sk-ant-..."  # PowerShell
   ```
3. Run normally. You'll see `🧠 Brain online` at startup.

Tune it in `config.yaml`:
```yaml
brain:
  enabled: true
  model: claude-opus-4-8   # or claude-sonnet-4-6 / claude-haiku-4-5 for cheaper/faster
  effort: low              # low | medium | high
```

You can also type natural language into the **Ask OS GOD** box in the phone/iPad
controller, or POST to `/command`. No key? Everything still works through the
offline parser — the brain is purely additive.

---

## 🗂️ Built-in Modes

| Mode | Purpose |
|---|---|
| `alpha state` | Deep focus — study, coding, leetcode |
| `zen state` | Light focus — calm, creative thinking |
| `render state` | After Effects workflow |
| `dj pawnin state` | IPL / hype / chill sessions |
| `gaming state` | Full game mode — Discord up, distractions gone |
| `shutdown` | Shuts down the PC |
| `gg` | Plays the GG sound effect |

---

## ⚙️ Customising Modes

Edit `config.yaml` — no coding required.

```yaml
modes:
  my custom mode:
    sound: assets/sounds/mysound.wav
    wallpaper: "WORKSHOP_ID_HERE"
    volume: 60
    apps:
      - spotify
      - vscode
    websites:
      - https://github.com
    close:
      - discord
```

Full guide: see `CONFIG_GUIDE.md`

---

## 📦 Install Requirements

- Windows 10/11
- Python 3.10+
- [Wallpaper Engine](https://store.steampowered.com/app/431960/Wallpaper_Engine/) (optional — for wallpaper switching)

---

## 🗺️ Roadmap

- [ ] Mode duration timer ("focus for 90 minutes then switch")
- [ ] Scheduled modes (activate alpha at 9am on weekdays)
- [ ] Mode history log (CSV of what you used and when)
- [ ] System tray GUI with quick-switch buttons
- [ ] Windows notification mute during focus modes
- [ ] `undo last mode` command

---

## 🤝 Contributing

Issues and PRs welcome. If you want a feature — open a discussion first so we can talk about the approach.

---

## 📄 License

MIT — free to use, modify, and distribute.
