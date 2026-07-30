from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor


MAX_BCA_ROWS = 2_500
MAX_BOOTSTRAP_RESAMPLED_ROWS = 10_000_000


class _RescaledFit:
    def __init__(
        self,
        fit: Any,
        predictor_means: np.ndarray,
        predictor_scales: np.ndarray,
    ):
        self._fit = fit
        size = len(predictor_scales) + 1
        self._transform = np.zeros((size, size), dtype=float)
        self._transform[0, 0] = 1.0
        if len(predictor_scales):
            self._transform[0, 1:] = -predictor_means / predictor_scales
            self._transform[1:, 1:] = np.diag(1 / predictor_scales)
        self.params = self._transform @ np.asarray(fit.params, dtype=float)
        self._covariance = (
            self._transform
            @ np.asarray(fit.cov_params(), dtype=float)
            @ self._transform.T
        )
        self.bse = np.sqrt(np.maximum(np.diag(self._covariance), 0))
        self.tvalues = np.divide(
            self.params,
            self.bse,
            out=np.full_like(self.params, np.nan),
            where=self.bse > 0,
        )
        if bool(getattr(fit, "use_t", True)):
            self.pvalues = 2 * stats.t.sf(np.abs(self.tvalues), fit.df_resid)
        else:
            self.pvalues = 2 * stats.norm.sf(np.abs(self.tvalues))

    def cov_params(self) -> np.ndarray:
        return self._covariance

    def conf_int(self, alpha: float = 0.05) -> np.ndarray:
        critical = (
            stats.t.ppf(1 - alpha / 2, self._fit.df_resid)
            if bool(getattr(self._fit, "use_t", True))
            else stats.norm.ppf(1 - alpha / 2)
        )
        return np.column_stack(
            [self.params - critical * self.bse, self.params + critical * self.bse]
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fit, name)


