from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.effects_analysis import (
    analysis_variable_names,
    prepare_analysis_data,
    run_correlations,
    run_descriptives,
    run_mediation,
    run_moderated_mediation,
    run_moderation,
    run_regressions,
)
from app.schemas import AnalysisRequest


def test_reverse_scoring_and_scale_scores(demo_frame, demo_request):
    frame, item_data, quality = prepare_analysis_data(demo_frame, demo_request)
    source = demo_frame.loc[0, "工作投入3"]
    assert item_data.loc[0, "工作投入3"] == 8 - source
    expected = item_data.loc[0, ["工作投入1", "工作投入2", "工作投入3"]].mean()
    assert frame.loc[0, "工作投入"] == expected
    assert quality["input_rows"] == 320
    assert len(quality["scale_scores"]) == 4


def test_nonnumeric_item_cell_requires_an_explicit_missing_code(demo_frame, demo_request):
    invalid = demo_frame.copy()
    invalid["创新氛围1"] = invalid["创新氛围1"].astype(object)
    invalid.loc[0, "创新氛围1"] = "not-a-number"

    with pytest.raises(ValueError, match="非数值单元格"):
        prepare_analysis_data(invalid, demo_request)


def test_out_of_range_item_value_stops_analysis(demo_frame, demo_request):
    invalid = demo_frame.copy()
    invalid.loc[0, "创新氛围1"] = 99

    with pytest.raises(ValueError, match="超出"):
        prepare_analysis_data(invalid, demo_request)


def test_main_and_mediation_regressions_do_not_drop_unused_moderator(
    demo_frame, demo_request
):
    frame, _, _ = prepare_analysis_data(demo_frame, demo_request)
    frame[demo_request.roles.moderator] = np.nan
    expected_columns = [
        demo_request.roles.y,
        demo_request.roles.x,
        demo_request.roles.mediator,
        *demo_request.roles.controls,
    ]
    expected_n = len(frame[expected_columns].dropna())

    result = run_regressions(frame, demo_request)

    assert result["common_sample_n"] == expected_n


def test_descriptives_and_pairwise_correlations(demo_frame, demo_request):
    frame, _, _ = prepare_analysis_data(demo_frame, demo_request)
    variables = analysis_variable_names(frame, demo_request)
    descriptives = run_descriptives(frame, variables)
    correlations = run_correlations(frame, variables, "pearson", 0.05)
    assert {row["variable"] for row in descriptives} >= {"创新氛围", "创新绩效"}
    target = next(
        row
        for row in correlations["rows"]
        if row["variable_1"] == "创新氛围" and row["variable_2"] == "创新绩效"
    )
    assert target["n"] > 250
    assert 0 < target["r"] < 1
    assert target["ci_low"] < target["r"] < target["ci_high"]


