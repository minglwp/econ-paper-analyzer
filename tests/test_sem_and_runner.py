from __future__ import annotations

import json
from pathlib import Path

from app.effects_analysis import prepare_analysis_data
from app.runner import run_full_analysis
from app.sem_analysis import run_cfa, run_harman, run_ulmc


def test_cfa_harman_and_ulmc(demo_frame, demo_request):
    _, item_data, _ = prepare_analysis_data(demo_frame, demo_request)
    cfa = run_cfa(item_data, demo_request.scales)
    assert cfa["fit"]["converged"] is True
    assert cfa["fit"]["n"] > 250
    assert len(cfa["loadings"]) == 12
    assert all(row["composite_reliability"] is not None for row in cfa["reliability"])

    harman = run_harman(item_data, 40)
    assert 0 < harman["first_component_percent"] < 100
    assert len(harman["eigenvalues"]) == 12

    ulmc = run_ulmc(item_data, demo_request.scales)
    assert ulmc["trait_only_fit"]["converged"] is True
    assert ulmc["trait_method_fit"]["converged"] is True
    assert len(ulmc["method_loadings"]) == 12


def test_full_runner_exports(tmp_path: Path, demo_request):
    project_root = Path(__file__).resolve().parents[1]
    input_path = project_root / "examples" / "demo_survey.csv"
    run_dir = tmp_path / "run-test"
    summary, artifacts = run_full_analysis(
        input_path,
        demo_request,
        "run-test",
        run_dir,
    )
    assert summary["input_rows"] == 320
    preview = summary["in_app_preview"]
    ulmc_preview = preview["共同方法偏差：ULMC"]
    assert ulmc_preview
    assert list(ulmc_preview[0]) == ["指标", "特质模型拟合结果", "方法因子模型拟合", "对比结果"]
    correlation_preview = preview["相关分析"]
    assert correlation_preview["display"] == "correlation_lower_triangle"
    assert correlation_preview["variables"]
    assert correlation_preview["rows"][0]["values"][0] == ""
    assert correlation_preview["rows"][1]["values"][0].endswith("***")
    assert {artifact["name"] for artifact in artifacts} >= {
        "report.html",
        "tables.xlsx",
        "results.json",
        "analysis_bundle.zip",
    }
    for artifact in artifacts:
        assert (run_dir / artifact["name"]).is_file()
    assert (run_dir / ".complete").is_file()
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["data_sha256"]
    assert results["config"]["inference"]["seed"] == 20260730
    report = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "横截面" in report
    assert "区分效度（HTMT）" in report
    assert "Johnson–Neyman 边界" in report
    assert "创新氛围 × 领导支持" in report
    assert "__epa_" not in report
