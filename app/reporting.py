from __future__ import annotations

import html
import importlib.metadata
import json
import platform
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import RESOURCE_ROOT
from .data import write_json


def software_versions() -> dict[str, str]:
    packages = ["numpy", "pandas", "scipy", "statsmodels", "semopy", "matplotlib", "fastapi"]
    versions = {"python": platform.python_version(), "platform": platform.platform()}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _model_coefficient_rows(models: list[dict[str, Any]] | dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(models, dict):
        iterable = [(name, model) for name, model in models.items()]
    else:
        iterable = [(model.get("name", f"model_{index}"), model) for index, model in enumerate(models, start=1)]
    rows: list[dict[str, Any]] = []
    for name, model in iterable:
        for coefficient in model.get("coefficients", []):
            rows.append({"model": name, "outcome": model.get("outcome"), "n": model.get("n"), **coefficient})
    return rows


def _iter_models(
    models: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(models, dict):
        return list(models.items())
    return [
        (model.get("name", f"model_{index}"), model)
        for index, model in enumerate(models, start=1)
    ]


def _model_summary_rows(
    models: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "outcome",
        "n",
        "r_squared",
        "adjusted_r_squared",
        "delta_r_squared",
        "f",
        "f_p",
        "aic",
        "bic",
        "robust_se",
        "confidence_level",
    )
    rows: list[dict[str, Any]] = []
    for name, model in _iter_models(models):
        row = {"model": name, **{field: model.get(field) for field in fields}}
        row.update(model.get("diagnostics", {}))
        rows.append(row)
    return rows


def _model_vif_rows(
    models: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, model in _iter_models(models):
        rows.extend(
            {"model": name, "outcome": model.get("outcome"), **row}
            for row in model.get("vif", [])
        )
    return rows


def _fit_rows(results: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "cfa" in results and results["cfa"].get("status") == "ok":
        rows.append({"model": "CFA", **results["cfa"]["fit"]})
    if "ulmc" in results and results["ulmc"].get("status") == "ok":
        rows.append({"model": "Trait only", **results["ulmc"]["trait_only_fit"]})
        rows.append({"model": "Trait + method", **results["ulmc"]["trait_method_fit"]})
    return rows


def _write_sheet(writer: pd.ExcelWriter, name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    frame = pd.DataFrame(rows)
    for column in frame.columns:
        if frame[column].map(lambda value: isinstance(value, (dict, list))).any():
            frame[column] = frame[column].map(
                lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            )
    frame.to_excel(writer, sheet_name=name[:31], index=False)
    worksheet = writer.sheets[name[:31]]
    worksheet.freeze_panes(1, 0)
    worksheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
    for index, column in enumerate(frame.columns):
        width = min(42, max(11, len(str(column)) + 2, *(len(str(value)) + 1 for value in frame[column].head(80))))
        worksheet.set_column(index, index, width)


def export_excel(results: dict[str, Any], output_path: Path) -> None:
    with pd.ExcelWriter(
        output_path,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_formulas": False, "strings_to_urls": False}},
    ) as writer:
        overview = [
            {"field": "run_id", "value": results.get("run_id")},
            {"field": "created_at", "value": results.get("created_at")},
            {"field": "data_sha256", "value": results.get("data_sha256")},
            {"field": "input_rows", "value": results.get("data_quality", {}).get("input_rows")},
            {"field": "errors", "value": " | ".join(results.get("errors", []))},
        ]
        _write_sheet(writer, "运行概览", overview)
        quality = results.get("data_quality", {})
        _write_sheet(writer, "量表得分质量", quality.get("scale_scores", []))
        _write_sheet(writer, "越界值", quality.get("range_violations", []))
        _write_sheet(writer, "描述性统计", results.get("descriptives", {}).get("rows", []))
        _write_sheet(writer, "相关分析", results.get("correlations", {}).get("rows", []))

        if (
            results.get("cfa", {}).get("status") == "ok"
            or results.get("ulmc", {}).get("status") == "ok"
        ):
            _write_sheet(writer, "SEM拟合", _fit_rows(results))
        if results.get("cfa", {}).get("status") == "ok":
            _write_sheet(writer, "CFA载荷", results["cfa"].get("loadings", []))
            _write_sheet(writer, "信度效度", results["cfa"].get("reliability", []))
            _write_sheet(writer, "HTMT", results["cfa"].get("htmt", []))
        if results.get("harman", {}).get("status") == "ok":
            summary = {key: value for key, value in results["harman"].items() if key not in {"eigenvalues", "diagnostics"}}
            summary.update({f"diagnostic_{key}": value for key, value in results["harman"].get("diagnostics", {}).items()})
            _write_sheet(writer, "Harman摘要", [summary])
            _write_sheet(writer, "Harman特征值", results["harman"].get("eigenvalues", []))
        if results.get("ulmc", {}).get("status") == "ok":
            _write_sheet(writer, "ULMC比较", [results["ulmc"].get("comparison", {})])
            _write_sheet(writer, "ULMC方法载荷", results["ulmc"].get("method_loadings", []))
        if results.get("regression", {}).get("status") == "ok":
            _write_sheet(writer, "回归模型摘要", _model_summary_rows(results["regression"].get("models", [])))
            _write_sheet(writer, "回归系数", _model_coefficient_rows(results["regression"].get("models", [])))
            _write_sheet(writer, "回归VIF", _model_vif_rows(results["regression"].get("models", [])))
        if results.get("mediation", {}).get("status") == "ok":
            _write_sheet(writer, "中介效应", results["mediation"].get("effects", []))
            _write_sheet(writer, "中介模型摘要", _model_summary_rows(results["mediation"].get("models", {})))
            _write_sheet(writer, "中介回归", _model_coefficient_rows(results["mediation"].get("models", {})))
        if results.get("moderation", {}).get("status") == "ok":
            _write_sheet(writer, "调节模型摘要", _model_summary_rows(results["moderation"].get("models", [])))
            _write_sheet(writer, "调节回归", _model_coefficient_rows(results["moderation"].get("models", [])))
            _write_sheet(writer, "调节VIF", _model_vif_rows(results["moderation"].get("models", [])))
            _write_sheet(writer, "简单斜率", results["moderation"].get("simple_slopes", []))
            _write_sheet(
                writer,
                "Johnson-Neyman",
                [
                    {
                        "boundary": boundary,
                        "observed_min": results["moderation"].get("observed_moderator_range", [None, None])[0],
                        "observed_max": results["moderation"].get("observed_moderator_range", [None, None])[1],
                    }
                    for boundary in results["moderation"].get("johnson_neyman_boundaries", [])
                ],
            )
            _write_sheet(writer, "调节绘图数据", results["moderation"].get("plot_data", []))
        if results.get("moderated_mediation", {}).get("status") == "ok":
            _write_sheet(writer, "被调节中介效应", results["moderated_mediation"].get("effects", []))
            _write_sheet(
                writer,
                "被调节中介模型摘要",
                _model_summary_rows(results["moderated_mediation"].get("models", {})),
            )
            _write_sheet(
                writer,
                "被调节中介回归",
                _model_coefficient_rows(results["moderated_mediation"].get("models", {})),
            )


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if abs(value) < 0.0001 and value != 0:
            return f"{value:.3e}"
        return f"{value:.4f}"
    return str(value)


def _html_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return '<p class="empty">无可用结果</p>'
    selected = columns or list(rows[0].keys())
    head = "".join(f"<th>{html.escape(str(column))}</th>" for column in selected)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(_format_value(row.get(column)))}</td>" for column in selected)
        body.append(f"<tr>{cells}</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_html_report(results: dict[str, Any], output_path: Path) -> None:
    environment = Environment(
        loader=FileSystemLoader(RESOURCE_ROOT / "app" / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    environment.filters["fmt"] = _format_value
    template = environment.get_template("report.html")
    context = {
        "results": results,
        "tables": {
            "descriptives": _html_table(results.get("descriptives", {}).get("rows", [])),
            "correlations": _html_table(results.get("correlations", {}).get("rows", [])),
            "cfa_fit": _html_table([results.get("cfa", {}).get("fit", {})]),
            "cfa_loadings": _html_table(results.get("cfa", {}).get("loadings", [])),
            "reliability": _html_table(results.get("cfa", {}).get("reliability", [])),
            "htmt": _html_table(results.get("cfa", {}).get("htmt", [])),
            "harman": _html_table(
                [
                    {
                        "method": results.get("harman", {}).get("method"),
                        "n": results.get("harman", {}).get("n"),
                        "first_component_percent": results.get("harman", {}).get("first_component_percent"),
                        "threshold_percent": results.get("harman", {}).get("threshold_percent"),
                    }
                ]
            ),
            "harman_diagnostics": _html_table(
                [results.get("harman", {}).get("diagnostics", {})]
            ),
            "ulmc": _html_table(
                [
                    {"model": "Trait only", **results.get("ulmc", {}).get("trait_only_fit", {})},
                    {"model": "Trait + method", **results.get("ulmc", {}).get("trait_method_fit", {})},
                ]
            ),
            "ulmc_comparison": _html_table(
                [results.get("ulmc", {}).get("comparison", {})]
            ),
            "ulmc_loadings": _html_table(
                results.get("ulmc", {}).get("method_loadings", [])
            ),
            "regression_fit": _html_table(
                _model_summary_rows(results.get("regression", {}).get("models", []))
            ),
            "regression": _html_table(
                _model_coefficient_rows(results.get("regression", {}).get("models", []))
            ),
            "regression_vif": _html_table(
                _model_vif_rows(results.get("regression", {}).get("models", []))
            ),
            "mediation": _html_table(results.get("mediation", {}).get("effects", [])),
            "mediation_fit": _html_table(
                _model_summary_rows(results.get("mediation", {}).get("models", {}))
            ),
            "mediation_regression": _html_table(
                _model_coefficient_rows(results.get("mediation", {}).get("models", {}))
            ),
            "moderation_fit": _html_table(
                _model_summary_rows(results.get("moderation", {}).get("models", []))
            ),
            "moderation_regression": _html_table(
                _model_coefficient_rows(results.get("moderation", {}).get("models", []))
            ),
            "moderation": _html_table(
                results.get("moderation", {}).get("simple_slopes", [])
            ),
            "johnson_neyman": _html_table(
                [
                    {"boundary": boundary}
                    for boundary in results.get("moderation", {}).get(
                        "johnson_neyman_boundaries", []
                    )
                ]
            ),
            "moderated_mediation": _html_table(results.get("moderated_mediation", {}).get("effects", [])),
            "moderated_mediation_fit": _html_table(
                _model_summary_rows(
                    results.get("moderated_mediation", {}).get("models", {})
                )
            ),
            "moderated_mediation_regression": _html_table(
                _model_coefficient_rows(
                    results.get("moderated_mediation", {}).get("models", {})
                )
            ),
        },
    }
    output_path.write_text(template.render(**context), encoding="utf-8")


def create_bundle(run_dir: Path, output_name: str = "analysis_bundle.zip") -> Path:
    output_path = run_dir / output_name
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.iterdir()):
            if path.is_file() and path.name != output_name:
                archive.write(path, arcname=path.name)
    return output_path


def save_all_outputs(results: dict[str, Any], run_dir: Path) -> list[dict[str, str]]:
    results_path = run_dir / "results.json"
    excel_path = run_dir / "tables.xlsx"
    report_path = run_dir / "report.html"
    artifacts = [
        {"name": "report.html", "label": "完整 HTML 报告"},
        {"name": "tables.xlsx", "label": "论文结果表 Excel"},
        {"name": "results.json", "label": "机器可读结果 JSON"},
        {"name": "analysis_config.json", "label": "可复现分析配置"},
        {"name": "analysis_bundle.zip", "label": "完整审计包 ZIP"},
    ]
    if (run_dir / "moderation_plot.png").exists():
        artifacts.extend(
            [
                {"name": "moderation_plot.png", "label": "调节效应图 PNG"},
                {"name": "moderation_plot.svg", "label": "调节效应图 SVG"},
            ]
        )
    results["artifacts"] = artifacts
    write_json(results_path, results)
    export_excel(results, excel_path)
    render_html_report(results, report_path)
    create_bundle(run_dir)
    marker = run_dir / ".complete"
    marker_temporary = run_dir / ".complete.tmp"
    write_json(
        marker_temporary,
        {
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "artifacts": [artifact["name"] for artifact in artifacts],
        },
    )
    marker_temporary.replace(marker)
    return artifacts
