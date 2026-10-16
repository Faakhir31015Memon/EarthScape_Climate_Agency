"""Cross-variable and cross-region correlation analysis.

These are ASSOCIATION measures only. Nothing in this module supports a causal
claim; every result carries that disclaimer because the platform brief forbids
presenting correlation as causation.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats

NOT_CAUSAL_NOTE = (
    "These coefficients measure linear/rank association between co-observed series. "
    "They are correlations, NOT causal claims: shared seasonality, synoptic-scale weather "
    "systems and station siting can produce high coefficients without any variable causing another."
)

MIN_OVERLAP = 30


def _pivot(tidy: pd.DataFrame, index_col: str, collapse_col: str,
           region: Optional[str], granularity: str) -> pd.DataFrame:
    frame = tidy[tidy["granularity"] == granularity]
    if region:
        frame = frame[frame["region"] == region]
    if frame.empty:
        return pd.DataFrame()
    # Mean-collapse repeated observations of the same slot (e.g. several stations).
    return (frame.groupby([frame["timestamp"].dt.floor("h"), collapse_col])["value"]
            .mean().unstack(collapse_col))


def variable_correlations(tidy: pd.DataFrame, region: Optional[str] = None,
                          granularity: str = "daily", min_overlap: int = MIN_OVERLAP) -> dict[str, Any]:
    """Pearson + Spearman matrices between climate variables (aligned on timestamps)."""
    pivot = _pivot(tidy, "timestamp", "variable", region, granularity)
    if pivot.shape[1] < 2:
        return {"available": False,
                "reason": f"fewer than two variables present for region={region or 'ALL'} at {granularity}",
                "note": NOT_CAUSAL_NOTE}
    pearson = pivot.corr(method="pearson")
    spearman = pivot.corr(method="spearman")
    overlap = pivot.notna()
    pairs = []
    cols = list(pivot.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            mask = overlap[a] & overlap[b]
            n = int(mask.sum())
            if n < min_overlap:
                pairs.append({"variables": [a, b], "n_overlap": n, "pearson": None, "spearman": None,
                              "skipped": f"overlap {n} < min_overlap {min_overlap}"})
                continue
            sub = pivot.loc[mask, [a, b]]
            pr = float(stats.pearsonr(sub[a], sub[b])[0])
            sp = float(stats.spearmanr(sub[a], sub[b])[0])
            pairs.append({"variables": [a, b], "n_overlap": n, "pearson": round(pr, 4),
                          "spearman": round(sp, 4)})
    return {
        "available": True,
        "region": region or "ALL",
        "granularity": granularity,
        "pearson": {a: {b: (None if pd.isna(v) else round(float(v), 4))
                        for b, v in row.items()} for a, row in pearson.iterrows()},
        "spearman": {a: {b: (None if pd.isna(v) else round(float(v), 4))
                         for b, v in row.items()} for a, row in spearman.iterrows()},
        "pairs": pairs,
        "min_overlap": min_overlap,
        "note": NOT_CAUSAL_NOTE,
    }


def region_correlations(tidy: pd.DataFrame, variable: str = "temperature",
                        granularity: str = "daily", min_overlap: int = MIN_OVERLAP) -> dict[str, Any]:
    """Pearson + Spearman between regions for one variable (aligned calendar)."""
    pivot = _pivot(tidy, "timestamp", "region", None, granularity)
    if variable not in pivot.columns and not pivot.empty:
        sub = tidy[(tidy["variable"] == variable) & (tidy["granularity"] == granularity)]
        pivot = (sub.groupby([sub["timestamp"].dt.floor("h"), "region"])["value"].mean().unstack("region"))
    if pivot.empty or pivot.shape[1] < 2:
        return {"available": False,
                "reason": f"fewer than two regions carry '{variable}' at {granularity}",
                "note": NOT_CAUSAL_NOTE}
    pivot = pivot.dropna(how="all")
    pearson = pivot.corr(method="pearson")
    spearman = pivot.corr(method="spearman")
    overlap = pivot.notna()
    pairs = []
    cols = list(pivot.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            mask = overlap[a] & overlap[b]
            n = int(mask.sum())
            if n < min_overlap:
                pairs.append({"regions": [a, b], "n_overlap": n, "pearson": None, "spearman": None,
                              "skipped": f"overlap {n} < min_overlap {min_overlap}"})
                continue
            sub = pivot.loc[mask, [a, b]]
            pr = float(stats.pearsonr(sub[a], sub[b])[0])
            sp = float(stats.spearmanr(sub[a], sub[b])[0])
            pairs.append({"regions": [a, b], "n_overlap": n, "pearson": round(pr, 4), "spearman": round(sp, 4)})
    return {
        "available": True,
        "variable": variable,
        "granularity": granularity,
        "pearson": {a: {b: (None if pd.isna(v) else round(float(v), 4))
                        for b, v in row.items()} for a, row in pearson.iterrows()},
        "spearman": {a: {b: (None if pd.isna(v) else round(float(v), 4))
                         for b, v in row.items()} for a, row in spearman.iterrows()},
        "pairs": pairs,
        "min_overlap": min_overlap,
        "note": NOT_CAUSAL_NOTE,
    }
