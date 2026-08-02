# OS GOD

OS GOD is a Windows automation assistant for switching your computer into predefined modes. A mode can open and close applications, open websites, set system volume, change Wallpaper Engine wallpaper, and play an audio cue.

It includes a desktop command center, a browser-based controller for phones and tablets, text commands, and optional offline voice input.

## Current status

The project currently supports:

- Mode activation from the desktop dashboard, browser controller, text commands, or voice input.
- Preset modes for focus, zen, rendering, DJ, gaming, development, shutdown, and GG audio.
- A desktop JARVIS Command Center started automatically by `run.bat`.
- A browser controller available on the local network at port `5000`.
- Offline speech recognition through Faster Whisper.
- Wake-word listening and the `Alt+J` shortcut.
- Dashboard microphone capture through the core server, avoiding microphone conflicts with the wake-word listener.
- Optional natural-language commands through the Anthropic API when `ANTHROPIC_API_KEY` is configured.

Windows is required. Wallpaper switching is optional and needs Wallpaper Engine installed.

## Quick start

1. Install Python 3.10 or later on Windows.
2. Double-click [run.bat](run.bat).
3. On the first run, OS GOD creates `.venv` and installs its dependencies.
4. Keep the `OS GOD Core` window running, then use the JARVIS Command Center that opens.

The first voice-recognition startup can take longer because the local Whisper model may need to download.

## Using the desktop command center

The command center connects to the local core at `http://localhost:5000`.

- Use a mode button or type a command to activate a mode.
- Select the microphone button and then speak your command. The core will show that it is listening.
- If the dashboard says it is offline, start or restart the core with `run.bat`.
- `Alt+J` can also start a hands-free capture directly from Windows.

## Using the phone or tablet controller

When the core starts, it prints a local-network URL similar to:

```text
http://192.168.x.x:5000/?token=your-token
```

Open that URL on a device connected to the same Wi-Fi network. The default token is `pawnin2025`; set the `OSGOD_TOKEN` environment variable before launch to use a different token.

## Text commands

Use these commands in the core window or dashboard:

| Command | Result |
| --- | --- |
| `activate alpha state` | Starts the alpha focus setup. |
| `activate zen state` | Starts the zen setup. |
| `activate developer mode` | Starts the development setup. |
| `activate gaming state` | Starts the gaming setup. |
| `activate [mode] for 30 minutes` | Activates a mode with a timer. |
| `list modes` | Lists available modes. |
| `what mode` | Shows the active mode and elapsed time. |
| `history` | Shows mode-usage history. |
| `cancel timer` | Cancels the active timer. |
| `voice mode` / `text mode` | Changes the core input method. |
| `exit` | Stops OS GOD. |

## Configuration

Edit [config.yaml](config.yaml) to change presets. Each mode can contain any of the following fields:

```yaml
modes:
  my focus mode:
    volume: 40
    sound: assets/sounds/alpha.wav
    wallpaper: "WORKSHOP_ID"
    apps:
      - opera gx
      - notion
    websites:
      - https://music.youtube.com
    close:
      - discord
```

Do not place the same application in both `apps` and `close` for a mode. Use valid `http://` or `https://` URLs, and ensure configured sound files exist.

See [CONFIG_GUIDE.md](CONFIG_GUIDE.md) for more configuration details.

## Voice and AI options

Voice recognition works locally. Wake-word listening is enabled by default in `config.yaml`; disable it if you do not want OS GOD to keep the microphone open.

Natural-language understanding is optional. To enable it, set an Anthropic API key before launching:

```powershell
$env:ANTHROPIC_API_KEY = "your-api-key"
```

Without a key, normal mode commands and the offline parser continue to work.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `run.bat` reports that the core did not start | Check the `OS GOD Core` window for the printed Python error. |
| Dashboard is offline | Close any old core window and start the project again with `run.bat`. |
| Microphone button fails | Restart the core after updating the project, then allow microphone access in Windows. |
| Voice capture hears nothing | Check the selected Windows input device and `wakeword` settings in `config.yaml`. |
| A program does not open | Update the relevant application path or mode entry in `core/router.py` and `config.yaml`. |
| Wallpaper does not change | Install Wallpaper Engine or remove `wallpaper` from the mode. |

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | Core engine and command loop. |
| `run.bat` | Windows launcher. |
| `config.yaml` | Modes and runtime settings. |
| `core/dashboard.py` | Desktop JARVIS Command Center. |
| `utils/server.py` | Local browser-controller and voice API server. |
| `assets/sounds/` | Mode and DJ audio files. |

## Dependencies

Dependencies are listed in [requirements.txt](requirements.txt). The launcher installs them automatically for a new virtual environment.

## License

MIT