def finite_or_none(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def cronbach_alpha(items: pd.DataFrame) -> float | None:
    clean = items.apply(pd.to_numeric, errors="coerce").dropna()
    if clean.shape[0] < 3 or clean.shape[1] < 2:
        return None
    variances = clean.var(axis=0, ddof=1)
    total_variance = clean.sum(axis=1).var(ddof=1)
    if total_variance <= 0:
        return None
    count = clean.shape[1]
    return finite_or_none(count / (count - 1) * (1 - variances.sum() / total_variance))


def fit_ols(
    data: pd.DataFrame,
    outcome: str,
    predictors: Sequence[str],
    robust_se: str = "HC3",
    alpha: float = 0.05,
) -> tuple[Any, dict[str, Any]]:
    if not 0 < alpha < 1:
        raise ValueError("alpha 必须在 0 与 1 之间")
    columns = [outcome, *predictors]
    model_data = data.loc[:, columns].apply(pd.to_numeric, errors="coerce").dropna()
    if len(model_data) <= len(predictors) + 2:
        raise ValueError(f"模型 {outcome} 的有效样本不足")
    if model_data[outcome].nunique() <= 1:
        raise ValueError(f"因变量 {outcome} 没有变异")
    if not np.isfinite(model_data.to_numpy(dtype=float)).all():
        raise ValueError(f"模型 {outcome} 包含无穷值")
    constant = [name for name in predictors if model_data[name].nunique() <= 1]
    if constant:
        raise ValueError(f"预测变量没有变异: {', '.join(constant)}")

    predictor_values = model_data[list(predictors)].to_numpy(dtype=float)
    predictor_means = (
        predictor_values.mean(axis=0) if predictors else np.asarray([], dtype=float)
    )
    centered_predictors = predictor_values - predictor_means
    predictor_scales = (
        np.linalg.norm(centered_predictors, axis=0) / np.sqrt(len(model_data))
        if predictors
        else np.asarray([], dtype=float)
    )
    if np.any(predictor_scales <= np.finfo(float).eps):
        raise ValueError(f"模型 {outcome} 的预测变量没有足够变异")
    scaled_predictors = (
        pd.DataFrame(
            centered_predictors / predictor_scales,
            index=model_data.index,
            columns=list(predictors),
        )
        if predictors
        else model_data.loc[:, []]
    )
    x_matrix = sm.add_constant(scaled_predictors, has_constant="add")
    if np.linalg.matrix_rank(x_matrix.to_numpy(dtype=float)) < x_matrix.shape[1]:
        raise ValueError(f"模型 {outcome} 的预测变量存在完全共线性")
    raw_fit = sm.OLS(model_data[outcome], x_matrix).fit()
    scaled_fit = (
        raw_fit.get_robustcov_results(cov_type="HC3")
        if robust_se == "HC3"
        else raw_fit
    )
    fit = _RescaledFit(scaled_fit, predictor_means, predictor_scales)
    parameter_names = list(x_matrix.columns)
    params = np.asarray(fit.params)
    standard_errors = np.asarray(fit.bse)
    p_values = np.asarray(fit.pvalues)
    t_values = np.asarray(fit.tvalues)
    confidence = np.asarray(fit.conf_int(alpha=alpha))
    outcome_sd = float(model_data[outcome].std(ddof=1))

    coefficients: list[dict[str, Any]] = []
    for index, name in enumerate(parameter_names):
        beta = None
        if name != "const" and outcome_sd > 0:
            beta = float(params[index] * model_data[name].std(ddof=1) / outcome_sd)
        coefficients.append(
            {
                "term": name,
                "b": finite_or_none(params[index]),
                "se": finite_or_none(standard_errors[index]),
                "beta": finite_or_none(beta),
                "t": finite_or_none(t_values[index]),
                "p": finite_or_none(p_values[index]),
                "ci_low": finite_or_none(confidence[index, 0]),
                "ci_high": finite_or_none(confidence[index, 1]),
            }
        )

    vif: list[dict[str, Any]] = []
    if predictors:
        vif_values = sm.add_constant(
            scaled_predictors, has_constant="add"
        ).to_numpy(dtype=float)
        for index, name in enumerate(predictors):
            value = (
                1.0
                if len(predictors) == 1
                else variance_inflation_factor(vif_values, index + 1)
            )
            vif.append(
                {
                    "term": name,
                    "vif": finite_or_none(value),
                    "tolerance": finite_or_none(1 / value if value and value > 0 else None),
                }
            )

    influence = raw_fit.get_influence()
    cooks = np.asarray(influence.cooks_distance[0])
    leverage = np.asarray(influence.hat_matrix_diag)
    summary = {
        "outcome": outcome,
        "predictors": list(predictors),
        "n": int(fit.nobs),
        "r_squared": finite_or_none(raw_fit.rsquared),
        "adjusted_r_squared": finite_or_none(raw_fit.rsquared_adj),
        "f": finite_or_none(fit.fvalue),
        "f_p": finite_or_none(fit.f_pvalue),
        "aic": finite_or_none(raw_fit.aic),
        "bic": finite_or_none(raw_fit.bic),
        "robust_se": robust_se,
        "confidence_level": finite_or_none(1 - alpha),
        "coefficients": coefficients,
        "vif": vif,
        "diagnostics": {
            "max_cooks_distance": finite_or_none(cooks.max(initial=0)),
            "influential_count_cook_4n": int((cooks > (4 / len(model_data))).sum()),
            "max_leverage": finite_or_none(leverage.max(initial=0)),
        },
    }
    fit._epa_parameter_names = parameter_names
    fit._epa_model_data = model_data
    fit._epa_raw_fit = raw_fit
    return fit, summary


def parameter_index(fit: Any, name: str) -> int:
    return list(fit._epa_parameter_names).index(name)


def least_squares_coefficient(
    data: pd.DataFrame,
    outcome: str,
    predictors: Sequence[str],
    term: str,
) -> float:
    predictor_values = data[list(predictors)].to_numpy(dtype=float)
    if not np.isfinite(predictor_values).all():
        raise ValueError(f"模型 {outcome} 包含无穷值")
    predictor_means = predictor_values.mean(axis=0)
    centered_predictors = predictor_values - predictor_means
    predictor_scales = np.linalg.norm(centered_predictors, axis=0) / np.sqrt(len(data))
    if np.any(predictor_scales <= np.finfo(float).eps):
        raise ValueError(f"模型 {outcome} 的预测变量没有变异")
    matrix = np.column_stack(
        [np.ones(len(data)), centered_predictors / predictor_scales]
    )
    if np.linalg.matrix_rank(matrix) < matrix.shape[1]:
        raise ValueError(f"模型 {outcome} 的预测变量存在完全共线性")
    target = data[outcome].to_numpy(dtype=float)
    scaled_coefficients = np.linalg.lstsq(matrix, target, rcond=None)[0]
    coefficients = np.empty_like(scaled_coefficients)
    coefficients[1:] = scaled_coefficients[1:] / predictor_scales
    coefficients[0] = scaled_coefficients[0] - np.dot(
        coefficients[1:], predictor_means
    )
    names = ["const", *predictors]
    return float(coefficients[names.index(term)])


def bca_interval(
    estimate: float,
    bootstrap_values: np.ndarray,
    jackknife_values: np.ndarray,
    alpha: float,
) -> tuple[float, float]:
    values = bootstrap_values[np.isfinite(bootstrap_values)]
    jackknife = jackknife_values[np.isfinite(jackknife_values)]
    if len(values) < 50:
        raise ValueError("Bootstrap 成功样本少于 50，无法计算 BCa 区间")
    if len(jackknife) < 3 or len(jackknife) != len(jackknife_values):
        raise ValueError("BCa Jackknife 未全部成功，请改用 percentile 区间")

    probability = (np.sum(values < estimate) + 0.5 * np.sum(values == estimate)) / len(values)
    probability = np.clip(probability, 1 / (2 * len(values)), 1 - 1 / (2 * len(values)))
    z0 = stats.norm.ppf(probability)
    jack_mean = jackknife.mean()
    differences = jack_mean - jackknife
    denominator = 6 * np.power(np.square(differences).sum(), 1.5)
    acceleration = np.power(differences, 3).sum() / denominator if denominator else 0.0

    adjusted: list[float] = []
    for tail in (alpha / 2, 1 - alpha / 2):
        z_tail = stats.norm.ppf(tail)
        value = stats.norm.cdf(z0 + (z0 + z_tail) / (1 - acceleration * (z0 + z_tail)))
        adjusted.append(float(np.clip(value, 0, 1)))
    return tuple(np.quantile(values, adjusted))


def bootstrap_statistics(
    row_count: int,
    evaluator: Callable[[np.ndarray], dict[str, float]],
    samples: int,
    seed: int,
    alpha: float,
    interval: str,
) -> dict[str, dict[str, Any]]:
    if row_count * samples > MAX_BOOTSTRAP_RESAMPLED_ROWS:
        raise ValueError(
            "Bootstrap 计算量超过上限（完整案例数 × 抽样次数最多为 "
            f"{MAX_BOOTSTRAP_RESAMPLED_ROWS:,}）；请减少抽样次数或缩小样本"
        )
    if interval == "bca" and row_count > MAX_BCA_ROWS:
        raise ValueError(
            f"BCa 区间最多支持 {MAX_BCA_ROWS:,} 个完整案例；"
            "请改用 percentile 区间或缩小样本"
        )
    full_index = np.arange(row_count)
    estimates = evaluator(full_index)
    keys = list(estimates)
    rng = np.random.default_rng(seed)
    draws: dict[str, list[float]] = {key: [] for key in keys}
    failed = 0

    for _ in range(samples):
        index = rng.integers(0, row_count, row_count)
        try:
            values = evaluator(index)
            if not all(np.isfinite(values[key]) for key in keys):
                raise ValueError("non-finite bootstrap estimate")
            for key in keys:
                draws[key].append(float(values[key]))
        except (np.linalg.LinAlgError, ValueError):
            failed += 1

    successful = samples - failed
    if successful < max(50, int(samples * 0.8)):
        raise ValueError("Bootstrap 成功拟合比例低于 80%，结果不稳定")

    jackknife: dict[str, list[float]] = {key: [] for key in keys}
    if interval == "bca":
        for omitted in range(row_count):
            index = np.delete(full_index, omitted)
            try:
                values = evaluator(index)
                for key in keys:
                    jackknife[key].append(float(values[key]))
            except (np.linalg.LinAlgError, ValueError):
                for key in keys:
                    jackknife[key].append(np.nan)

    result: dict[str, dict[str, Any]] = {}
    for key in keys:
        boot = np.asarray(draws[key], dtype=float)
        if interval == "bca":
            low, high = bca_interval(
                float(estimates[key]), boot, np.asarray(jackknife[key], dtype=float), alpha
            )
        else:
            low, high = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
        result[key] = {
            "effect": key,
            "estimate": finite_or_none(estimates[key]),
            "boot_se": finite_or_none(boot.std(ddof=1)),
            "ci_low": finite_or_none(low),
            "ci_high": finite_or_none(high),
            "significant": bool(low > 0 or high < 0),
            "samples_requested": samples,
            "samples_successful": successful,
            "ci_method": interval,
        }
    return result


def flatten_table_rows(prefix: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**prefix, **row} for row in rows]
