from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.schemas import AnalysisRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo_frame() -> pd.DataFrame:
    return pd.read_csv(PROJECT_ROOT / "examples" / "demo_survey.csv", encoding="utf-8-sig")


@pytest.fixture
def demo_request() -> AnalysisRequest:
    return AnalysisRequest.model_validate(
        {
            "dataset_id": "0" * 32,
            "missing_codes": ["", 999],
            "scales": [
                {"name": "创新氛围", "items": ["创新氛围1", "创新氛围2", "创新氛围3"], "minimum": 1, "maximum": 7},
                {"name": "工作投入", "items": ["工作投入1", "工作投入2", "工作投入3"], "reverse_items": ["工作投入3"], "minimum": 1, "maximum": 7},
                {"name": "领导支持", "items": ["领导支持1", "领导支持2", "领导支持3"], "minimum": 1, "maximum": 7},
                {"name": "创新绩效", "items": ["创新绩效1", "创新绩效2", "创新绩效3"], "minimum": 1, "maximum": 7},
            ],
            "roles": {
                "x": "创新氛围",
                "y": "创新绩效",
                "mediator": "工作投入",
                "moderator": "领导支持",
                "controls": ["年龄", "性别"],
            },
            "analyses": {
                "cfa": True,
                "harman": True,
                "ulmc": True,
                "descriptives": True,
                "regression": True,
                "mediation": True,
                "moderation": True,
                "moderated_mediation": True,
                "moderated_stage": "first",
                "correlation": "pearson",
            },
            "inference": {
                "bootstrap_samples": 200,
                "confidence_interval": "percentile",
                "seed": 20260730,
                "robust_se": "HC3",
            },
        }
    )
