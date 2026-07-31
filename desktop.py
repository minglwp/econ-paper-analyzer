from __future__ import annotations

import atexit
import logging
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from typing import IO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


APP_NAME = "EconPaperAnalyzer"
DESKTOP_BOOTSTRAP = r"""(() => {
  "use strict";
  Object.defineProperty(window, "__EPA_RUNTIME__", { value: "desktop", configurable: false, writable: false });
  const pending = [];
  const seen = new Set();
  let bridgeReady = false;
  let sending = false;

  function text(value, limit) {
    if (typeof value === "string") return value.slice(0, limit);
    if (typeof value === "number" || typeof value === "boolean") return String(value).slice(0, limit);
    return "";
  }

  function describe(reason) {
    if (reason instanceof Error) {
      return { error_name: text(reason.name, 128), message: text(reason.message, 1000), stack: text(reason.stack, 8192) };
    }
    return { error_name: typeof reason, message: text(reason, 1000), stack: "" };
  }

  async function flush() {
    if (!bridgeReady || sending || !pending.length) return;
    const api = window.pywebview?.api;
    if (!api || typeof api.log_frontend_event !== "function") return;
    sending = true;
    try {
      while (pending.length) {
        const payload = pending.shift();
        try {
          await api.log_frontend_event(payload);
        } catch (_error) {
          break;
        }
      }
    } finally {
      sending = false;
    }
  }

  function record(payload) {
    const fingerprint = [payload.event, payload.message, payload.source, payload.line, payload.column].join("|");
    if (seen.has(fingerprint)) return;
    seen.add(fingerprint);
    if (seen.size > 64) seen.delete(seen.values().next().value);
    if (pending.length >= 32) pending.shift();
    pending.push({
      occurred_at: new Date().toISOString(),
      bridge_available: Boolean(window.pywebview?.api),
      ...payload,
    });
    void flush();
  }

  window.addEventListener("error", (event) => {
    const details = describe(event.error);
    record({
      event: "window.onerror",
      error_name: details.error_name,
      message: text(event.message, 1000) || details.message,
      source: text(event.filename, 512),
      line: Number(event.lineno) || 0,
      column: Number(event.colno) || 0,
      stack: details.stack,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    const details = describe(event.reason);
    record({ event: "unhandledrejection", ...details, source: "", line: 0, column: 0 });
  });

  function markBridgeReady() {
    if (bridgeReady) return;
    bridgeReady = true;
    record({ event: "pywebviewready", error_name: "", message: "Desktop bridge ready", source: "", line: 0, column: 0, stack: "" });
    void flush();
  }

  window.addEventListener("pywebviewready", markBridgeReady, { once: true });
  if (window.pywebview?.api?.log_frontend_event) markBridgeReady();
})();"""


def _application_directories() -> tuple[Path, Path]:
    if sys.platform == "darwin":
        runtime = Path.home() / "Library" / "Application Support" / APP_NAME
        cache = Path.home() / "Library" / "Caches" / APP_NAME
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
        runtime = root / "runtime"
        cache = root / "cache"
    else:
        runtime = Path(os.environ.get("EPA_RUNTIME_ROOT", Path.cwd() / ".runtime"))
        cache = Path(os.environ.get("EPA_CACHE_ROOT", runtime / "cache"))
    return runtime, cache


def _prepare_environment() -> tuple[Path, Path]:
    default_runtime, default_cache = _application_directories()
    runtime = Path(os.environ.setdefault("EPA_RUNTIME_ROOT", str(default_runtime))).expanduser()
    cache = Path(os.environ.setdefault("EPA_CACHE_ROOT", str(default_cache))).expanduser()
    run_root = Path(os.environ.setdefault("EPA_RUN_ROOT", str(runtime / "runs"))).expanduser()
    upload_root = Path(os.environ.setdefault("EPA_UPLOAD_ROOT", str(cache / "uploads"))).expanduser()
    os.environ.setdefault("MPLCONFIGDIR", str(cache / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache))
    for directory in (runtime, run_root, cache, upload_root, cache / "matplotlib"):
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
    return runtime, cache


def _acquire_instance_lock(runtime: Path) -> tuple[IO[str], bool]:
    # The former browser-and-server edition owns application.lock while it runs.
    # Keep the embedded desktop instance independent so an old edition cannot block it.
    lock_file = (runtime / "embedded-window.lock").open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.seek(0)
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return lock_file, False
    return lock_file, True


