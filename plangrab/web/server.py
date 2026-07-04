"""Start the local PlanGrab web app and open a browser to it.

Used by Run.ps1 (Windows) and run.sh (macOS). Binds 127.0.0.1 on a free port,
so it never clashes with anything and is never reachable off the machine.
Closing the console window stops the server.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Allow `python -m plangrab.web.server` and `python plangrab/web/server.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import uvicorn  # noqa: E402

from plangrab.engine.config import Config  # noqa: E402
from plangrab.engine.update import start_background_refresh  # noqa: E402
from plangrab.web.app import app  # noqa: E402

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def main() -> None:
    port = _free_port()
    url = f"http://{HOST}:{port}/"

    def open_browser() -> None:
        time.sleep(1.0)  # give uvicorn a moment to bind
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()
    # Quietly pick up newly-added councils from the repo (best-effort, offline-safe).
    start_background_refresh(Config.load())
    print(f"PlanGrab running at {url}")
    print("Leave this window open. Close it to stop PlanGrab.")
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
