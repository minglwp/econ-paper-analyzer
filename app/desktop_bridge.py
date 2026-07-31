from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from pydantic import ValidationError

from .config import ALLOWED_SUFFIXES, MAX_UPLOAD_BYTES, UPLOAD_ROOT
from .data import write_json
from .main import _dataset_response, _run_directory, job_status, load_demo_dataset, run_status, start_analysis
from .schemas import AnalysisRequest

if TYPE_CHECKING:
    from webview import Window


class DesktopBridge:
    """Expose the analysis service directly to the packaged WebKit window."""

    def __init__(self) -> None:
        self._window: Window | None = None
        self._frontend_log_lock = threading.Lock()
        self._frontend_log_count = 0
        self._frontend_log_limit_reported = False
        self._frontend_log_fingerprints: set[tuple[str, str, int, int]] = set()

    def _set_window(self, window: Window) -> None:
        self._window = window

    def choose_dataset_file(self) -> dict[str, Any] | None:
        window = self._require_window()
        from webview import FileDialog

        paths = window.create_file_dialog(
            FileDialog.OPEN,
            allow_multiple=False,
            file_types=("Data files (*.csv;*.xlsx)",),
        )
        if not paths:
            return None
        return self._import_path(Path(paths[0]))

    def upload_file(self, filename: str, encoded_content: str) -> dict[str, Any]:
        safe_name = self._safe_filename(filename)
        if not isinstance(encoded_content, str):
            raise ValueError("上传内容格式不正确")
        if len(encoded_content) > (MAX_UPLOAD_BYTES * 4 // 3) + 8:
            raise ValueError("文件超过 100 MB 限制")
        try:
            content = base64.b64decode(encoded_content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("上传内容无法读取") from exc
        if not content:
            raise ValueError("上传文件为空")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文件超过 100 MB 限制")

        target = UPLOAD_ROOT / f"{uuid.uuid4().hex}_upload{Path(safe_name).suffix.lower()}"
        target.write_bytes(content)
        try:
            target.chmod(0o600)
            return self._dataset_payload(target, safe_name)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def load_demo(self) -> dict[str, Any]:
        return self._translate_http_call(load_demo_dataset)

    def get_dataset(self, dataset_id: str, sheet_name: str | None = None) -> dict[str, Any]:
        from .main import _dataset_path

        def inspect() -> dict[str, Any]:
            return _dataset_response(dataset_id, _dataset_path(dataset_id), sheet_name)

        return self._translate_http_call(inspect)

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = AnalysisRequest.model_validate(payload)
        except ValidationError as exc:
            messages = [item.get("msg", "配置无效") for item in exc.errors()]
            raise ValueError("；".join(str(message) for message in messages)) from exc
        return self._translate_http_call(start_analysis, request)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._decorate_artifacts(self._translate_http_call(job_status, job_id))

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._decorate_artifacts(self._translate_http_call(run_status, run_id))

    def save_artifact(self, run_id: str, artifact_name: str) -> dict[str, Any]:
        source = self._artifact_path(run_id, artifact_name)
        window = self._require_window()
        from webview import FileDialog

        destination = window.create_file_dialog(FileDialog.SAVE, save_filename=source.name)
        target = self._selected_dialog_path(destination)
        if target is None:
            return {"saved": False}
        shutil.copy2(source, target)
        return {"saved": True, "filename": target.name, "path": str(target)}

    def save_settings(self, filename: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("设置文件内容无效")
        safe_name = Path(str(filename)).name
        if not safe_name.endswith(".json"):
            safe_name = "analysis-settings.json"
        window = self._require_window()
        from webview import FileDialog

        destination = window.create_file_dialog(FileDialog.SAVE, save_filename=safe_name)
        target = self._selected_dialog_path(destination)
        if target is None:
            return {"saved": False}
        target.write_text(content, encoding="utf-8")
        return {"saved": True, "filename": target.name, "path": str(target)}

    def log_frontend_event(self, payload: Any) -> dict[str, bool]:
        try:
            event = self._sanitize_frontend_event(payload)
            if event is None:
                return {"accepted": False}
            fingerprint = (
                event["event"],
                event["message"],
                event["line"],
                event["column"],
            )
            with self._frontend_log_lock:
                if fingerprint in self._frontend_log_fingerprints:
                    return {"accepted": False}
                if self._frontend_log_count >= 200:
                    if not self._frontend_log_limit_reported:
                        logging.warning("Frontend event limit reached; further events are suppressed")
                        self._frontend_log_limit_reported = True
                    return {"accepted": False}
                self._frontend_log_fingerprints.add(fingerprint)
                self._frontend_log_count += 1
            logging.info(
                "Frontend event %s",
                json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            )
            return {"accepted": True}
        except Exception:
            return {"accepted": False}

    @classmethod
    def _sanitize_frontend_event(cls, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        event_name = payload.get("event")
        if event_name not in {"window.onerror", "unhandledrejection", "pywebviewready"}:
            return None
        return {
            "schema": "econ-paper-analyzer/frontend-log",
            "version": 1,
            "event": event_name,
            "occurred_at": cls._clean_frontend_text(payload.get("occurred_at"), 64),
            "error_name": cls._clean_frontend_text(payload.get("error_name"), 128),
            "message": cls._clean_frontend_text(payload.get("message"), 1000),
            "source": cls._clean_frontend_source(payload.get("source")),
            "line": cls._bounded_frontend_integer(payload.get("line")),
            "column": cls._bounded_frontend_integer(payload.get("column")),
            "stack": cls._clean_frontend_text(payload.get("stack"), 8192),
            "bridge_available": payload.get("bridge_available") is True,
        }

    @staticmethod
    def _clean_frontend_text(value: Any, limit: int) -> str:
        if not isinstance(value, (str, int, float, bool)):
            return ""
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
        home = str(Path.home())
        if home:
            text = text.replace(home, "<home>")
        return text[:limit]

    @classmethod
    def _clean_frontend_source(cls, value: Any) -> str:
        source = cls._clean_frontend_text(value, 512)
        if not source:
            return ""
        try:
            parsed = urlsplit(source)
        except ValueError:
            return source
        if parsed.scheme:
            source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return cls._clean_frontend_text(source, 512)

    @staticmethod
    def _bounded_frontend_integer(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return min(max(number, 0), 10_000_000)

    def _import_path(self, source: Path) -> dict[str, Any]:
        safe_name = self._safe_filename(source.name)
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise ValueError("无法读取所选文件") from exc
        if size > MAX_UPLOAD_BYTES:
            raise ValueError("文件超过 100 MB 限制")
        target = UPLOAD_ROOT / f"{uuid.uuid4().hex}_upload{source.suffix.lower()}"
        try:
            shutil.copyfile(source, target)
            target.chmod(0o600)
            return self._dataset_payload(target, safe_name, size)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def _dataset_payload(self, path: Path, filename: str, size: int | None = None) -> dict[str, Any]:
        dataset_id = path.name.split("_", 1)[0]
        payload = self._translate_http_call(_dataset_response, dataset_id, path, None, filename)
        metadata_path = UPLOAD_ROOT / f"{dataset_id}.json"
        write_json(metadata_path, {"dataset_id": dataset_id, "filename": filename, "stored_name": path.name})
        metadata_path.chmod(0o600)
        payload["native_file_size"] = size if size is not None else path.stat().st_size
        return payload

    @staticmethod
    def _safe_filename(value: str) -> str:
        filename = Path(str(value)).name
        if not filename or Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError("仅支持 CSV 与 XLSX 文件")
        return filename

    @staticmethod
    def _selected_dialog_path(selection: Any) -> Path | None:
        """Normalize pywebview's platform-specific save-dialog return values.

        Cocoa returns a string for an NSSavePanel, while other backends return a
        one-element sequence. Treat an empty response as an ordinary cancellation.
        """
        if not selection:
            return None
        if isinstance(selection, (str, Path)):
            selected = selection
        elif isinstance(selection, (tuple, list)) and len(selection) == 1:
            selected = selection[0]
        else:
            raise ValueError("保存位置无效")
        if not isinstance(selected, (str, Path)) or not str(selected).strip():
            raise ValueError("保存位置无效")
        return Path(selected).expanduser()

    @staticmethod
    def _translate_http_call(function: Any, *args: Any) -> dict[str, Any]:
        try:
            return function(*args)
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, list):
                message = "；".join(str(item.get("msg", item)) for item in detail)
            else:
                message = str(detail)
            raise ValueError(message) from exc

    @staticmethod
    def _artifact_path(run_id: str, artifact_name: str) -> Path:
        run_dir = _run_directory(run_id)
        path = (run_dir / Path(artifact_name).name).resolve()
        if path.parent != run_dir or not path.is_file():
            raise ValueError("结果文件不存在")
        return path

    @staticmethod
    def _decorate_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("run_id") or "")
        artifacts = payload.get("artifacts")
        if not run_id or not isinstance(artifacts, list):
            return payload
        decorated = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            name = Path(str(artifact.get("name", ""))).name
            if name:
                decorated.append({
                    **artifact,
                    "url": f"desktop-artifact://{run_id}/{name}",
                    "native": True,
                    "run_id": run_id,
                    "artifact_name": name,
                })
        return {**payload, "artifacts": decorated}

    def _require_window(self) -> Window:
        if self._window is None:
            raise RuntimeError("桌面窗口尚未准备完成")
        return self._window
