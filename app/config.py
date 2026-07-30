from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = (
    Path(getattr(sys, "_MEIPASS"))
    if IS_FROZEN and getattr(sys, "_MEIPASS", None)
    else Path(__file__).resolve().parents[1]
)
PROJECT_ROOT = RESOURCE_ROOT


def _default_runtime_root() -> Path:
    if not IS_FROZEN:
        return PROJECT_ROOT / ".runtime"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "EconPaperAnalyzer"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "EconPaperAnalyzer"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "econ-paper-analyzer"


RUNTIME_ROOT = Path(
    os.environ.get("EPA_RUNTIME_ROOT", str(_default_runtime_root()))
).expanduser()


def _default_cache_root() -> Path:
    if IS_FROZEN and sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "EconPaperAnalyzer"
    if IS_FROZEN and sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "EconPaperAnalyzer" / "Cache"
    if IS_FROZEN:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return base / "econ-paper-analyzer"
    return RUNTIME_ROOT / "cache"


CACHE_ROOT = Path(
    os.environ.get("EPA_CACHE_ROOT", str(_default_cache_root()))
).expanduser()
UPLOAD_ROOT = Path(
    os.environ.get(
        "EPA_UPLOAD_ROOT",
        str(Path(tempfile.gettempdir()) / "econ-paper-analyzer" / "uploads"),
    )
).expanduser()
RUN_ROOT = Path(
    os.environ.get("EPA_RUN_ROOT", str(RUNTIME_ROOT / "runs"))
).expanduser()

os.environ.setdefault("MPLCONFIGDIR", str(CACHE_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_ROOT))

for directory in (RUNTIME_ROOT, UPLOAD_ROOT, RUN_ROOT, CACHE_ROOT, CACHE_ROOT / "matplotlib"):
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_DATA_ROWS = 200_000
MAX_DATA_COLUMNS = 2_000
MAX_DATA_CELLS = 5_000_000
MAX_XLSX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ACTIVE_JOBS = 3
MAX_RETAINED_JOBS = 100
ALLOWED_SUFFIXES = {".csv", ".xlsx"}
