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
from .schemas import AnalysisRequest, PathModelConfig
from .sem_analysis import run_cfa, run_harman, run_ulmc


ProgressCallback = Callable[[int, str], None]


def _request_for_path_model(
    request: AnalysisRequest, model: PathModelConfig
) -> AnalysisRequest:
    analyses = request.analyses.model_copy(
        update={"moderated_stage": model.moderated_stage}
    )
    return request.model_copy(
        update={"roles": model.as_roles(), "models": [], "analyses": analyses}
    )


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
    if "path_models" in results:
        path_models = results["path_models"]
        summary["path_models"] = [
            {
                "id": model["id"],
                "name": model["name"],
                "analysis": model["analysis"],
                "status": model["status"],
                **({"error": model["error"]} if model.get("error") else {}),
            }
            for model in path_models
        ]
        summary["completed_models"] = [
            model["id"] for model in path_models if model["status"] == "ok"
        ]
        summary["failed_models"] = [
            model["id"] for model in path_models if model["status"] == "error"
        ]
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
        "schema_version": "2.0" if request.models else "1.0",
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

    global_step_count = sum(
        (
            options.cfa,
            options.harman,
            options.ulmc,
            options.descriptives,
            options.descriptives,
        )
    )
    legacy_step_count = 0
    if not request.models:
        legacy_step_count = sum(
            (
                options.regression,
                options.mediation,
                options.moderation,
                options.moderated_mediation,
            )
        )
    total_steps = global_step_count + legacy_step_count + len(request.models)
    step_index = 0

    def report_step(message: str) -> None:
        nonlocal step_index
        step_index += 1
        value = 10 + round(80 * step_index / (total_steps + 1))
        progress(value, message)

    if options.cfa:
        report_step("运行验证性因子分析")
        execute("cfa", "CFA", lambda: run_cfa(item_data, request.scales))
    if options.harman:
        report_step("运行 Harman 降维检验")
        execute(
            "harman",
            "Harman 检验",
            lambda: run_harman(item_data, request.inference.harman_threshold),
        )
    if options.ulmc:
        report_step("估计 ULMC 方法因子模型")
        execute("ulmc", "ULMC", lambda: run_ulmc(item_data, request.scales))
    if options.descriptives:
        report_step("计算描述性统计")
        execute("descriptives", "描述性统计", lambda: {"rows": run_descriptives(frame, variables)})
        report_step("计算相关矩阵")
        execute(
            "correlations",
            "相关分析",
            lambda: run_correlations(frame, variables, options.correlation, request.inference.alpha),
        )
    if request.models:
        results["path_models"] = []
        path_runners: dict[
            str, Callable[[AnalysisRequest, str], dict[str, Any]]
        ] = {
            "regression": lambda model_request, _model_id: run_regressions(
                frame, model_request
            ),
            "mediation": lambda model_request, _model_id: run_mediation(
                frame, model_request
            ),
            "moderation": lambda model_request, model_id: run_moderation(
                frame, model_request, run_dir, artifact_prefix=model_id
            ),
            "moderated_mediation": lambda model_request, _model_id: (
                run_moderated_mediation(frame, model_request)
            ),
        }
        for index, model in enumerate(request.models, start=1):
            model_id = f"model-{index:02d}"
            label = f"模型 {index}/{len(request.models)}：{model.name}"
            report_step(f"运行{label}")
            entry: dict[str, Any] = {
                "id": model_id,
                "name": model.name,
                "analysis": model.analysis,
                "status": "ok",
                "result": {},
                "artifacts": [],
                "config": model.model_dump(mode="json"),
            }
            try:
                model_request = _request_for_path_model(request, model)
                model_result = path_runners[model.analysis](model_request, model_id)
                entry["artifacts"] = model_result.pop("artifacts", [])
                entry["result"] = model_result
                log_lines.append(f"OK {label}")
            except Exception as exc:  # Keep the other configured paths auditable.
                message = f"{label}: {exc}"
                entry["status"] = "error"
                entry["error"] = str(exc)
                results["errors"].append(message)
                log_lines.append(f"ERROR {message}\n{traceback.format_exc()}")
            results["path_models"].append(entry)
    else:
        if options.regression:
            report_step("估计回归模型")
            execute("regression", "回归分析", lambda: run_regressions(frame, request))
        if options.mediation:
            report_step("Bootstrap 中介效应")
            execute("mediation", "中介效应", lambda: run_mediation(frame, request))
        if options.moderation:
            report_step("计算调节效应与简单斜率")
            execute("moderation", "调节效应", lambda: run_moderation(frame, request, run_dir))
        if options.moderated_mediation:
            report_step("Bootstrap 被调节的中介效应")
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
