from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ScaleConfig(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    items: list[str] = Field(min_length=2, max_length=100)
    reverse_items: list[str] = Field(default_factory=list, max_length=100)
    minimum: float = 1
    maximum: float = 5
    min_valid_ratio: float = Field(default=0.8, ge=0.1, le=1)

    @field_validator("items")
    @classmethod
    def unique_items(cls, value: list[str]) -> list[str]:
        invalid = [item for item in value if not item or len(item) > 128]
        if invalid:
            raise ValueError("题项列名必须为 1 至 128 个字符")
        if len(set(value)) != len(value):
            raise ValueError("量表题项不能重复")
        return value

    @field_validator("reverse_items")
    @classmethod
    def unique_reverse_items(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("反向题不能重复")
        return value

    @model_validator(mode="after")
    def validate_reverse_items(self) -> "ScaleConfig":
        unknown = set(self.reverse_items) - set(self.items)
        if unknown:
            raise ValueError(f"反向题不属于该量表: {', '.join(sorted(unknown))}")
        if self.minimum >= self.maximum:
            raise ValueError("量表最小值必须小于最大值")
        return self


class RoleConfig(BaseModel):
    x: str = Field(min_length=1, max_length=128)
    y: str = Field(min_length=1, max_length=128)
    mediator: str | None = None
    moderator: str | None = None
    controls: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("mediator", "moderator")
    @classmethod
    def valid_optional_role(cls, value: str | None) -> str | None:
        if value is not None and not 1 <= len(value) <= 128:
            raise ValueError("变量名必须为 1 至 128 个字符")
        return value

    @field_validator("controls")
    @classmethod
    def valid_control_names(cls, value: list[str]) -> list[str]:
        if any(not name or len(name) > 128 for name in value):
            raise ValueError("控制变量名必须为 1 至 128 个字符")
        return value

    @model_validator(mode="after")
    def validate_distinct_roles(self) -> "RoleConfig":
        roles = [self.x, self.y, self.mediator, self.moderator]
        assigned = [value for value in roles if value]
        if len(set(assigned)) != len(assigned):
            raise ValueError("X、Y、M、W 必须使用不同变量")
        if len(set(self.controls)) != len(self.controls):
            raise ValueError("控制变量不能重复")
        overlap = set(self.controls) & set(assigned)
        if overlap:
            raise ValueError(f"控制变量不能同时作为 X/Y/M/W: {', '.join(sorted(overlap))}")
        return self


class AnalysisOptions(BaseModel):
    cfa: bool = True
    harman: bool = True
    ulmc: bool = True
    descriptives: bool = True
    regression: bool = True
    mediation: bool = True
    moderation: bool = True
    moderated_mediation: bool = True
    moderated_stage: Literal["first", "second"] = "first"
    correlation: Literal["pearson", "spearman"] = "pearson"


class InferenceConfig(BaseModel):
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    bootstrap_samples: int = Field(default=5000, ge=200, le=20000)
    seed: int = Field(default=20260730, ge=0, le=2**63 - 1)
    confidence_interval: Literal["percentile", "bca"] = "bca"
    robust_se: Literal["HC3", "classical"] = "HC3"
    harman_threshold: float = Field(default=40.0, ge=0, le=100)


class AnalysisRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sheet_name: str | None = None
    missing_codes: list[str | float | int] = Field(
        default_factory=lambda: ["", 999], max_length=50
    )
    scales: list[ScaleConfig] = Field(default_factory=list, max_length=50)
    roles: RoleConfig
    analyses: AnalysisOptions = Field(default_factory=AnalysisOptions)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)

    @field_validator("missing_codes")
    @classmethod
    def valid_missing_codes(
        cls, value: list[str | float | int]
    ) -> list[str | float | int]:
        if any(isinstance(code, str) and len(code) > 128 for code in value):
            raise ValueError("单个缺失值编码不能超过 128 个字符")
        return value

    @model_validator(mode="after")
    def validate_required_roles(self) -> "AnalysisRequest":
        enabled = (
            self.analyses.cfa,
            self.analyses.harman,
            self.analyses.ulmc,
            self.analyses.descriptives,
            self.analyses.regression,
            self.analyses.mediation,
            self.analyses.moderation,
            self.analyses.moderated_mediation,
        )
        if not any(enabled):
            raise ValueError("至少需要启用一项分析")
        if self.analyses.cfa or self.analyses.harman or self.analyses.ulmc:
            if not self.scales:
                raise ValueError("CFA 与共同方法偏差检验至少需要一个量表")
        if self.analyses.mediation or self.analyses.moderated_mediation:
            if not self.roles.mediator:
                raise ValueError("中介分析需要指定 M")
        if self.analyses.moderation or self.analyses.moderated_mediation:
            if not self.roles.moderator:
                raise ValueError("调节分析需要指定 W")
        names = [scale.name for scale in self.scales]
        if len(set(names)) != len(names):
            raise ValueError("量表名称不能重复")
        configured_items = [item for scale in self.scales for item in scale.items]
        if len(configured_items) > 300:
            raise ValueError("配置的量表题项总数不能超过 300")
        duplicate_items = sorted(
            item for item, count in Counter(configured_items).items() if count > 1
        )
        if duplicate_items:
            raise ValueError(f"题项不能同时属于多个量表: {', '.join(duplicate_items)}")
        overlap = set(names) & set(configured_items)
        if overlap:
            raise ValueError(f"量表名称不能与题项列名相同: {', '.join(sorted(overlap))}")
        configured_names = [
            *names,
            *configured_items,
            self.roles.x,
            self.roles.y,
            self.roles.mediator,
            self.roles.moderator,
            *self.roles.controls,
        ]
        reserved = sorted(
            {name for name in configured_names if name and name.startswith("__epa_")}
        )
        if reserved:
            raise ValueError(f"变量名使用了系统保留前缀 __epa_: {', '.join(reserved)}")
        return self
