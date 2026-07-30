from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


OUTPUT = Path(__file__).with_name("demo_survey.csv")
SEED = 20260730


def standardized(values: np.ndarray) -> np.ndarray:
    return (values - values.mean()) / values.std(ddof=1)


def likert_items(
    latent: np.ndarray,
    method: np.ndarray,
    rng: np.random.Generator,
    count: int = 3,
) -> list[np.ndarray]:
    latent = standardized(latent)
    return [
        np.clip(
            np.rint(4 + 1.18 * (0.78 * latent + 0.18 * method + 0.60 * rng.normal(size=len(latent)))),
            1,
            7,
        ).astype(float)
        for _ in range(count)
    ]


def main() -> None:
    rng = np.random.default_rng(SEED)
    n = 320
    method = rng.normal(size=n)
    x = rng.normal(size=n)
    w = 0.20 * x + np.sqrt(1 - 0.20**2) * rng.normal(size=n)
    mediator = 0.52 * x + 0.18 * w + 0.22 * x * w + rng.normal(scale=0.74, size=n)
    outcome = 0.22 * x + 0.48 * mediator + 0.12 * w + rng.normal(scale=0.76, size=n)

    x_items = likert_items(x, method, rng)
    m_items = likert_items(mediator, method, rng)
    w_items = likert_items(w, method, rng)
    y_items = likert_items(outcome, method, rng)
    m_items[2] = 8 - m_items[2]

    frame = pd.DataFrame(
        {
            "受访者ID": [f"R{index:04d}" for index in range(1, n + 1)],
            "创新氛围1": x_items[0],
            "创新氛围2": x_items[1],
            "创新氛围3": x_items[2],
            "工作投入1": m_items[0],
            "工作投入2": m_items[1],
            "工作投入3": m_items[2],
            "领导支持1": w_items[0],
            "领导支持2": w_items[1],
            "领导支持3": w_items[2],
            "创新绩效1": y_items[0],
            "创新绩效2": y_items[1],
            "创新绩效3": y_items[2],
            "年龄": rng.integers(22, 56, size=n),
            "性别": rng.integers(0, 2, size=n),
        }
    )
    item_columns = frame.columns[1:13]
    missing_mask = rng.random((n, len(item_columns))) < 0.012
    frame.loc[:, item_columns] = frame.loc[:, item_columns].mask(missing_mask)
    frame.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
    print(f"Generated {OUTPUT} ({len(frame)} rows)")


if __name__ == "__main__":
    main()
