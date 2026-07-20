"""Start the local PlanGrab web app and open a browser to it.

Used by Run.ps1 (Windows) and run.sh (macOS). Binds 127.0.0.1 on a known port,
so it never clashes with anything and is never reachable off the machine.
Closing the console window stops the server.

The port comes from a small fixed candidate list (config ``[server] ports``) so
the hosted GitHub Pages UI can *find* this helper by probing the same list. Set
``PLANGRAB_PORT`` to force a specific port. If every candidate is busy we fall
back to a random free port (the helper's own local UI still opens fine; only the
hosted page's auto-discovery relies on the known list).
"""
from __future__ import annotations

import os
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


def _is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _pick_port(candidates: list[int]) -> int:
    """First free port: PLANGRAB_PORT override, then the candidate list, then any
    free port. The candidate list is what the hosted UI probes to find us."""
    override = os.environ.get("PLANGRAB_PORT")
    if override:
        return int(override)
    for port in candidates:
        if _is_free(port):
            return port
    return _free_port()


def main() -> None:
    config = Config.load()
    port = _pick_port(config.ports)
    url = f"http://{HOST}:{port}/"

    # By default PlanGrab runs as a quiet background helper for the PlanGrab
    # website (which finds it automatically) — so we don't pop open our own UI.
    # Opt in with [server] open_browser = true or PLANGRAB_OPEN_BROWSER=1.
    if config.open_browser or os.environ.get("PLANGRAB_OPEN_BROWSER"):
        def open_browser() -> None:
            time.sleep(1.0)  # give uvicorn a moment to bind
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    # Quietly pick up newly-added councils from the repo (best-effort, offline-safe).
    start_background_refresh(config)
    print(f"PlanGrab helper running — open the PlanGrab website to use it ({url} also works).")
    print("Keep this window open; close it to stop PlanGrab.")
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
