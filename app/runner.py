from __future__ import annotations

import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .data import file_sha256, load_dataframe, write_json
from .effects_analysis import (
    analysis_variable_names,
    prepare_analysis_data,
    run_correlations,
    run_descriptives,
    run_mediation,
    run_moderated_mediation,
    run_moderation,
    run_regressions,
)
from .reporting import save_all_outputs, software_versions
from .schemas import AnalysisRequest
from .sem_analysis import run_cfa, run_harman, run_ulmc


ProgressCallback = Callable[[int, str], None]


def summarize_results(results: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "input_rows": results.get("data_quality", {}).get("input_rows"),
        "completed_modules": [
            name
            for name in (
                "cfa",
                "harman",
                "ulmc",
                "descriptives",
                "correlations",
                "regression",
                "mediation",
                "moderation",
                "moderated_mediation",
            )
            if results.get(name, {}).get("status") == "ok"
        ],
        "failed_modules": [
            name
            for name in (
                "cfa",
                "harman",
                "ulmc",
                "descriptives",
                "correlations",
                "regression",
                "mediation",
                "moderation",
                "moderated_mediation",
            )
            if results.get(name, {}).get("status") == "error"
        ],
        "errors": list(results.get("errors", [])),
    }
    if results.get("cfa", {}).get("status") == "ok":
        summary["cfa_fit"] = results["cfa"]["fit"]
    if results.get("harman", {}).get("status") == "ok":
        summary["harman_first_component_percent"] = results["harman"]["first_component_percent"]
    if results.get("mediation", {}).get("status") == "ok":
        summary["mediation_indirect"] = next(
            row for row in results["mediation"]["effects"] if row["effect"] == "indirect_ab"
        )
    if results.get("moderation", {}).get("status") == "ok":
        summary["moderation_interaction"] = results["moderation"]["interaction"]
    if results.get("moderated_mediation", {}).get("status") == "ok":
        summary["moderated_mediation_index"] = next(
            row
            for row in results["moderated_mediation"]["effects"]
            if row["effect"] == "index_moderated_mediation"
        )
    return summary


def run_full_analysis(
    input_path: Path,
    request: AnalysisRequest,
    run_id: str,
    run_dir: Path,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    progress = progress or (lambda _value, _message: None)
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "analysis_config.json", request.model_dump(mode="json"))
    log_path = run_dir / "analysis.log"
    log_lines: list[str] = []

    progress(4, "读取数据并检查配置")
    original = load_dataframe(input_path, request.sheet_name)
    frame, item_data, quality = prepare_analysis_data(original, request)
    results: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_file": input_path.name,
        "data_sha256": file_sha256(input_path),
        "config": request.model_dump(mode="json"),
        "software_versions": software_versions(),
        "data_quality": quality,
        "errors": [],
    }

    def execute(name: str, label: str, function: Callable[[], dict[str, Any]]) -> None:
        try:
            results[name] = {"status": "ok", **function()}
            log_lines.append(f"OK {label}")
        except Exception as exc:  # A failed module must not discard the other audited outputs.
            message = f"{label}: {exc}"
            results[name] = {"status": "error", "error": str(exc)}
            results["errors"].append(message)
            log_lines.append(f"ERROR {message}\n{traceback.format_exc()}")

    options = request.analyses
    progress(10, "计算量表得分与数据质量")
    variables = analysis_variable_names(frame, request)

    if options.cfa:
        progress(18, "运行验证性因子分析")
        execute("cfa", "CFA", lambda: run_cfa(item_data, request.scales))
    if options.harman:
        progress(29, "运行 Harman 降维检验")
        execute(
            "harman",
            "Harman 检验",
            lambda: run_harman(item_data, request.inference.harman_threshold),
        )
    if options.ulmc:
        progress(39, "估计 ULMC 方法因子模型")
        execute("ulmc", "ULMC", lambda: run_ulmc(item_data, request.scales))
    if options.descriptives:
        progress(48, "计算描述性统计")
        execute("descriptives", "描述性统计", lambda: {"rows": run_descriptives(frame, variables)})
        progress(54, "计算相关矩阵")
        execute(
            "correlations",
            "相关分析",
            lambda: run_correlations(frame, variables, options.correlation, request.inference.alpha),
        )
    if options.regression:
        progress(61, "估计回归模型")
        execute("regression", "回归分析", lambda: run_regressions(frame, request))
    if options.mediation:
        progress(69, "Bootstrap 中介效应")
        execute("mediation", "中介效应", lambda: run_mediation(frame, request))
    if options.moderation:
        progress(78, "计算调节效应与简单斜率")
        execute("moderation", "调节效应", lambda: run_moderation(frame, request, run_dir))
    if options.moderated_mediation:
        progress(87, "Bootstrap 被调节的中介效应")
        execute(
            "moderated_mediation",
            "被调节的中介",
            lambda: run_moderated_mediation(frame, request),
        )

    log_path.write_text("\n\n".join(log_lines), encoding="utf-8")
    progress(94, "生成报告、表格和审计包")
    artifacts = save_all_outputs(results, run_dir)
    summary = summarize_results(results)
    progress(100, "分析完成")
    return summary, artifacts
