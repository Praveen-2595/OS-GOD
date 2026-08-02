# ⚙️ CONFIG_GUIDE — How to Customise OS GOD

Everything in OS GOD is controlled by `config.yaml`. No coding needed.

---

## File Structure Overview

```yaml
version: "1.0"        ← don't change this

defaults:             ← fallback values for all modes
  volume: 50
  sound: null
  wallpaper: null

trigger_words:        ← what you say before a voice command
  - system

cooldown: 1.5         ← seconds between commands

modes:                ← your actual modes live here
  my mode:
    ...
```

---

## Mode Fields — Full Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `sound` | string (path) | No | `.wav` file to play when mode activates. Path is relative to the project root. |
| `wallpaper` | string (ID) | No | Steam Workshop ID of your Wallpaper Engine wallpaper. |
| `volume` | number 0–100 | No | Sets Windows system volume when mode activates. |
| `apps` | list of strings | No | App names to open. Matched against running processes and Start Menu. |
| `websites` | list of strings | No | Full URLs to open in your default browser. |
| `close` | list of strings | No | App names to force-close. |
| `shutdown` | boolean | No | If `true`, shuts down the PC when this mode is activated. |

---

## Adding a Sound

1. Put your `.wav` file in `assets/sounds/`
2. Reference it as `sound: assets/sounds/yourfile.wav`

---

## Finding a Wallpaper Engine ID

1. Open Steam → Workshop → Wallpaper Engine
2. Find your wallpaper → click it
3. The ID is the number at the end of the URL:
   `https://steamcommunity.com/sharedfiles/filedetails/?id=` **3541330116**
4. Paste that number (as a string in quotes) into `wallpaper:`

---

## Adding a New Mode

```yaml
modes:
  study grind:
    sound: assets/sounds/alpha.wav
    wallpaper: "YOUR_WORKSHOP_ID"
    volume: 45
    apps:
      - notion
      - opera gx
    websites:
      - https://pomofocus.io/app
      - https://leetcode.com
    close:
      - discord
      - whatsapp
```

Then activate it: `activate study grind`

---

## Changing Trigger Words

```yaml
trigger_words:
  - system
  - hey osgod
  - computer
```

---

## Tips

- Mode names are case-insensitive: "Alpha State" and "alpha state" both work
- If a sound file path is wrong, OS GOD will warn you at startup but still run
- If an app in `close` isn't running, it's silently skipped — no crash
- `volume` is optional — if missing, volume stays unchanged
