from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from app.runner import run_full_analysis
from app.schemas import AnalysisRequest


def test_multi_model_outputs_are_grouped_and_keep_each_plot(
    tmp_path: Path, demo_request: AnalysisRequest
) -> None:
    payload = demo_request.model_dump(mode="json")
    payload.pop("roles", None)
    payload["models"] = [
        {
            "name": "领导支持的调节路径",
            "analysis": "moderation",
            "x": "创新氛围",
            "y": "创新绩效",
            "moderator": "领导支持",
            "controls": ["年龄"],
        },
        {
            "name": "工作投入的中介路径",
            "analysis": "mediation",
            "x": "创新氛围",
            "y": "创新绩效",
            "mediator": "工作投入",
            "controls": ["性别"],
        },
    ]
    payload["analyses"] = {
        "cfa": False,
        "harman": False,
        "ulmc": False,
        "descriptives": True,
        "regression": False,
        "mediation": False,
        "moderation": False,
        "moderated_mediation": False,
        "correlation": "pearson",
    }
    payload["inference"]["bootstrap_samples"] = 200
    payload["inference"]["confidence_interval"] = "percentile"
    request = AnalysisRequest.model_validate(payload)
    project_root = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "run-multi-report"

    summary, artifacts = run_full_analysis(
        project_root / "examples" / "demo_survey.csv",
        request,
        "run-multi-report",
        run_dir,
    )

    assert summary["completed_models"] == ["model-01", "model-02"]
    artifact_names = {artifact["name"] for artifact in artifacts}
    assert {
        "model-01_moderation_plot.png",
        "model-01_moderation_plot.svg",
    } <= artifact_names
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in results["path_models"]] == [
        "领导支持的调节路径",
        "工作投入的中介路径",
    ]
    report = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "路径 1：领导支持的调节路径" in report
    assert "路径 2：工作投入的中介路径" in report
    assert 'src="model-01_moderation_plot.png"' in report
    workbook = pd.ExcelFile(run_dir / "tables.xlsx")
    assert {
        "路径模型清单",
        "路径模型摘要",
        "路径回归系数",
        "路径效应检验",
        "路径简单斜率",
    } <= set(workbook.sheet_names)
    with zipfile.ZipFile(run_dir / "analysis_bundle.zip") as archive:
        assert "model-01_moderation_plot.png" in archive.namelist()
