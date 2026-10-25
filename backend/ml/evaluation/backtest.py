"""Rolling-origin (walk-forward) evaluation.

For each fold k the model is refit on every row strictly before the fold's
forecast origins and evaluated on the fold's origin block. Training targets
never overlap the evaluation targets (row q's target is s(q+H), so training on
positions < p0 uses only observations before the fold's first target at p0+H).
Preprocessing for every fold is refit inside the factory on the fold's train
slice only. Dashboard numbers should come from this out-of-sample aggregate,
not from a single lucky split.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from . import metrics as M

# factory(X_train, y_train, ctx) -> predict(X_test) -> np.ndarray
ModelFactory = Callable[[pd.DataFrame, pd.Series, dict[str, Any]], Callable[[pd.DataFrame], np.ndarray]]


def _fold_boundaries(n_positions: int, warmup: int, folds: int) -> list[tuple[int, int]]:
    eval_rows = n_positions - warmup
    if eval_rows < folds:
        folds = max(1, eval_rows)
    block = max(1, eval_rows // folds)
    bounds: list[tuple[int, int]] = []
    start = warmup
    for _ in range(folds):
        end = min(start + block, n_positions)
        if start >= n_positions:
            break
        bounds.append((start, end))
        start = end
    if bounds:  # last fold absorbs the remainder
        last_start, _ = bounds[-1]
        bounds[-1] = (last_start, n_positions)
    return bounds


def walk_forward(X: pd.DataFrame, y: pd.Series, model_factories: dict[str, ModelFactory],
                 horizon_steps: int, folds: int = 5, warmup_train_rows: int = 240,
                 series: Optional[pd.Series] = None) -> dict[str, Any]:
    """Walk-forward evaluate each named model over N chronological folds."""
    y = y.dropna()
    X = X.loc[y.index]
    n = len(y)
    if n <= warmup_train_rows + 2:
        return {"available": False,
                "reason": f"only {n} valid (lag-completed, targeted) rows; need > {warmup_train_rows + 2} "
                          f"for {folds} walk-forward folds with this warmup",
                "folds": [], "aggregate": {}}

    warmup = max(warmup_train_rows, n // 4)
    warmup = min(warmup, n - 2)
    bounds = _fold_boundaries(n, warmup, folds)
    positions = list(range(n))
    per_model_folds: dict[str, list[dict[str, Any]]] = {name: [] for name in model_factories}
    fold_records: list[dict[str, Any]] = []
    ctx = {"series": series, "horizon_steps": horizon_steps}

    for k, (p0, p1) in enumerate(bounds):
        train_pos = positions[:p0]
        eval_pos = positions[p0:p1]
        X_train, y_train = X.iloc[train_pos], y.iloc[train_pos]
        X_test, y_test = X.iloc[eval_pos], y.iloc[eval_pos]
        fold = {"fold": k + 1,
                "train_start": str(X.index[0]), "train_end": str(X.index[p0 - 1]),
                "eval_origin_start": str(X.index[p0]), "eval_origin_end": str(X.index[p1 - 1]),
                "eval_rows": len(eval_pos)}
        for name, factory in model_factories.items():
            try:
                predict = factory(X_train, y_train, ctx)
                pred = np.asarray(predict(X_test), dtype="float64")
                fold_metrics = {"mae": M.mae(y_test.to_numpy(), pred),
                                "rmse": M.rmse(y_test.to_numpy(), pred)}
                per_model_folds[name].append({**fold, **fold_metrics})
            except Exception as exc:
                per_model_folds[name].append({**fold, "error": str(exc)})
        fold_records.append(fold)

    aggregate: dict[str, Any] = {}
    baseline_mae = None
    for base_name in ("naive", "seasonal_naive"):
        if base_name in per_model_folds and per_model_folds[base_name]:
            maes = [f["mae"] for f in per_model_folds[base_name] if "mae" in f]
            if maes:
                aggregate.setdefault("baseline", {"algorithm": base_name,
                                                  "mean_fold_mae": float(np.mean(maes))})
                baseline_mae = float(np.mean(maes))
                break
    for name, fold_list in per_model_folds.items():
        maes = [f["mae"] for f in fold_list if "mae" in f]
        rmses = [f["rmse"] for f in fold_list if "rmse" in f]
        errors = [f.get("error") for f in fold_list if "error" in f]
        entry: dict[str, Any] = {"folds_evaluated": len(maes),
                                 "mean_fold_mae": float(np.mean(maes)) if maes else None,
                                 "std_fold_mae": float(np.std(maes)) if maes else None,
                                 "mean_fold_rmse": float(np.mean(rmses)) if rmses else None,
                                 "min_fold_mae": float(np.min(maes)) if maes else None,
                                 "max_fold_mae": float(np.max(maes)) if maes else None}
        if errors:
            entry["fold_errors"] = errors
        if baseline_mae and entry["mean_fold_mae"] is not None:
            entry["skill_vs_baseline"] = M.skill_vs_baseline(entry["mean_fold_mae"], baseline_mae)
        aggregate[name] = entry
    return {
        "available": True,
        "horizon_steps": horizon_steps,
        "folds": len(fold_records),
        "warmup_train_rows": int(bounds[0][0]) if bounds else warmup,
        "per_fold": per_model_folds,
        "aggregate": aggregate,
        "note": "Each fold retrains strictly on data before its origins; metrics are out-of-sample.",
    }
