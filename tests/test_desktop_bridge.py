from __future__ import annotations

import base64
import inspect
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.desktop_bridge as desktop_bridge
import app.main as main
import desktop
from app.config import RESOURCE_ROOT


class FakeWindow:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[object, dict[str, object]]] = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append((dialog_type, kwargs))
        return self.responses.pop(0) if self.responses else None


def recovery_snapshot() -> dict[str, object]:
    return {
        "format": "econ-paper-analyzer/recovery",
        "version": 1,
        "saved_at": "2026-08-01T08:30:00.000Z",
        "dataset": {
            "id": "dataset-0123456789",
            "filename": "survey.csv",
            "sheet_name": None,
        },
        "current_step": "analysis",
        "settings": {
            "format": "econ-paper-analyzer/settings",
            "version": 1,
            "source": {"filename": "survey.csv", "sheet_name": None},
            "configuration": {
                "sheet_name": None,
                "scales": [],
                "models": [],
                "analyses": {"cfa": True},
                "inference": {"alpha": 0.05},
            },
        },
        "job": {"job_id": "job-012345", "run_id": ""},
    }


def test_desktop_bridge_imports_csv_without_http(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(desktop_bridge, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path)
    content = "x,y\n1,2\n3,4\n".encode("utf-8")

    response = desktop_bridge.DesktopBridge().upload_file(
        "survey.csv", base64.b64encode(content).decode("ascii")
    )

    assert response["filename"] == "survey.csv"
    assert response["native_file_size"] == len(content)
    assert response["dataset_id"]
    assert list(tmp_path.glob("*_upload.csv"))


def test_desktop_bridge_loads_demo_without_http(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(desktop_bridge, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path)

    response = desktop_bridge.DesktopBridge().load_demo()

    assert response["rows"] == 320
    assert response["columns_count"] == 15
    assert len(response["suggested_config"]["scales"]) == 4
    assert len(response["suggested_config"]["models"]) == 4
    assert (tmp_path / f"{response['dataset_id']}.json").is_file()
    assert list(tmp_path.glob(f"{response['dataset_id']}_upload.csv"))


def test_desktop_bridge_exposes_only_expected_public_methods() -> None:
    bridge = desktop_bridge.DesktopBridge()
    public_callables = {
        name
        for name, value in inspect.getmembers(bridge, callable)
        if not name.startswith("_")
    }

    assert public_callables == {
        "analyze",
        "choose_dataset_file",
        "clear_recovery_snapshot",
        "get_dataset",
        "get_job",
        "get_run",
        "load_recovery_snapshot",
        "load_demo",
        "log_frontend_event",
        "save_artifact",
        "save_recovery_snapshot",
        "save_settings",
        "upload_file",
    }
    assert not hasattr(bridge, "window")
    assert not hasattr(bridge, "set_window")
    window = FakeWindow()
    bridge._set_window(window)
    assert bridge._require_window() is window


def test_desktop_file_dialog_imports_and_cancels(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.csv"
    source.write_text("x,y\n1,2\n", encoding="utf-8")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(desktop_bridge, "UPLOAD_ROOT", uploads)
    monkeypatch.setattr(main, "UPLOAD_ROOT", uploads)
    window = FakeWindow([(str(source),), None])
    bridge = desktop_bridge.DesktopBridge()
    bridge._set_window(window)

    imported = bridge.choose_dataset_file()

    assert imported is not None
    assert imported["filename"] == "source.csv"
    assert imported["native_file_size"] == source.stat().st_size
    assert bridge.choose_dataset_file() is None


def test_desktop_save_dialogs_copy_artifact_and_settings(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "tables.xlsx"
    source.write_bytes(b"result")
    artifact_target = tmp_path / "saved-tables.xlsx"
    settings_target = tmp_path / "settings.json"
    window = FakeWindow([(str(artifact_target),), (str(settings_target),)])
    bridge = desktop_bridge.DesktopBridge()
    bridge._set_window(window)
    monkeypatch.setattr(
        desktop_bridge.DesktopBridge,
        "_artifact_path",
        staticmethod(lambda _run_id, _artifact_name: source),
    )

    artifact = bridge.save_artifact("run-1", "tables.xlsx")
    settings = bridge.save_settings("settings.json", '{"ok": true}')

    assert artifact == {"saved": True, "filename": artifact_target.name, "path": str(artifact_target)}
    assert artifact_target.read_bytes() == b"result"
    assert settings == {"saved": True, "filename": settings_target.name, "path": str(settings_target)}
    assert settings_target.read_text(encoding="utf-8") == '{"ok": true}'


def test_desktop_save_dialogs_accept_cocoa_string_paths(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "tables.xlsx"
    source.write_bytes(b"result")
    artifact_target = tmp_path / "saved-tables.xlsx"
    settings_target = tmp_path / "settings.json"
    window = FakeWindow([str(artifact_target), str(settings_target)])
    bridge = desktop_bridge.DesktopBridge()
    bridge._set_window(window)
    monkeypatch.setattr(
        desktop_bridge.DesktopBridge,
        "_artifact_path",
        staticmethod(lambda _run_id, _artifact_name: source),
    )

    assert bridge.save_artifact("run-1", "tables.xlsx")["saved"] is True
    assert bridge.save_settings("settings.json", '{"ok": true}')["saved"] is True
    assert artifact_target.read_bytes() == b"result"
    assert settings_target.read_text(encoding="utf-8") == '{"ok": true}'


def test_desktop_save_dialog_cancellation_is_not_an_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "tables.xlsx"
    source.write_bytes(b"result")
    bridge = desktop_bridge.DesktopBridge()
    bridge._set_window(FakeWindow([None, None]))
    monkeypatch.setattr(
        desktop_bridge.DesktopBridge,
        "_artifact_path",
        staticmethod(lambda _run_id, _artifact_name: source),
    )

    assert bridge.save_artifact("run-1", "tables.xlsx") == {"saved": False}
    assert bridge.save_settings("settings.json", '{"ok": true}') == {"saved": False}


def test_desktop_recovery_snapshot_is_atomic_and_clearable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(desktop_bridge, "RUNTIME_ROOT", tmp_path)
    bridge = desktop_bridge.DesktopBridge()
    snapshot = recovery_snapshot()

    result = bridge.save_recovery_snapshot(snapshot)
    stored = tmp_path / "recovery" / "latest.json"

    assert result == {"saved": True, "saved_at": snapshot["saved_at"]}
    assert stored.is_file()
    assert bridge.load_recovery_snapshot() == snapshot
    assert not list((tmp_path / "recovery").glob("*.tmp"))
    assert bridge.clear_recovery_snapshot() == {"cleared": True}
    assert bridge.load_recovery_snapshot() is None


def test_desktop_recovery_snapshot_rejects_invalid_or_oversize_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(desktop_bridge, "RUNTIME_ROOT", tmp_path)
    bridge = desktop_bridge.DesktopBridge()

    with pytest.raises(ValueError, match="格式"):
        bridge.save_recovery_snapshot({})

    oversized = recovery_snapshot()
    oversized["settings"] = {
        **oversized["settings"],
        "configuration": {"payload": "x" * (2 * 1024 * 1024)},
    }
    with pytest.raises(ValueError, match="2 MB"):
        bridge.save_recovery_snapshot(oversized)

    recovery_file = tmp_path / "recovery" / "latest.json"
    recovery_file.parent.mkdir(parents=True)
    recovery_file.write_text("not-json", encoding="utf-8")
    assert bridge.load_recovery_snapshot() is None


def test_desktop_archives_latest_recovery_snapshot_on_close(tmp_path: Path) -> None:
    latest = tmp_path / "recovery" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text('{"workspace": true}', encoding="utf-8")

    backup = desktop._archive_recovery_snapshot(tmp_path)

    assert backup is not None
    assert backup.parent == tmp_path / "backups"
    assert backup.name.startswith("analysis-backup-")
    assert backup.read_text(encoding="utf-8") == latest.read_text(encoding="utf-8")


def test_desktop_bridge_translates_backend_validation_errors() -> None:
    def fail() -> dict[str, object]:
        raise HTTPException(status_code=422, detail=[{"msg": "字段无效"}])

    with pytest.raises(ValueError, match="字段无效"):
        desktop_bridge.DesktopBridge._translate_http_call(fail)
    with pytest.raises(ValueError, match="Input should be a valid dictionary"):
        desktop_bridge.DesktopBridge().analyze(None)


def test_desktop_artifacts_are_addressed_by_run_and_filename() -> None:
    payload = {
        "run_id": "run-0123456789ab",
        "artifacts": [{"name": "tables.xlsx", "url": "/api/runs/example"}],
    }

    decorated = desktop_bridge.DesktopBridge._decorate_artifacts(payload)

    assert decorated["artifacts"] == [{
        "name": "tables.xlsx",
        "url": "desktop-artifact://run-0123456789ab/tables.xlsx",
        "native": True,
        "run_id": "run-0123456789ab",
        "artifact_name": "tables.xlsx",
    }]


def test_frontend_event_logging_sanitizes_and_limits(caplog) -> None:
    bridge = desktop_bridge.DesktopBridge()
    caplog.set_level(logging.INFO)
    home = str(Path.home())
    payload = {
        "event": "window.onerror",
        "occurred_at": "2026-07-31T12:00:00Z",
        "error_name": "TypeError",
        "message": "Oops\nforged " + ("x" * 1100),
        "source": f"file://{home}/secret/app.js?token=secret#fragment",
        "line": 12,
        "column": 7,
        "stack": f"at {home}/secret/app.js:12:7",
        "bridge_available": True,
        "dataset": "must-not-be-logged",
    }

    assert bridge.log_frontend_event(payload) == {"accepted": True}
    assert bridge.log_frontend_event(payload) == {"accepted": False}
    assert bridge.log_frontend_event([payload]) == {"accepted": False}
    assert bridge.log_frontend_event({"event": "unknown"}) == {"accepted": False}

    message = next(record.getMessage() for record in caplog.records if record.message.startswith("Frontend event"))
    assert "Oops forged" in message
    assert "\n" not in message
    assert "<home>/secret/app.js" in message
    assert "token=secret" not in message
    assert "must-not-be-logged" not in message
    assert home not in message
    assert len(desktop_bridge.DesktopBridge._sanitize_frontend_event(payload)["message"]) == 1000

    bridge._frontend_log_count = 200
    assert bridge.log_frontend_event({"event": "pywebviewready", "message": "ready"}) == {"accepted": False}


def test_desktop_html_inlines_static_resources_and_bootstrap() -> None:
    html = desktop._desktop_html()
    css = (RESOURCE_ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    script = (RESOURCE_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'href="/static/app.css"' not in html
    assert 'src="/static/app.js"' not in html
    assert "file://" not in html
    assert f'<style data-desktop-resource="app.css">\n{css}\n</style>' in html
    assert f'<script data-desktop-resource="app.js">\n{script}\n</script>' in html
    assert html.index("data-desktop-bootstrap") < html.index('data-desktop-resource="app.js"')
    assert 'Object.defineProperty(window, "__EPA_RUNTIME__"' in html
    assert 'window.addEventListener("error"' in html
    assert 'window.addEventListener("unhandledrejection"' in html
    assert 'window.addEventListener("pywebviewready"' in html


def test_desktop_html_includes_workspace_recovery_controls() -> None:
    html = desktop._desktop_html()

    assert 'id="draftStatus"' in html
    assert 'id="recoveryNotice"' in html
    assert "新建分析" in html
    assert "save_recovery_snapshot" in html
    assert "load_recovery_snapshot" in html
    assert "clear_recovery_snapshot" in html


def test_desktop_removes_windows_download_zone_identifier(tmp_path: Path) -> None:
    runtime_dll = tmp_path / "Python.Runtime.dll"
    runtime_dll.write_bytes(b"runtime")
    zone_identifier = tmp_path / "Python.Runtime.dll:Zone.Identifier"
    zone_identifier.write_text("[ZoneTransfer]\nZoneId=3\n", encoding="utf-8")

    assert desktop._remove_windows_zone_identifier(runtime_dll) is True
    assert not zone_identifier.exists()
