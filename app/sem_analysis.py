from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize, stats
from semopy import Model, calc_stats

from .schemas import ScaleConfig
from .stats_utils import cronbach_alpha, finite_or_none


def _safe_sem_data(
    item_data: pd.DataFrame, scales: list[ScaleConfig]
) -> tuple[pd.DataFrame, list[tuple[ScaleConfig, str, list[str]]], dict[str, str]]:
    all_items = list(dict.fromkeys(item for scale in scales for item in scale.items))
    aliases = {item: f"v_{index:03d}" for index, item in enumerate(all_items, start=1)}
    aliased = item_data[all_items].rename(columns=aliases).apply(pd.to_numeric, errors="coerce")
    model_scales = [
        (scale, f"f_{index:03d}", [aliases[item] for item in scale.items])
        for index, scale in enumerate(scales, start=1)
    ]
    return aliased, model_scales, aliases


def _srmr(model: Model, _data: pd.DataFrame) -> float | None:
    try:
        sample = np.asarray(model.mx_cov, dtype=float)
        implied = np.asarray(model.calc_sigma()[0], dtype=float)
        standardizer = np.sqrt(np.outer(np.diag(sample), np.diag(sample)))
        residual = np.divide(sample - implied, standardizer, out=np.zeros_like(sample), where=standardizer > 0)
        lower = residual[np.tril_indices_from(residual)]
        return finite_or_none(np.sqrt(np.mean(np.square(lower))))
    except (KeyError, ValueError, np.linalg.LinAlgError):
        return None


def _rmsea_interval(chi_square: float | None, df: float | None, n: int, level: float = 0.90) -> tuple[float | None, float | None]:
    if chi_square is None or df is None or df <= 0 or n <= 1:
        return None, None

    tail = (1 - level) / 2

    def ncp_for_probability(probability: float) -> float:
        at_zero = stats.ncx2.cdf(chi_square, df, 0)
        if at_zero <= probability:
            return 0.0
        upper = max(float(chi_square), 1.0)
        while stats.ncx2.cdf(chi_square, df, upper) > probability and upper < 1e7:
            upper *= 2
        return float(optimize.brentq(lambda value: stats.ncx2.cdf(chi_square, df, value) - probability, 0, upper))

    lower_ncp = ncp_for_probability(1 - tail)
    upper_ncp = ncp_for_probability(tail)
    return (
        finite_or_none(np.sqrt(max(lower_ncp, 0) / (df * (n - 1)))),
        finite_or_none(np.sqrt(max(upper_ncp, 0) / (df * (n - 1)))),
    )


def _latent_diagnostics(model: Model, estimates: pd.DataFrame) -> dict[str, Any]:
    latent = {str(value) for value in model.vars.get("latent", set())}
    correlations = estimates[
        (estimates["op"] == "~~")
        & (estimates["lval"] != estimates["rval"])
        & estimates["lval"].astype(str).isin(latent)
        & estimates["rval"].astype(str).isin(latent)
    ]
    values = pd.to_numeric(correlations.get("Est. Std"), errors="coerce").dropna()
    max_correlation = float(values.abs().max()) if len(values) else None
    covariance = np.asarray(model.mx_psi, dtype=float)
    eigenvalues = np.linalg.eigvalsh((covariance + covariance.T) / 2)
    min_eigenvalue = float(eigenvalues.min()) if len(eigenvalues) else None
    scale = max(float(np.max(np.abs(eigenvalues))), 1.0) if len(eigenvalues) else 1.0
    positive_definite = bool(
        min_eigenvalue is not None and min_eigenvalue > np.finfo(float).eps * 100 * scale
    )
    return {
        "max_abs_latent_correlation": finite_or_none(max_correlation),
        "latent_covariance_min_eigenvalue": finite_or_none(min_eigenvalue),
        "latent_covariance_positive_definite": positive_definite,
        "improper_latent_correlation": bool(
            max_correlation is not None and max_correlation >= 1 - 1e-6
        ),
    }


