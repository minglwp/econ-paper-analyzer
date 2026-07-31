from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.effects_analysis import analysis_variable_names, prepare_analysis_data
from app.runner import run_full_analysis
from app.schemas import AnalysisRequest


DISABLED_ANALYSES = {
    "cfa": False,
    "harman": False,
    "ulmc": False,
    "descriptives": False,
    "regression": False,
    "mediation": False,
    "moderation": False,
    "moderated_mediation": False,
}


def test_path_model_schema_validates_types_roles_and_limit():
    request = AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "models": [
                {"name": "直接效应", "analysis": "regression", "x": "x1", "y": "y1"},
                {
                    "name": "中介路径",
                    "analysis": "mediation",
                    "x": "x2",
                    "y": "y2",
                    "mediator": "m2",
                    "controls": ["c1"],
                },
                {
                    "name": "调节路径",
                    "analysis": "moderation",
                    "x": "x3",
                    "y": "y3",
                    "moderator": "w3",
                },
                {
                    "name": "第二阶段被调节中介",
                    "analysis": "moderated_mediation",
                    "x": "x4",
                    "y": "y4",
                    "mediator": "m4",
                    "moderator": "w4",
                    "moderated_stage": "second",
                },
            ],
            "analyses": DISABLED_ANALYSES,
        }
    )

    assert request.roles is None
    assert [model.analysis for model in request.models] == [
        "regression",
        "mediation",
        "moderation",
        "moderated_mediation",
    ]

    with pytest.raises(ValidationError, match="需要指定中介变量 M"):
        AnalysisRequest.model_validate(
            {
                "dataset_id": "0" * 32,
                "models": [
                    {"name": "缺少 M", "analysis": "mediation", "x": "x", "y": "y"}
                ],
                "analyses": DISABLED_ANALYSES,
            }
        )

    with pytest.raises(ValidationError, match="不使用调节变量 W"):
        AnalysisRequest.model_validate(
            {
                "dataset_id": "0" * 32,
                "models": [
                    {
                        "name": "多余 W",
                        "analysis": "regression",
                        "x": "x",
                        "y": "y",
                        "moderator": "w",
                    }
                ],
                "analyses": DISABLED_ANALYSES,
            }
        )

    with pytest.raises(ValidationError, match="List should have at most 20 items"):
        AnalysisRequest.model_validate(
            {
                "dataset_id": "0" * 32,
                "models": [
                    {
                        "name": f"模型 {index}",
                        "analysis": "regression",
                        "x": f"x{index}",
                        "y": f"y{index}",
                    }
                    for index in range(21)
                ],
                "analyses": DISABLED_ANALYSES,
            }
        )


def test_data_preparation_collects_variables_from_every_path_model(demo_frame):
    request = AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "models": [
                {
                    "name": "路径一",
                    "analysis": "regression",
                    "x": "创新氛围1",
                    "y": "创新绩效1",
                    "controls": ["年龄"],
                },
                {
                    "name": "路径二",
                    "analysis": "mediation",
                    "x": "领导支持1",
                    "y": "创新绩效2",
                    "mediator": "工作投入1",
                    "controls": ["性别"],
                },
            ],
            "analyses": {**DISABLED_ANALYSES, "descriptives": True},
        }
    )

    frame, _, _ = prepare_analysis_data(demo_frame, request)

    assert analysis_variable_names(frame, request) == [
        "创新氛围1",
        "创新绩效1",
        "年龄",
        "领导支持1",
        "创新绩效2",
        "工作投入1",
        "性别",
    ]


