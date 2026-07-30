from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


def test_api_runs_a_user_selected_path_list() -> None:
    client = TestClient(app)
    demo = client.post("/api/demo").json()
    payload = {
        "dataset_id": demo["dataset_id"],
        "scales": [],
        "models": [
            {
                "name": "人口统计控制路径",
                "analysis": "regression",
                "x": "年龄",
                "y": "创新绩效1",
                "controls": ["性别"],
            },
            {
                "name": "题项层路径",
                "analysis": "regression",
                "x": "创新氛围1",
                "y": "创新绩效2",
                "controls": ["年龄"],
            },
        ],
        "analyses": {
            "cfa": False,
            "harman": False,
            "ulmc": False,
            "descriptives": False,
            "regression": False,
            "mediation": False,
            "moderation": False,
            "moderated_mediation": False,
        },
        "inference": {
            "bootstrap_samples": 200,
            "confidence_interval": "percentile",
        },
    }

    started = client.post("/api/analyze", json=payload)
    assert started.status_code == 202
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{started.json()['job_id']}").json()
        if job["status"] in {"completed", "completed_with_errors", "failed"}:
            break
        time.sleep(0.05)

    assert job["status"] == "completed", job
    assert [row["name"] for row in job["result"]["path_models"]] == [
        "人口统计控制路径",
        "题项层路径",
    ]
    assert job["result"]["completed_models"] == ["model-01", "model-02"]