def _fit_sem(description: str, data: pd.DataFrame) -> tuple[Model, dict[str, Any], pd.DataFrame]:
    complete = data.dropna()
    if len(complete) < max(30, complete.shape[1] + 5):
        raise ValueError(f"SEM 完整样本不足（N={len(complete)}，题项数={complete.shape[1]}）")
    if any(complete[column].nunique() <= 1 for column in complete.columns):
        constants = [column for column in complete.columns if complete[column].nunique() <= 1]
        raise ValueError(f"SEM 题项没有变异: {', '.join(constants)}")

    model = Model(description)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        optimizer = model.fit(complete, obj="MLW", solver="SLSQP")
        estimates = model.inspect(std_est=True)
        raw_stats = calc_stats(model).iloc[0]
    warning_text = [str(item.message) for item in caught]
    success = bool(getattr(optimizer, "success", True))
    message = str(getattr(optimizer, "message", ""))
    residual_rows = estimates[
        (estimates["op"] == "~~") & (estimates["lval"] == estimates["rval"])
    ]
    negative_variances = residual_rows[
        pd.to_numeric(residual_rows["Estimate"], errors="coerce") < -1e-6
    ]
    latent_diagnostics = _latent_diagnostics(model, estimates)

    chi_square = finite_or_none(raw_stats.get("chi2"))
    degrees_freedom = finite_or_none(raw_stats.get("DoF"))
    if degrees_freedom is None or degrees_freedom <= 0:
        raise ValueError(
            "SEM 模型自由度必须大于 0，无法检验整体拟合"
            f"（df={degrees_freedom}）"
        )
    rmsea_low, rmsea_high = _rmsea_interval(chi_square, degrees_freedom, len(complete))
    fit = {
        "n": int(len(complete)),
        "estimator": "MLW",
        "missing": "listwise",
        "converged": success,
        "optimizer_message": message,
        "warnings": warning_text,
        "heywood_case": bool(len(negative_variances)),
        **latent_diagnostics,
        "chi_square": chi_square,
        "df": degrees_freedom,
        "p": finite_or_none(raw_stats.get("chi2 p-value")),
        "chi_square_df": finite_or_none(
            raw_stats.get("chi2") / raw_stats.get("DoF")
            if raw_stats.get("DoF") and raw_stats.get("DoF") > 0
            else None
        ),
        "cfi": finite_or_none(raw_stats.get("CFI")),
        "tli": finite_or_none(raw_stats.get("TLI")),
        "rmsea": finite_or_none(raw_stats.get("RMSEA")),
        "rmsea_ci_90_low": rmsea_low,
        "rmsea_ci_90_high": rmsea_high,
        "srmr": _srmr(model, complete),
    }
    if not success:
        raise ValueError(f"SEM 未收敛: {message}")
    if len(negative_variances):
        raise ValueError("SEM 出现负方差（Heywood 不当解），不输出拟合与信效度结论")
    if latent_diagnostics["improper_latent_correlation"]:
        raise ValueError("SEM 潜变量相关达到或超过 1，属于不当解")
    if not latent_diagnostics["latent_covariance_positive_definite"]:
        raise ValueError("SEM 潜变量协方差矩阵非正定，属于不当解")
    return model, fit, estimates


def _standardized_loading_rows(
    estimates: pd.DataFrame,
    model_scales: list[tuple[ScaleConfig, str, list[str]]],
    aliases: dict[str, str],
) -> list[dict[str, Any]]:
    alias_to_original = {alias: original for original, alias in aliases.items()}
    latent_to_scale = {latent: scale.name for scale, latent, _ in model_scales}
    rows: list[dict[str, Any]] = []
    for _, row in estimates[estimates["op"] == "~"].iterrows():
        latent = str(row["rval"])
        observed = str(row["lval"])
        if latent not in latent_to_scale or observed not in alias_to_original:
            continue
        rows.append(
            {
                "construct": latent_to_scale[latent],
                "item": alias_to_original[observed],
                "estimate": finite_or_none(row.get("Estimate")),
                "std_loading": finite_or_none(row.get("Est. Std")),
                "se": finite_or_none(row.get("Std. Err")),
                "z": finite_or_none(row.get("z-value")),
                "p": finite_or_none(row.get("p-value")),
            }
        )
    return rows