def test_runner_executes_independent_models_and_preserves_plot_artifacts(
    tmp_path: Path, demo_request
):
    project_root = Path(__file__).resolve().parents[1]
    payload = demo_request.model_dump(mode="json")
    payload.pop("roles")
    payload["analyses"] = DISABLED_ANALYSES
    payload["models"] = [
        {
            "name": "年龄控制路径",
            "analysis": "moderation",
            "x": "创新氛围",
            "y": "创新绩效",
            "moderator": "领导支持",
            "controls": ["年龄"],
        },
        {
            "name": "性别控制路径",
            "analysis": "moderation",
            "x": "工作投入",
            "y": "创新绩效",
            "moderator": "领导支持",
            "controls": ["性别"],
        },
    ]
    request = AnalysisRequest.model_validate(payload)
    progress_updates: list[tuple[int, str]] = []
    run_dir = tmp_path / "multi-model-run"

    summary, _ = run_full_analysis(
        project_root / "examples" / "demo_survey.csv",
        request,
        "multi-model-run",
        run_dir,
        progress=lambda value, message: progress_updates.append((value, message)),
    )

    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    paths = results["path_models"]
    assert results["schema_version"] == "2.0"
    assert "moderation" not in results
    assert [path["id"] for path in paths] == ["model-01", "model-02"]
    assert [path["status"] for path in paths] == ["ok", "ok"]
    assert summary["completed_models"] == ["model-01", "model-02"]
    assert summary["path_models"][0]["result"]["交互项"]
    assert summary["path_models"][0]["result"]["简单斜率"]

    first_terms = {
        row["term"] for row in paths[0]["result"]["models"][0]["coefficients"]
    }
    second_terms = {
        row["term"] for row in paths[1]["result"]["models"][0]["coefficients"]
    }
    assert "年龄" in first_terms and "性别" not in first_terms
    assert "性别" in second_terms and "年龄" not in second_terms

    artifact_names = [
        artifact["name"] for path in paths for artifact in path["artifacts"]
    ]
    assert artifact_names == [
        "model-01_moderation_plot.png",
        "model-01_moderation_plot.svg",
        "model-02_moderation_plot.png",
        "model-02_moderation_plot.svg",
    ]
    assert all((run_dir / name).is_file() for name in artifact_names)
    assert not (run_dir / "moderation_plot.png").exists()

    values = [value for value, _ in progress_updates]
    assert values == sorted(values)
    assert values[-1] == 100
    assert sum("模型" in message for _, message in progress_updates) == 2


def test_runner_keeps_other_path_results_when_one_model_fails(
    tmp_path: Path, demo_request
):
    project_root = Path(__file__).resolve().parents[1]
    payload = demo_request.model_dump(mode="json")
    payload.pop("roles")
    payload["analyses"] = DISABLED_ANALYSES
    payload["models"] = [
        {
            "name": "可运行路径",
            "analysis": "regression",
            "x": "创新氛围",
            "y": "创新绩效",
        },
        {
            "name": "不适用 OLS 的二元结果",
            "analysis": "regression",
            "x": "创新氛围",
            "y": "性别",
        },
    ]
    request = AnalysisRequest.model_validate(payload)
    run_dir = tmp_path / "partially-failed-run"

    summary, _ = run_full_analysis(
        project_root / "examples" / "demo_survey.csv",
        request,
        "partially-failed-run",
        run_dir,
    )

    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert [path["status"] for path in results["path_models"]] == ["ok", "error"]
    assert results["path_models"][0]["result"]["models"]
    assert results["path_models"][1]["result"] == {}
    assert results["path_models"][1]["artifacts"] == []
    assert "连续变量" in results["path_models"][1]["error"]
    assert summary["completed_models"] == ["model-01"]
    assert summary["failed_models"] == ["model-02"]


def test_legacy_request_keeps_top_level_path_results(tmp_path: Path, demo_request):
    project_root = Path(__file__).resolve().parents[1]
    payload = demo_request.model_dump(mode="json")
    payload["analyses"].update(
        {
            "cfa": False,
            "harman": False,
            "ulmc": False,
            "descriptives": False,
            "mediation": False,
            "moderation": False,
            "moderated_mediation": False,
        }
    )
    request = AnalysisRequest.model_validate(payload)
    run_dir = tmp_path / "legacy-run"

    run_full_analysis(
        project_root / "examples" / "demo_survey.csv",
        request,
        "legacy-run",
        run_dir,
    )

    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["schema_version"] == "1.0"
    assert results["regression"]["status"] == "ok"
    assert "path_models" not in results