def _remove_windows_zone_identifier(path: Path) -> bool:
    """Remove Windows' download-zone marker from a bundled runtime file."""
    zone_identifier = Path(f"{path}:Zone.Identifier")
    if not zone_identifier.exists():
        return False
    try:
        zone_identifier.unlink()
        return True
    except OSError:
        return False


def _unblock_windows_pythonnet_runtime() -> bool:
    if os.name != "nt":
        return False

    application_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    runtime_dll = application_root / "pythonnet" / "runtime" / "Python.Runtime.dll"
    if not runtime_dll.is_file():
        return False
    return _remove_windows_zone_identifier(runtime_dll)


def _show_startup_error(log_path: Path, error: BaseException | None = None) -> None:
    message = f"EconPaperAnalyzer could not start.\n\nSee the log file:\n{log_path}"
    if error and "Python.Runtime.Loader.Initialize" in str(error):
        message = (
            "EconPaperAnalyzer 无法加载 Windows 运行组件。\n\n"
            "请优先使用 GitHub Release 中的 -setup.exe 安装版，而不是从微信或 ZIP 解压后直接运行。"
            "若仍使用 ZIP，请在 ZIP 文件属性中选择“解除锁定”，再重新解压。\n\n"
            f"日志文件：\n{log_path}"
        )
    try:
        if sys.platform == "darwin":
            script = (
                "on run argv\n"
                'display alert "EconPaperAnalyzer could not start" '
                'message (item 1 of argv) as critical\n'
                "end run"
            )
            subprocess.run(["osascript", "-e", script, message], check=False, timeout=10)
        elif os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                message,
                "EconPaperAnalyzer",
                0x10,
            )
    except (OSError, subprocess.SubprocessError):
        pass


def _desktop_html() -> str:
    from app.config import RESOURCE_ROOT

    template_path = RESOURCE_ROOT / "app" / "templates" / "index.html"
    css_path = RESOURCE_ROOT / "app" / "static" / "app.css"
    script_path = RESOURCE_ROOT / "app" / "static" / "app.js"
    source = template_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8")
    script = script_path.read_text(encoding="utf-8")
    stylesheet_tag = '<link rel="stylesheet" href="/static/app.css">'
    script_tag = '<script src="/static/app.js" defer></script>'

    if source.count(stylesheet_tag) != 1 or source.count(script_tag) != 1:
        raise RuntimeError("桌面界面资源标签与模板不匹配")
    if "</style" in css.lower() or "</script" in script.lower() or "</script" in DESKTOP_BOOTSTRAP.lower():
        raise RuntimeError("桌面界面资源包含不安全的内联结束标签")

    source = source.replace(stylesheet_tag, f'<style data-desktop-resource="app.css">\n{css}\n</style>')
    source = source.replace(
        script_tag,
        '<script data-desktop-bootstrap>\n'
        f"{DESKTOP_BOOTSTRAP}\n"
        "</script>\n"
        '<script data-desktop-resource="app.js">\n'
        f"{script}\n"
        "</script>",
    )
    if "/static/app.css" in source or "/static/app.js" in source or "file://" in source:
        raise RuntimeError("桌面界面资源未正确内联")
    return source


def main() -> int:
    runtime, cache = _prepare_environment()
    log_path = runtime / "launcher.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if _unblock_windows_pythonnet_runtime():
        logging.info("Removed the Windows download-zone marker from Python.Runtime.dll")
    lock_file, owns_lock = _acquire_instance_lock(runtime)
    if not owns_lock:
        logging.info("An application window is already open")
        lock_file.close()
        return 0

    try:
        import webview

        from app.desktop_bridge import DesktopBridge

        bridge = DesktopBridge()
        window = webview.create_window(
            "经管论文数据处理器",
            html=_desktop_html(),
            js_api=bridge,
            width=1440,
            height=960,
            min_size=(1080, 720),
            text_select=True,
            zoomable=True,
            background_color="#ffffff",
        )
        if window is None:
            raise RuntimeError("无法创建桌面窗口")
        bridge._set_window(window)
        logging.info("Starting embedded desktop window without an HTTP service")
        atexit.register(lock_file.close)
        webview.start(
            private_mode=True,
            http_server=False,
            storage_path=str(cache / "webview"),
        )
        return 0
    except Exception as error:
        logging.exception("Application startup failed")
        _show_startup_error(log_path, error)
        return 1
    finally:
        lock_file.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
