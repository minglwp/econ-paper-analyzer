from __future__ import annotations

import atexit
import errno
import fcntl
import json
import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import IO


APP_NAME = "EconPaperAnalyzer"
DEFAULT_PORT = 8765
HEALTH_TIMEOUT_SECONDS = 20


def _application_directories() -> tuple[Path, Path]:
    if sys.platform == "darwin":
        runtime = Path.home() / "Library" / "Application Support" / APP_NAME
        cache = Path.home() / "Library" / "Caches" / APP_NAME
    else:
        runtime = Path(os.environ.get("EPA_RUNTIME_ROOT", Path.cwd() / ".runtime"))
        cache = Path(os.environ.get("EPA_CACHE_ROOT", runtime / "cache"))
    return runtime, cache


def _prepare_environment() -> tuple[Path, Path]:
    default_runtime, default_cache = _application_directories()
    runtime = Path(os.environ.setdefault("EPA_RUNTIME_ROOT", str(default_runtime))).expanduser()
    cache = Path(os.environ.setdefault("EPA_CACHE_ROOT", str(default_cache))).expanduser()
    run_root = Path(os.environ.setdefault("EPA_RUN_ROOT", str(runtime / "runs"))).expanduser()
    upload_root = Path(
        os.environ.setdefault("EPA_UPLOAD_ROOT", str(cache / "uploads"))
    ).expanduser()
    os.environ.setdefault("MPLCONFIGDIR", str(cache / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache))
    for directory in (runtime, run_root, cache, upload_root, cache / "matplotlib"):
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
    return runtime, cache


def _health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def _browser_enabled() -> bool:
    return os.environ.get("EPA_NO_BROWSER", "").lower() not in {"1", "true", "yes"}


def _healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(_health_url(port), timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _open_when_ready(port: int) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _healthy(port):
            if _browser_enabled():
                webbrowser.open_new_tab(f"http://127.0.0.1:{port}/")
            return
        time.sleep(0.2)
    logging.error("The local service did not become healthy within %s seconds", HEALTH_TIMEOUT_SECONDS)


def _available_port(start: int) -> int:
    for port in range(start, min(start + 100, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    continue
                raise
            return port
    raise RuntimeError("No local port is available for the application")


def _requested_port() -> int:
    raw = os.environ.get("EPA_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("EPA_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("EPA_PORT must be between 1 and 65535")
    return port


def _acquire_instance_lock(runtime: Path) -> tuple[IO[str], bool]:
    lock_path = runtime / "application.lock"
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return lock_file, False
    return lock_file, True


def _read_running_port(runtime: Path) -> int | None:
    state_path = runtime / "application.json"
    try:
        value = int(json.loads(state_path.read_text(encoding="utf-8"))["port"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if 1 <= value <= 65535 else None


def _open_existing_instance(runtime: Path) -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        port = _read_running_port(runtime)
        if port is not None and _healthy(port):
            if _browser_enabled():
                webbrowser.open_new_tab(f"http://127.0.0.1:{port}/")
            return True
        time.sleep(0.2)
    return False


def _write_state(runtime: Path, port: int) -> Path:
    state_path = runtime / "application.json"
    temporary = runtime / "application.json.tmp"
    temporary.write_text(
        json.dumps({"pid": os.getpid(), "port": port}, ensure_ascii=True),
        encoding="utf-8",
    )
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(state_path)
    return state_path


def _show_startup_error(log_path: Path) -> None:
    if sys.platform != "darwin":
        return
    script = (
        "on run argv\n"
        'display alert "EconPaperAnalyzer could not start" '
        'message ("See the log file: " & item 1 of argv) as critical\n'
        "end run"
    )
    try:
        subprocess.run(["osascript", "-e", script, str(log_path)], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    runtime, _ = _prepare_environment()
    log_path = runtime / "launcher.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    lock_file, owns_lock = _acquire_instance_lock(runtime)
    if not owns_lock:
        try:
            if not _open_existing_instance(runtime):
                logging.error("Another instance holds the lock but did not become healthy")
                _show_startup_error(log_path)
                return 1
            return 0
        finally:
            lock_file.close()

    state_path: Path | None = None
    try:
        port = _available_port(_requested_port())
        state_path = _write_state(runtime, port)
        atexit.register(state_path.unlink, missing_ok=True)

        from app.main import app
        import uvicorn

        logging.info("Starting local service on http://127.0.0.1:%s", port)
        threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            workers=1,
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
            log_config=None,
            access_log=False,
        )
        return 0
    except Exception:
        logging.exception("Application startup failed")
        _show_startup_error(log_path)
        return 1
    finally:
        if state_path is not None:
            state_path.unlink(missing_ok=True)
        lock_file.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
