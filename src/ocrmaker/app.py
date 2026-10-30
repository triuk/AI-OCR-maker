"""Launcher: serves the API and WebUI on loopback for one project library and opens the browser."""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .api import create_api
from .files import write_json_atomic
from .locking import FileLock
from .store import ProjectStore
from .workflow import Workflow

DEFAULT_PORT = 8770


def data_directory(explicit: Path | None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
    if os.environ.get("OCRMAKER_DATA_DIR"):
        return Path(os.environ["OCRMAKER_DATA_DIR"]).expanduser().resolve()
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".local/share") / "ai-ocr-maker"


def listen_socket(port: int, *, fallback: bool) -> socket.socket:
    """A socket on 127.0.0.1 only; with fallback, a busy default port gives way to a free one."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            if not fallback:
                raise
            listener.bind(("127.0.0.1", 0))
        listener.listen(2048)
        return listener
    except OSError:
        listener.close()
        raise


class Control:
    """Lets the WebUI's power button shut the server down."""

    def __init__(self):
        self.server: uvicorn.Server | None = None

    def shutdown(self) -> None:
        if self.server:
            self.server.should_exit = True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="./start.sh", description="AI-OCR-maker: OCR naskenovaných dokumentů")
    parser.add_argument("--data-dir", type=Path,
                        help="Adresář s knihovnou projektů (výchozí ~/.local/share/ai-ocr-maker)")
    parser.add_argument("--port", type=int, help=f"Port rozhraní (výchozí {DEFAULT_PORT}, při obsazení se zvolí volný)")
    parser.add_argument("--server", action="store_true", help="Jen spustit server, neotevírat prohlížeč")
    args = parser.parse_args(argv)

    library = data_directory(args.data_dir) / "library"
    library.mkdir(parents=True, exist_ok=True)
    runtime = library / ".app-runtime.json"
    instance = FileLock(library / ".app.lock", "Tuto knihovnu už používá jiné spuštění aplikace.")
    try:
        instance.acquire()
    except BlockingIOError:
        # A second ./start.sh just brings the running application back into the browser.
        url = json.loads(runtime.read_text(encoding="utf-8"))["url"] if runtime.is_file() else None
        if url and not args.server:
            webbrowser.open(url)
        parser.exit(0, f"Aplikace už běží: {url or 'adresa není známa'}\n")
    try:
        workflow = Workflow(ProjectStore(library))
        listener = listen_socket(DEFAULT_PORT if args.port is None else args.port, fallback=args.port is None)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/"
        control = Control()
        app = create_api(workflow, control=control)
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        control.server = server
        write_json_atomic(runtime, {"url": url, "pid": os.getpid()})

        def announce():
            while not server.started:
                if server.should_exit:
                    return
                time.sleep(0.05)
            print(f"AI-OCR-maker: {url}\nUkončení: Ctrl+C nebo tlačítko ⏻ v aplikaci.", flush=True)
            if not args.server:
                webbrowser.open(url)
        threading.Thread(target=announce, daemon=True).start()
        try:
            server.run(sockets=[listener])  # handles Ctrl+C and SIGTERM itself
        finally:
            workflow.close(timeout=30)
            listener.close()
            runtime.unlink(missing_ok=True)
    finally:
        instance.release()
