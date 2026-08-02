"""
launch_dashboard.py — start the JARVIS Command Center desktop app.

Run this AFTER OS GOD is already running (it connects to the Flask server on
http://localhost:5000). Standalone:  python launch_dashboard.py
"""

from core.dashboard import main

if __name__ == "__main__":
    main()