def _reliability_rows(
    loadings: list[dict[str, Any]], item_data: pd.DataFrame, scales: list[ScaleConfig]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scale in scales:
        values = [
            row["std_loading"]
            for row in loadings
            if row["construct"] == scale.name and row["std_loading"] is not None
        ]
        lambdas = np.asarray(values, dtype=float)
        residuals = 1 - np.square(lambdas)
        denominator = np.square(lambdas.sum()) + residuals.sum()
        cr = np.square(lambdas.sum()) / denominator if denominator > 0 and len(lambdas) else None
        ave = np.square(lambdas).mean() if len(lambdas) else None
        rows.append(
            {
                "construct": scale.name,
                "n": int(len(item_data[scale.items].dropna())),
                "items": len(scale.items),
                "alpha": cronbach_alpha(item_data[scale.items]),
                "composite_reliability": finite_or_none(cr),
                "ave": finite_or_none(ave),
                "sqrt_ave": finite_or_none(np.sqrt(ave) if ave is not None and ave >= 0 else None),
            }
        )
    return rows


def _htmt_rows(item_data: pd.DataFrame, scales: list[ScaleConfig]) -> list[dict[str, Any]]:
    correlation = item_data.apply(pd.to_numeric, errors="coerce").corr().abs()
    rows: list[dict[str, Any]] = []
    for index, left in enumerate(scales):
        for right in scales[index + 1 :]:
            hetero = correlation.loc[left.items, right.items].to_numpy(dtype=float)
            left_corr = correlation.loc[left.items, left.items].to_numpy(dtype=float)
            right_corr = correlation.loc[right.items, right.items].to_numpy(dtype=float)
            left_values = left_corr[np.triu_indices_from(left_corr, k=1)]
            right_values = right_corr[np.triu_indices_from(right_corr, k=1)]
            denominator = np.sqrt(np.nanmean(left_values) * np.nanmean(right_values))
            htmt = np.nanmean(hetero) / denominator if denominator > 0 else None
            rows.append(
                {
                    "construct_1": left.name,
                    "construct_2": right.name,
                    "n": int(len(item_data[[*left.items, *right.items]].dropna())),
                    "missing": "listwise",
                    "htmt": finite_or_none(htmt),
                }
            )
    return rows


def run_cfa(item_data: pd.DataFrame, scales: list[ScaleConfig]) -> dict[str, Any]:
    aliased, model_scales, aliases = _safe_sem_data(item_data, scales)
    description = "\n".join(
        f"{latent} =~ {' + '.join(items)}" for _, latent, items in model_scales
    )
    _, fit, estimates = _fit_sem(description, aliased)
    all_items = list(dict.fromkeys(item for scale in scales for item in scale.items))
    complete_items = item_data[all_items].apply(pd.to_numeric, errors="coerce").dropna()
    loadings = _standardized_loading_rows(estimates, model_scales, aliases)
    reliability = _reliability_rows(loadings, complete_items, scales)
    weak_constructs = [scale.name for scale in scales if len(scale.items) < 3]
    notes = []
    if weak_constructs:
        notes.append(f"以下构念少于 3 个题项，识别与稳定性需谨慎: {', '.join(weak_constructs)}")
    if (fit.get("max_abs_latent_correlation") or 0) >= 0.90:
        notes.append("潜变量相关较高（≥0.90），请结合 HTMT 谨慎判断区分效度。")
    return {
        "model": description,
        "fit": fit,
        "loadings": loadings,
        "reliability": reliability,
        "htmt": _htmt_rows(complete_items, scales),
        "notes": notes,
    }


def _kmo_bartlett(values: np.ndarray) -> dict[str, Any]:
    n, p = values.shape
    correlation = np.corrcoef(values, rowvar=False)
    try:
        inverse = np.linalg.pinv(correlation)
        diagonal = np.sqrt(np.outer(np.diag(inverse), np.diag(inverse)))
        partial = -np.divide(inverse, diagonal, out=np.zeros_like(inverse), where=diagonal > 0)
        np.fill_diagonal(partial, 0)
        corr_squared = np.square(correlation)
        np.fill_diagonal(corr_squared, 0)
        partial_squared = np.square(partial)
        kmo = corr_squared.sum() / (corr_squared.sum() + partial_squared.sum())
    except np.linalg.LinAlgError:
        kmo = np.nan
    determinant = max(float(np.linalg.det(correlation)), np.finfo(float).tiny)
    bartlett_chi = -(n - 1 - (2 * p + 5) / 6) * np.log(determinant)
    bartlett_df = p * (p - 1) / 2
    return {
        "kmo": finite_or_none(kmo),
        "bartlett_chi_square": finite_or_none(bartlett_chi),
        "bartlett_df": int(bartlett_df),
        "bartlett_p": finite_or_none(stats.chi2.sf(bartlett_chi, bartlett_df)),
    }


def run_harman(item_data: pd.DataFrame, threshold: float) -> dict[str, Any]:
    numeric = item_data.apply(pd.to_numeric, errors="coerce")
    numeric = numeric.loc[:, numeric.nunique(dropna=True) > 1]
    numeric = numeric.dropna()
    numeric = numeric.loc[:, numeric.nunique(dropna=True) > 1]
    if numeric.shape[0] < 10 or numeric.shape[1] < 2:
        raise ValueError("Harman 检验的有效题项或样本不足")
    standardized = (numeric - numeric.mean()) / numeric.std(ddof=1)
    correlation = standardized.corr().to_numpy(dtype=float)
    eigenvalues = np.linalg.eigvalsh(correlation)[::-1]
    explained = eigenvalues / eigenvalues.sum() * 100
    first = float(explained[0])
    eigen_rows = [
        {
            "component": index,
            "eigenvalue": finite_or_none(value),
            "variance_percent": finite_or_none(explained[index - 1]),
            "cumulative_percent": finite_or_none(explained[:index].sum()),
        }
        for index, value in enumerate(eigenvalues, start=1)
    ]
    return {
        "method": "未旋转主成分法（PCA，传统 Harman 降维检验）",
        "missing": "listwise",
        "n": int(len(numeric)),
        "items": int(numeric.shape[1]),
        "first_component_percent": first,
        "threshold_percent": threshold,
        "above_threshold": bool(first >= threshold),
        "components_eigenvalue_gt_1": int((eigenvalues > 1).sum()),
        "diagnostics": _kmo_bartlett(numeric.to_numpy(dtype=float)),
        "eigenvalues": eigen_rows,
        "interpretation": (
            "第一主成分达到预设阈值，存在单一成分主导的明显风险。"
            if first >= threshold
            else "第一主成分未达到预设阈值，但该结果不能证明共同方法偏差不存在。"
        ),
    }


def run_ulmc(item_data: pd.DataFrame, scales: list[ScaleConfig]) -> dict[str, Any]:
    aliased, model_scales, aliases = _safe_sem_data(item_data, scales)
    trait_lines = [f"{latent} =~ {' + '.join(items)}" for _, latent, items in model_scales]
    all_aliases = [aliases[item] for scale in scales for item in scale.items]
    all_aliases = list(dict.fromkeys(all_aliases))
    if len(all_aliases) < 6 or len(model_scales) < 2:
        raise ValueError("ULMC 至少需要 2 个构念且合计不少于 6 个题项")
    method_lines = [f"method =~ {' + '.join(all_aliases)}"]
    method_lines.extend(f"method ~~ 0*{latent}" for _, latent, _ in model_scales)
    trait_description = "\n".join(trait_lines)
    ulmc_description = "\n".join([*trait_lines, *method_lines])

    _, trait_fit, trait_estimates = _fit_sem(trait_description, aliased)
    _, method_fit, method_estimates = _fit_sem(ulmc_description, aliased)
    alias_to_original = {alias: original for original, alias in aliases.items()}
    method_loadings: list[dict[str, Any]] = []
    for _, row in method_estimates[
        (method_estimates["op"] == "~") & (method_estimates["rval"] == "method")
    ].iterrows():
        alias = str(row["lval"])
        std_loading = finite_or_none(row.get("Est. Std"))
        method_loadings.append(
            {
                "item": alias_to_original.get(alias, alias),
                "method_loading": std_loading,
                "method_variance_share": finite_or_none(std_loading**2 if std_loading is not None else None),
            }
        )
    shares = [row["method_variance_share"] for row in method_loadings if row["method_variance_share"] is not None]
    comparison = {
        "delta_chi_square": finite_or_none(trait_fit["chi_square"] - method_fit["chi_square"]),
        "delta_df": finite_or_none(trait_fit["df"] - method_fit["df"]),
        "delta_cfi": finite_or_none(method_fit["cfi"] - trait_fit["cfi"]),
        "delta_tli": finite_or_none(method_fit["tli"] - trait_fit["tli"]),
        "delta_rmsea": finite_or_none(method_fit["rmsea"] - trait_fit["rmsea"]),
        "delta_srmr": finite_or_none(method_fit["srmr"] - trait_fit["srmr"]),
        "mean_method_variance_share": finite_or_none(np.mean(shares) if shares else None),
    }
    return {
        "trait_only_fit": trait_fit,
        "trait_method_fit": method_fit,
        "comparison": comparison,
        "method_loadings": method_loadings,
        "notes": [
            "方法因子与理论因子设为正交，并以首个方法载荷固定为 1 进行标定。",
            "ULMC 可能吸收措辞或反向题效应；模型改善不能单独证明共同方法偏差。",
            "新增方法因子在零假设下可能未识别，卡方差异仅作描述。",
        ],
    }
