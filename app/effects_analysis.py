from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from .schemas import AnalysisRequest
from .stats_utils import (
    bootstrap_statistics,
    finite_or_none,
    fit_ols,
    least_squares_coefficient,
    parameter_index,
)


X_CENTERED = "__epa_x_centered"
M_CENTERED = "__epa_m_centered"
W_CENTERED = "__epa_w_centered"
XW_INTERACTION = "__epa_xw_interaction"
MW_INTERACTION = "__epa_mw_interaction"


def prepare_analysis_data(
    original: pd.DataFrame, request: AnalysisRequest
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    frame = original.copy()
    for code in request.missing_codes:
        frame = frame.replace(code, np.nan)
        frame = frame.replace(str(code), np.nan)

    configured_items = [item for scale in request.scales for item in scale.items]
    item_names = list(dict.fromkeys(configured_items))
    source_columns = set(frame.columns)
    required_source = set(item_names)
    role_values = [request.roles.x, request.roles.y, request.roles.mediator, request.roles.moderator]
    role_values.extend(request.roles.controls)
    scale_names = {scale.name for scale in request.scales}
    required_source.update(value for value in role_values if value and value not in scale_names)
    missing = sorted(required_source - source_columns)
    if missing:
        raise ValueError(f"数据缺少配置中的列: {', '.join(missing)}")

    item_data = pd.DataFrame(index=frame.index)
    range_violations: list[dict[str, Any]] = []
    score_quality: list[dict[str, Any]] = []
    for scale in request.scales:
        numeric = frame[scale.items].apply(pd.to_numeric, errors="coerce")
        invalid = {
            item: int((frame[item].notna() & numeric[item].isna()).sum())
            for item in scale.items
            if (frame[item].notna() & numeric[item].isna()).any()
        }
        if invalid:
            details = ", ".join(f"{item}={count}" for item, count in invalid.items())
            raise ValueError(f"量表题项包含未声明为缺失值的非数值单元格: {details}")
        for item in scale.items:
            violations = int(((numeric[item] < scale.minimum) | (numeric[item] > scale.maximum)).sum())
            if violations:
                range_violations.append(
                    {
                        "construct": scale.name,
                        "item": item,
                        "outside_range": violations,
                        "minimum": scale.minimum,
                        "maximum": scale.maximum,
                    }
                )
        scale_violations = [
            row for row in range_violations if row["construct"] == scale.name
        ]
        if scale_violations:
            details = ", ".join(
                f"{row['item']}={row['outside_range']} 个"
                for row in scale_violations
            )
            raise ValueError(
                f"量表 {scale.name} 存在超出 [{scale.minimum}, {scale.maximum}] 的值: "
                f"{details}；请修正量表范围或将特殊编码声明为缺失值"
            )
        scored_items = numeric.copy()
        for item in scale.reverse_items:
            scored_items[item] = scale.minimum + scale.maximum - scored_items[item]
        item_data = pd.concat([item_data, scored_items], axis=1)
        item_data = item_data.loc[:, ~item_data.columns.duplicated(keep="last")]
        minimum_valid = int(np.ceil(len(scale.items) * scale.min_valid_ratio))
        valid_count = scored_items.notna().sum(axis=1)
        score = scored_items.mean(axis=1).where(valid_count >= minimum_valid)
        frame[scale.name] = score
        score_quality.append(
            {
                "construct": scale.name,
                "items": len(scale.items),
                "reverse_items": ", ".join(scale.reverse_items),
                "minimum_valid_items": minimum_valid,
                "valid_scores": int(score.notna().sum()),
                "missing_scores": int(score.isna().sum()),
            }
        )

    analysis_variables = [value for value in role_values if value]
    for variable in dict.fromkeys(analysis_variables):
        if variable not in frame.columns:
            raise ValueError(f"分析变量不存在: {variable}")
        converted = pd.to_numeric(frame[variable], errors="coerce")
        newly_missing = int((frame[variable].notna() & converted.isna()).sum())
        if newly_missing:
            raise ValueError(f"变量 {variable} 包含 {newly_missing} 个非数值，首版仅支持连续变量")
        frame[variable] = converted

    duplicate_items = len(configured_items) - len(set(configured_items))
    quality = {
        "input_rows": int(len(frame)),
        "input_columns": int(original.shape[1]),
        "duplicate_rows": int(original.duplicated().sum()),
        "duplicate_items_across_scales": duplicate_items,
        "range_violations": range_violations,
        "scale_scores": score_quality,
        "notes": [
            "越界值已标记但未自动删除；请根据问卷编码核对。" if range_violations else "未发现量表越界值。",
            "首版回归类模型采用完全案例分析；CFA 采用 MLW 与完全案例分析。",
        ],
    }
    return frame, item_data, quality


def analysis_variable_names(frame: pd.DataFrame, request: AnalysisRequest) -> list[str]:
    candidates = [scale.name for scale in request.scales]
    candidates.extend(
        [
            request.roles.x,
            request.roles.y,
            request.roles.mediator,
            request.roles.moderator,
            *request.roles.controls,
        ]
    )
    return [name for name in dict.fromkeys(candidates) if name and name in frame.columns]


def run_descriptives(frame: pd.DataFrame, variables: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variable in variables:
        values = pd.to_numeric(frame[variable], errors="coerce").dropna()
        rows.append(
            {
                "variable": variable,
                "n": int(len(values)),
                "missing": int(frame[variable].isna().sum()),
                "missing_percent": finite_or_none(frame[variable].isna().mean() * 100),
                "mean": finite_or_none(values.mean()),
                "sd": finite_or_none(values.std(ddof=1)),
                "median": finite_or_none(values.median()),
                "minimum": finite_or_none(values.min()),
                "maximum": finite_or_none(values.max()),
                "skewness": finite_or_none(stats.skew(values, bias=False)) if len(values) >= 3 else None,
                "kurtosis": finite_or_none(stats.kurtosis(values, fisher=True, bias=False)) if len(values) >= 4 else None,
            }
        )
    return rows


def run_correlations(
    frame: pd.DataFrame, variables: list[str], method: str, alpha: float
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(variables):
        for right_index, right in enumerate(variables):
            if right_index < left_index:
                continue
            pair = frame[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
            if left == right:
                coefficient, p_value = 1.0, 0.0
            elif len(pair) < 3 or pair[left].nunique() <= 1 or pair[right].nunique() <= 1:
                coefficient, p_value = np.nan, np.nan
            elif method == "spearman":
                coefficient, p_value = stats.spearmanr(pair[left], pair[right])
            else:
                coefficient, p_value = stats.pearsonr(pair[left], pair[right])
            ci_low = ci_high = None
            if method == "pearson" and left != right and len(pair) > 3 and np.isfinite(coefficient):
                clipped = np.clip(coefficient, -0.999999, 0.999999)
                z_value = np.arctanh(clipped)
                margin = stats.norm.ppf(1 - alpha / 2) / np.sqrt(len(pair) - 3)
                ci_low, ci_high = np.tanh([z_value - margin, z_value + margin])
            rows.append(
                {
                    "variable_1": left,
                    "variable_2": right,
                    "n": int(len(pair)),
                    "r": finite_or_none(coefficient),
                    "p": finite_or_none(p_value),
                    "ci_low": finite_or_none(ci_low),
                    "ci_high": finite_or_none(ci_high),
                }
            )
    return {"method": method, "rows": rows}


def _complete_model_data(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    unique = list(dict.fromkeys(columns))
    data = frame[unique].apply(pd.to_numeric, errors="coerce").dropna().copy()
    if len(data) < max(20, len(unique) + 5):
        raise ValueError(f"回归完整案例不足（N={len(data)}）")
    return data


def _validate_regression_variable_types(
    data: pd.DataFrame,
    continuous: list[str],
    predictors: list[str],
) -> None:
    discrete = [
        f"{variable}（{data[variable].nunique()} 个取值）"
        for variable in dict.fromkeys(continuous)
        if data[variable].nunique() <= 4
    ]
    if discrete:
        raise ValueError(
            "首版 OLS 流程要求 Y、M 和 W 为连续变量；不满足的变量: "
            + ", ".join(discrete)
        )
    ambiguous = [
        f"{variable}（{data[variable].nunique()} 类）"
        for variable in dict.fromkeys(predictors)
        if data[variable].nunique() in {3, 4}
    ]
    if ambiguous:
        raise ValueError(
            "3/4 分类的 X 或控制变量需先转换为哑变量: "
            + ", ".join(ambiguous)
        )


def run_regressions(frame: pd.DataFrame, request: AnalysisRequest) -> dict[str, Any]:
    roles = request.roles
    variables = [roles.y, roles.x, *roles.controls]
    if roles.mediator:
        variables.append(roles.mediator)
    data = _complete_model_data(frame, variables)
    _validate_regression_variable_types(
        data,
        [roles.y, *([roles.mediator] if roles.mediator else [])],
        [roles.x, *roles.controls],
    )
    _, main_model = fit_ols(
        data,
        roles.y,
        [*roles.controls, roles.x],
        robust_se=request.inference.robust_se,
        alpha=request.inference.alpha,
    )
    models = [{"name": "主效应模型", **main_model}]
    if roles.mediator:
        _, mediator_model = fit_ols(
            data,
            roles.mediator,
            [*roles.controls, roles.x],
            robust_se=request.inference.robust_se,
            alpha=request.inference.alpha,
        )
        _, outcome_model = fit_ols(
            data,
            roles.y,
            [*roles.controls, roles.x, roles.mediator],
            robust_se=request.inference.robust_se,
            alpha=request.inference.alpha,
        )
        models.extend(
            [
                {"name": "中介方程 M", **mediator_model},
                {"name": "中介方程 Y", **outcome_model},
            ]
        )
    return {"common_sample_n": int(len(data)), "models": models}


def run_mediation(frame: pd.DataFrame, request: AnalysisRequest) -> dict[str, Any]:
    roles = request.roles
    if not roles.mediator:
        raise ValueError("未指定中介变量 M")
    columns = [roles.x, roles.mediator, roles.y, *roles.controls]
    data = _complete_model_data(frame, columns)
    _validate_regression_variable_types(
        data, [roles.y, roles.mediator], [roles.x, *roles.controls]
    )
    a_predictors = [*roles.controls, roles.x]
    b_predictors = [*roles.controls, roles.x, roles.mediator]
    total_predictors = [*roles.controls, roles.x]
    _, model_a = fit_ols(
        data,
        roles.mediator,
        a_predictors,
        robust_se=request.inference.robust_se,
        alpha=request.inference.alpha,
    )
    _, model_b = fit_ols(
        data,
        roles.y,
        b_predictors,
        robust_se=request.inference.robust_se,
        alpha=request.inference.alpha,
    )
    _, model_total = fit_ols(
        data,
        roles.y,
        total_predictors,
        robust_se=request.inference.robust_se,
        alpha=request.inference.alpha,
    )

    def evaluator(index: np.ndarray) -> dict[str, float]:
        sample = data.iloc[index]
        a = least_squares_coefficient(sample, roles.mediator, a_predictors, roles.x)
        b = least_squares_coefficient(sample, roles.y, b_predictors, roles.mediator)
        direct = least_squares_coefficient(sample, roles.y, b_predictors, roles.x)
        total = least_squares_coefficient(sample, roles.y, total_predictors, roles.x)
        return {"indirect_ab": a * b, "direct_c_prime": direct, "total_c": total, "a": a, "b": b}

    effects = bootstrap_statistics(
        len(data),
        evaluator,
        request.inference.bootstrap_samples,
        request.inference.seed,
        request.inference.alpha,
        request.inference.confidence_interval,
    )
    return {
        "n": int(len(data)),
        "models": {"a_path": model_a, "outcome": model_b, "total": model_total},
        "effects": list(effects.values()),
        "interpretation": (
            "间接效应置信区间不含 0，存在统计上的间接关联。"
            if effects["indirect_ab"]["significant"]
            else "间接效应置信区间包含 0，未发现显著的间接关联。"
        ),
        "causal_note": "横截面观察数据不能仅凭该结果确立因果中介机制。",
    }


def _coefficient_by_name(fit: Any, name: str) -> float:
    return float(np.asarray(fit.params)[parameter_index(fit, name)])


def _label_model_terms(model: dict[str, Any], labels: dict[str, str]) -> None:
    for coefficient in model.get("coefficients", []):
        coefficient["term"] = labels.get(coefficient["term"], coefficient["term"])
    for row in model.get("vif", []):
        row["term"] = labels.get(row["term"], row["term"])


def _moderation_plot(
    data: pd.DataFrame,
    fit: Any,
    x_name: str,
    w_name: str,
    y_name: str,
    controls: list[str],
    x_mean: float,
    w_mean: float,
    w_sd: float,
    alpha: float,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    predictor_names = list(fit._epa_parameter_names)[1:]
    parameter_names = list(fit._epa_parameter_names)
    parameters = np.asarray(fit.params, dtype=float)
    covariance = np.asarray(fit.cov_params(), dtype=float)
    critical = stats.t.ppf(1 - alpha / 2, fit.df_resid)
    x_low, x_high = data[x_name].quantile([0.05, 0.95])
    x_grid = np.linspace(float(x_low), float(x_high), 80)
    levels = [
        ("低（-1 SD）", w_mean - w_sd),
        ("中（均值）", w_mean),
        ("高（+1 SD）", w_mean + w_sd),
    ]
    control_values = {control: float(data[control].mean()) for control in controls}
    plot_rows: list[dict[str, Any]] = []

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, axis = plt.subplots(figsize=(8.4, 5.4))
    colors = ["#247b6b", "#444b55", "#c24b3a"]
    for color, (label, w_value) in zip(colors, levels, strict=True):
        predictions: list[float] = []
        lower: list[float] = []
        upper: list[float] = []
        for x_value in x_grid:
            row_values = {
                **control_values,
                X_CENTERED: x_value - x_mean,
                W_CENTERED: w_value - w_mean,
                XW_INTERACTION: (x_value - x_mean) * (w_value - w_mean),
            }
            design = np.asarray([1.0, *[row_values[name] for name in predictor_names]], dtype=float)
            predicted = float(design @ parameters)
            se = float(np.sqrt(max(design @ covariance @ design, 0)))
            predictions.append(predicted)
            lower.append(predicted - critical * se)
            upper.append(predicted + critical * se)
            plot_rows.append(
                {
                    "level": label,
                    x_name: float(x_value),
                    w_name: float(w_value),
                    "predicted_y": predicted,
                    "ci_low": predicted - critical * se,
                    "ci_high": predicted + critical * se,
                }
            )
        axis.plot(x_grid, predictions, color=color, linewidth=2.2, label=f"{w_name}: {label}")
        axis.fill_between(x_grid, lower, upper, color=color, alpha=0.12, linewidth=0)
    axis.set_xlabel(x_name)
    axis.set_ylabel(f"{y_name}（预测值）")
    axis.set_title(f"调节效应图（{(1 - alpha) * 100:.0f}% 置信带）")
    axis.grid(axis="y", color="#dfe3e8", linewidth=0.8)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False)
    figure.tight_layout()
    png = output_dir / "moderation_plot.png"
    svg = output_dir / "moderation_plot.svg"
    figure.savefig(png, dpi=180, bbox_inches="tight")
    figure.savefig(svg, bbox_inches="tight")
    plt.close(figure)
    return plot_rows, [
        {"name": png.name, "label": "调节效应图 PNG"},
        {"name": svg.name, "label": "调节效应图 SVG"},
    ]


def run_moderation(
    frame: pd.DataFrame, request: AnalysisRequest, output_dir: Path
) -> dict[str, Any]:
    roles = request.roles
    if not roles.moderator:
        raise ValueError("未指定调节变量 W")
    columns = [roles.x, roles.y, roles.moderator, *roles.controls]
    data = _complete_model_data(frame, columns)
    _validate_regression_variable_types(
        data, [roles.y, roles.moderator], [roles.x, *roles.controls]
    )
    x_mean = float(data[roles.x].mean())
    w_mean = float(data[roles.moderator].mean())
    w_sd = float(data[roles.moderator].std(ddof=1))
    if w_sd <= 0:
        raise ValueError("调节变量 W 没有变异")
    data[X_CENTERED] = data[roles.x] - x_mean
    data[W_CENTERED] = data[roles.moderator] - w_mean
    data[XW_INTERACTION] = data[X_CENTERED] * data[W_CENTERED]

    model_summaries: list[dict[str, Any]] = []
    previous_r2 = None
    blocks = [
        ("模型 1：控制变量", list(roles.controls)),
        ("模型 2：主效应", [*roles.controls, X_CENTERED, W_CENTERED]),
        ("模型 3：交互效应", [*roles.controls, X_CENTERED, W_CENTERED, XW_INTERACTION]),
    ]
    final_fit = None
    for name, predictors in blocks:
        fit, summary = fit_ols(
            data,
            roles.y,
            predictors,
            robust_se=request.inference.robust_se,
            alpha=request.inference.alpha,
        )
        r2 = summary["r_squared"]
        summary["name"] = name
        summary["delta_r_squared"] = finite_or_none(r2 - previous_r2) if previous_r2 is not None else None
        previous_r2 = r2
        model_summaries.append(summary)
        final_fit = fit
    assert final_fit is not None

    x_index = parameter_index(final_fit, X_CENTERED)
    interaction_index = parameter_index(final_fit, XW_INTERACTION)
    parameters = np.asarray(final_fit.params, dtype=float)
    covariance = np.asarray(final_fit.cov_params(), dtype=float)
    critical = stats.t.ppf(1 - request.inference.alpha / 2, final_fit.df_resid)
    slopes: list[dict[str, Any]] = []
    for label, centered_w in (("低（-1 SD）", -w_sd), ("中（均值）", 0.0), ("高（+1 SD）", w_sd)):
        slope = parameters[x_index] + parameters[interaction_index] * centered_w
        variance = (
            covariance[x_index, x_index]
            + centered_w**2 * covariance[interaction_index, interaction_index]
            + 2 * centered_w * covariance[x_index, interaction_index]
        )
        se = np.sqrt(max(variance, 0))
        t_value = slope / se if se else np.nan
        slopes.append(
            {
                "level": label,
                "w_value": w_mean + centered_w,
                "slope": finite_or_none(slope),
                "se": finite_or_none(se),
                "t": finite_or_none(t_value),
                "p": finite_or_none(2 * stats.t.sf(abs(t_value), final_fit.df_resid)),
                "ci_low": finite_or_none(slope - critical * se),
                "ci_high": finite_or_none(slope + critical * se),
            }
        )

    b_x = parameters[x_index]
    b_int = parameters[interaction_index]
    v_x = covariance[x_index, x_index]
    v_int = covariance[interaction_index, interaction_index]
    cov_x_int = covariance[x_index, interaction_index]
    coefficients = [
        b_int**2 - critical**2 * v_int,
        2 * b_x * b_int - 2 * critical**2 * cov_x_int,
        b_x**2 - critical**2 * v_x,
    ]
    polynomial = np.asarray(
        [coefficients[0] * w_sd**2, coefficients[1] * w_sd, coefficients[2]],
        dtype=float,
    )
    coefficient_scale = float(np.max(np.abs(polynomial)))
    roots: np.ndarray | list[float]
    if coefficient_scale == 0:
        roots = []
    else:
        normalized = polynomial / coefficient_scale
        tolerance = np.finfo(float).eps * 100
        nonzero = np.flatnonzero(np.abs(normalized) > tolerance)
        trimmed = normalized[nonzero[0] :] if len(nonzero) else np.asarray([])
        roots = np.roots(trimmed) if len(trimmed) > 1 else []
    observed_min = float(data[roles.moderator].min())
    observed_max = float(data[roles.moderator].max())
    johnson_neyman = sorted(
        finite_or_none(w_mean + root.real * w_sd)
        for root in roots
        if abs(root.imag) < 1e-8
        and observed_min <= w_mean + root.real * w_sd <= observed_max
    )
    johnson_neyman = [value for value in johnson_neyman if value is not None]

    plot_data, artifacts = _moderation_plot(
        data,
        final_fit,
        roles.x,
        roles.moderator,
        roles.y,
        roles.controls,
        x_mean,
        w_mean,
        w_sd,
        request.inference.alpha,
        output_dir,
    )
    interaction_row = next(
        row
        for row in model_summaries[-1]["coefficients"]
        if row["term"] == XW_INTERACTION
    )
    term_labels = {
        X_CENTERED: f"{roles.x}（中心化）",
        W_CENTERED: f"{roles.moderator}（中心化）",
        XW_INTERACTION: f"{roles.x} × {roles.moderator}",
    }
    for model in model_summaries:
        _label_model_terms(model, term_labels)
    return {
        "n": int(len(data)),
        "centering": {roles.x: x_mean, roles.moderator: w_mean},
        "models": model_summaries,
        "interaction": interaction_row,
        "simple_slopes": slopes,
        "johnson_neyman_boundaries": johnson_neyman,
        "observed_moderator_range": [observed_min, observed_max],
        "plot_data": plot_data,
        "artifacts": artifacts,
        "interpretation": (
            "交互项置信区间不含 0，调节效应显著。"
            if interaction_row["ci_low"] is not None
            and (interaction_row["ci_low"] > 0 or interaction_row["ci_high"] < 0)
            else "交互项置信区间包含 0，未发现显著调节效应。"
        ),
    }


def run_moderated_mediation(frame: pd.DataFrame, request: AnalysisRequest) -> dict[str, Any]:
    roles = request.roles
    if not roles.mediator or not roles.moderator:
        raise ValueError("被调节的中介需要同时指定 M 和 W")
    columns = [roles.x, roles.mediator, roles.moderator, roles.y, *roles.controls]
    data = _complete_model_data(frame, columns)
    _validate_regression_variable_types(
        data,
        [roles.y, roles.mediator, roles.moderator],
        [roles.x, *roles.controls],
    )
    x_mean = float(data[roles.x].mean())
    m_mean = float(data[roles.mediator].mean())
    w_mean = float(data[roles.moderator].mean())
    w_sd = float(data[roles.moderator].std(ddof=1))
    if w_sd <= 0:
        raise ValueError("调节变量 W 没有变异")
    data[X_CENTERED] = data[roles.x] - x_mean
    data[M_CENTERED] = data[roles.mediator] - m_mean
    data[W_CENTERED] = data[roles.moderator] - w_mean
    data[XW_INTERACTION] = data[X_CENTERED] * data[W_CENTERED]
    data[MW_INTERACTION] = data[M_CENTERED] * data[W_CENTERED]
    w_points = [("low", -w_sd), ("mean", 0.0), ("high", w_sd)]

    if request.analyses.moderated_stage == "first":
        template = "PROCESS Model 7（第一阶段调节）"
        mediator_predictors = [*roles.controls, X_CENTERED, W_CENTERED, XW_INTERACTION]
        outcome_predictors = [*roles.controls, X_CENTERED, M_CENTERED]

        def evaluator(index: np.ndarray) -> dict[str, float]:
            sample = data.iloc[index]
            a1 = least_squares_coefficient(sample, roles.mediator, mediator_predictors, X_CENTERED)
            a3 = least_squares_coefficient(sample, roles.mediator, mediator_predictors, XW_INTERACTION)
            b = least_squares_coefficient(sample, roles.y, outcome_predictors, M_CENTERED)
            values = {f"indirect_{label}": (a1 + a3 * w_value) * b for label, w_value in w_points}
            values["index_moderated_mediation"] = a3 * b
            return values

    else:
        template = "PROCESS Model 14（第二阶段调节）"
        mediator_predictors = [*roles.controls, X_CENTERED]
        outcome_predictors = [
            *roles.controls,
            X_CENTERED,
            M_CENTERED,
            W_CENTERED,
            MW_INTERACTION,
        ]

        def evaluator(index: np.ndarray) -> dict[str, float]:
            sample = data.iloc[index]
            a = least_squares_coefficient(sample, roles.mediator, mediator_predictors, X_CENTERED)
            b1 = least_squares_coefficient(sample, roles.y, outcome_predictors, M_CENTERED)
            b3 = least_squares_coefficient(sample, roles.y, outcome_predictors, MW_INTERACTION)
            values = {f"indirect_{label}": a * (b1 + b3 * w_value) for label, w_value in w_points}
            values["index_moderated_mediation"] = a * b3
            return values

    _, mediator_model = fit_ols(
        data,
        roles.mediator,
        mediator_predictors,
        robust_se=request.inference.robust_se,
        alpha=request.inference.alpha,
    )
    _, outcome_model = fit_ols(
        data,
        roles.y,
        outcome_predictors,
        robust_se=request.inference.robust_se,
        alpha=request.inference.alpha,
    )
    term_labels = {
        X_CENTERED: f"{roles.x}（中心化）",
        M_CENTERED: f"{roles.mediator}（中心化）",
        W_CENTERED: f"{roles.moderator}（中心化）",
        XW_INTERACTION: f"{roles.x} × {roles.moderator}",
        MW_INTERACTION: f"{roles.mediator} × {roles.moderator}",
    }
    _label_model_terms(mediator_model, term_labels)
    _label_model_terms(outcome_model, term_labels)
    effects = bootstrap_statistics(
        len(data),
        evaluator,
        request.inference.bootstrap_samples,
        request.inference.seed + 17,
        request.inference.alpha,
        request.inference.confidence_interval,
    )
    effect_rows: list[dict[str, Any]] = []
    point_map = {label: w_mean + value for label, value in w_points}
    for key, value in effects.items():
        row = dict(value)
        if key.startswith("indirect_"):
            row["moderator_value"] = point_map[key.removeprefix("indirect_")]
        effect_rows.append(row)
    index_result = effects["index_moderated_mediation"]
    return {
        "template": template,
        "n": int(len(data)),
        "centering": {roles.x: x_mean, roles.mediator: m_mean, roles.moderator: w_mean},
        "models": {"mediator": mediator_model, "outcome": outcome_model},
        "effects": effect_rows,
        "interpretation": (
            "被调节中介指数的 Bootstrap 置信区间不含 0，间接效应随 W 显著变化。"
            if index_result["significant"]
            else "被调节中介指数的 Bootstrap 置信区间包含 0，未发现间接效应随 W 显著变化。"
        ),
        "note": "结论以被调节中介指数的置信区间为准，不能通过比较各条件点是否分别显著来替代。",
    }
