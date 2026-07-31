from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def test_home_health_and_demo_upload():
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    home = client.get("/")
    assert home.status_code == 200
    assert "经管论文" in home.text
    demo = client.post("/api/demo")
    assert demo.status_code == 200
    payload = demo.json()
    assert payload["rows"] == 320
    assert "suggested_config" in payload
    assert "创新氛围1" in payload["columns"]
    assert payload["quality"]["unique_values"]["性别"] == 2


def test_uppercase_csv_upload_can_be_inspected():
    client = TestClient(app)
    source = Path(__file__).resolve().parents[1] / "examples" / "demo_survey.csv"
    uploaded = client.post(
        "/api/upload",
        content=source.read_bytes(),
        headers={"X-Filename": f"{'A' * 230}.CSV"},
    )

    assert uploaded.status_code == 200
    dataset_id = uploaded.json()["dataset_id"]
    assert uploaded.json()["filename"].endswith(".CSV")
    assert client.get(f"/api/datasets/{dataset_id}").status_code == 200


def test_demo_dataset_can_start_and_complete_job():
    client = TestClient(app)
    demo = client.post("/api/demo").json()
    config = {
        "dataset_id": demo["dataset_id"],
        "scales": demo["suggested_config"]["scales"],
        "roles": demo["suggested_config"]["roles"],
        "analyses": {
            "cfa": False,
            "harman": False,
            "ulmc": False,
            "descriptives": True,
            "regression": True,
            "mediation": False,
            "moderation": False,
            "moderated_mediation": False,
            "moderated_stage": "first",
            "correlation": "pearson",
        },
        "inference": {"bootstrap_samples": 200, "confidence_interval": "percentile"},
    }
    started = client.post("/api/analyze", json=config)
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "completed_with_errors", "failed"}:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job
    assert job["result"]["input_rows"] == 320
    preview = job["result"]["in_app_preview"]
    assert preview["描述性统计"]
    assert preview["相关分析"]
    assert preview["回归分析"]["模型摘要"]
    assert preview["回归分析"]["关键回归系数"]
    assert any(artifact["name"] == "report.html" for artifact in job["artifacts"])
    recovered = client.get(f"/api/runs/{run_id}")
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "completed"
    assert any(
        artifact["name"] == "tables.xlsx"
        for artifact in recovered.json()["artifacts"]
    )


def test_module_failure_is_reported_as_partial_completion():
    client = TestClient(app)
    demo = client.post("/api/demo").json()
    config = {
        "dataset_id": demo["dataset_id"],
        "scales": [demo["suggested_config"]["scales"][0]],
        "roles": {"x": "年龄", "y": "创新绩效1", "controls": ["性别"]},
        "analyses": {
            "cfa": True,
            "harman": False,
            "ulmc": False,
            "descriptives": True,
            "regression": False,
            "mediation": False,
            "moderation": False,
            "moderated_mediation": False,
        },
        "inference": {"bootstrap_samples": 200},
    }
    started = client.post("/api/analyze", json=config)
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "completed_with_errors", "failed"}:
            break
        time.sleep(0.1)

    assert job["status"] == "completed_with_errors", job
    assert "cfa" in job["result"]["failed_modules"]
    assert job["result"]["errors"]


def test_run_recovery_requires_completion_marker(tmp_path, monkeypatch):
    run_id = "run-aaaaaaaaaaaa"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "results.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_module, "RUN_ROOT", tmp_path)

    response = TestClient(app).get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "重新运行" in response.json()["error"]