def test_three_level_predictor_requires_confirmation_before_ols():
    rows = 60
    x = np.tile([1.0, 2.0, 3.0], rows // 3)
    frame = pd.DataFrame(
        {
            "x": x,
            "y": 1.5 + 0.6 * x + np.random.default_rng(7).normal(0, 0.2, rows),
        }
    )
    request_payload = {
        "dataset_id": "0" * 32,
        "roles": {"x": "x", "y": "y"},
        "analyses": {
            "cfa": False,
            "harman": False,
            "ulmc": False,
            "descriptives": False,
            "regression": True,
            "mediation": False,
            "moderation": False,
            "moderated_mediation": False,
        },
    }
    unconfirmed = AnalysisRequest.model_validate(request_payload)

    with pytest.raises(ValueError, match="人工确认"):
        run_regressions(frame, unconfirmed)

    confirmed = AnalysisRequest.model_validate(
        {
            **request_payload,
            "inference": {"treat_as_continuous": ["x"]},
        }
    )
    result = run_regressions(frame, confirmed)

    assert result["models"][0]["outcome"] == "y"


def test_bootstrap_effects_and_moderation_plot(tmp_path: Path, demo_frame, demo_request):
    frame, _, _ = prepare_analysis_data(demo_frame, demo_request)
    mediation = run_mediation(frame, demo_request)
    indirect = next(row for row in mediation["effects"] if row["effect"] == "indirect_ab")
    assert indirect["estimate"] > 0
    assert indirect["samples_successful"] == 200

    moderation = run_moderation(frame, demo_request, tmp_path)
    assert moderation["interaction"]["b"] > 0
    assert len(moderation["simple_slopes"]) == 3
    assert (tmp_path / "moderation_plot.png").is_file()
    assert (tmp_path / "moderation_plot.svg").is_file()

    moderated = run_moderated_mediation(frame, demo_request)
    index = next(
        row for row in moderated["effects"] if row["effect"] == "index_moderated_mediation"
    )
    assert np.isfinite(index["estimate"])
    assert index["samples_successful"] == 200


def test_johnson_neyman_boundaries_are_scale_invariant(tmp_path: Path):
    rng = np.random.default_rng(314)
    rows = 600
    x = rng.normal(1_000_000, 100_000, rows)
    w = rng.normal(900_000, 50_000, rows)
    x_centered = x - x.mean()
    w_centered = w - w.mean()
    y = (
        3
        + 2e-6 * x_centered
        + 1e-6 * w_centered
        + 1e-10 * x_centered * w_centered
        + rng.normal(0, 1, rows)
    )
    frame = pd.DataFrame({"x": x, "w": w, "y": y})
    request = AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "roles": {"x": "x", "y": "y", "moderator": "w"},
            "analyses": {
                "cfa": False,
                "harman": False,
                "ulmc": False,
                "descriptives": False,
                "regression": False,
                "mediation": False,
                "moderation": True,
                "moderated_mediation": False,
            },
            "inference": {"bootstrap_samples": 200},
        }
    )

    result = run_moderation(frame, request, tmp_path)

    assert result["interaction"]["p"] < 0.001
    assert len(result["johnson_neyman_boundaries"]) == 2
    assert all(
        result["observed_moderator_range"][0]
        <= boundary
        <= result["observed_moderator_range"][1]
        for boundary in result["johnson_neyman_boundaries"]
    )
    scaled = frame.copy()
    scaled["w"] *= 1e8
    scaled_dir = tmp_path / "scaled"
    scaled_dir.mkdir()
    scaled_result = run_moderation(scaled, request, scaled_dir)
    assert np.allclose(
        np.asarray(scaled_result["johnson_neyman_boundaries"]) / 1e8,
        np.asarray(result["johnson_neyman_boundaries"]),
        rtol=1e-7,
    )


def test_ols_workflows_reject_binary_outcomes_and_moderators(tmp_path: Path):
    rng = np.random.default_rng(77)
    frame = pd.DataFrame(
        {
            "x": rng.normal(size=80),
            "y": rng.normal(size=80),
            "binary_y": rng.integers(0, 2, size=80),
            "binary_w": rng.integers(0, 2, size=80),
        }
    )
    analyses = {
        "cfa": False,
        "harman": False,
        "ulmc": False,
        "descriptives": False,
        "regression": False,
        "mediation": False,
        "moderation": True,
        "moderated_mediation": False,
    }
    binary_outcome = AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "roles": {"x": "x", "y": "binary_y", "moderator": "y"},
            "analyses": analyses,
            "inference": {"bootstrap_samples": 200},
        }
    )
    binary_moderator = AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "roles": {"x": "x", "y": "y", "moderator": "binary_w"},
            "analyses": analyses,
            "inference": {"bootstrap_samples": 200},
        }
    )

    with pytest.raises(ValueError, match="连续变量"):
        run_moderation(frame, binary_outcome, tmp_path)
    with pytest.raises(ValueError, match="连续变量"):
        run_moderation(frame, binary_moderator, tmp_path)


def test_model_14_recovers_known_second_stage_moderated_mediation():
    rng = np.random.default_rng(140)
    rows = 900
    x = rng.normal(size=rows)
    w = rng.normal(size=rows)
    m = 0.6 * x + rng.normal(0, 0.8, rows)
    y = 0.2 * x + 0.5 * m + 0.3 * m * w + 0.1 * w + rng.normal(0, 0.8, rows)
    frame = pd.DataFrame({"x": x, "m": m, "w": w, "y": y})
    request = AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "roles": {"x": "x", "y": "y", "mediator": "m", "moderator": "w"},
            "analyses": {
                "cfa": False,
                "harman": False,
                "ulmc": False,
                "descriptives": False,
                "regression": False,
                "mediation": False,
                "moderation": False,
                "moderated_mediation": True,
                "moderated_stage": "second",
            },
            "inference": {
                "bootstrap_samples": 200,
                "confidence_interval": "percentile",
                "seed": 140,
            },
        }
    )

    result = run_moderated_mediation(frame, request)
    effects = {row["effect"]: row for row in result["effects"]}

    assert result["template"].startswith("PROCESS Model 14")
    assert effects["index_moderated_mediation"]["estimate"] == pytest.approx(0.18, abs=0.05)
    assert effects["indirect_low"]["estimate"] < effects["indirect_mean"]["estimate"]
    assert effects["indirect_mean"]["estimate"] < effects["indirect_high"]["estimate"]
