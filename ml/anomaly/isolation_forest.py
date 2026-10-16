"""Optional IsolationForest detector (guarded import; degrades cleanly).

Fits on the temporal feature matrix built ONLY from data at or before
``origin`` (the "training window"), then scores recent observations. A
detection carries the largest-deviation features as its explanation. Because
IsolationForest's contamination is heuristic, scores are reported as
"unusual" with the feature evidence — never as proof of anything.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from ..features.time_features import add_time_features
from . import severity as S
from .explain import explain_detection

MIN_FIT_ROWS = 120


def isolation_forest_status() -> dict[str, Any]:
    try:
        from sklearn.ensemble import IsolationForest  # noqa: F401
        return {"available": True, "reason": None}
    except Exception as exc:
        return {"available": False, "reason": f"IsolationForest unavailable: {exc}"}


def isolation_forest_anomalies(series: pd.Series, variable: str, region: str, unit: str = "1",
                               origin: Optional[str] = None, fit_rows: Optional[int] = None,
                               contamination: str = "auto",
                               statics: Optional[dict[str, float]] = None) -> dict[str, Any]:
    status = isolation_forest_status()
    if not status["available"]:
        return {"status": "unavailable", "reason": status["reason"], "detections": []}
    from sklearn.ensemble import IsolationForest
    from sklearn.impute import SimpleImputer

    observed = series.dropna()
    if origin:
        cutoff = pd.to_datetime(origin, utc=True)
        observed = observed[observed.index <= cutoff]
    if observed.empty:
        return {"status": "no_data", "detections": [], "reason": "no observed values"}

    full = series.reindex(observed.index)  # observed slots only, in order
    X = add_time_features(pd.DataFrame({"value": full}), statics=statics)
    imputer = SimpleImputer(strategy="median")
    X_filled = pd.DataFrame(imputer.fit_transform(X), columns=X.columns, index=X.index)

    n = len(X_filled)
    fit_rows = min(fit_rows or max(MIN_FIT_ROWS, int(n * 0.7)), n - 1)
    if fit_rows < MIN_FIT_ROWS:
        return {"status": "insufficient_data",
                "reason": f"IsolationForest needs >= {MIN_FIT_ROWS} rows to fit the normal-behaviour "
                          f"model; only {fit_rows} available after origin cutoff",
                "detections": []}
    fit_slice = X_filled.iloc[:fit_rows]
    score_slice = X_filled.iloc[fit_rows:]
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=0)
    model.fit(fit_slice)
    scores = model.score_samples(score_slice)
    labels = model.predict(score_slice)
    train_scores = model.score_samples(fit_slice)
    # robust z against the fit-window score distribution
    med = float(np.median(train_scores))
    mad = float(np.median(np.abs(train_scores - med))) * 1.4826
    feature_stds = fit_slice.std(numeric_only=True).replace(0, np.nan)

    detections = []
    for (ts, score, label) in zip(score_slice.index, scores, labels):
        if label != -1:
            continue
        row = score_slice.loc[ts]
        devs = ((row - fit_slice.median()) / feature_stds).abs()
        top = devs.dropna().sort_values(ascending=False).head(3)
        evidence = [f"{name} is {val:.1f} train-sigma away from its median"
                    for name, val in top.items() if np.isfinite(val)]
        sigma_from_scores = (float(score) - med) / mad if mad > 1e-12 else None
        sev = S.severity_for(sigma_from_scores, breach_level=None)
        if sev == "none":
            sev = "info"   # IsolationForest flag but undefined score spread
        detections.append(explain_detection(
            value=float(series.loc[ts]) if ts in series.index and pd.notna(series.loc[ts])
                    else float(row.get("lag_1", np.nan)),
            baseline=None, sigma=sigma_from_scores, timestamp=ts, region=region,
            variable=variable, unit=unit, method="isolation_forest", severity=sev,
            evidence=["score %.3f below fit-window median score %.3f" % (score, med)] + evidence))
    detections.sort(key=lambda d: S.rank(d["severity"]), reverse=True)
    return {
        "status": "ok", "region": region, "variable": variable,
        "fit_rows": int(fit_rows), "scored_rows": int(len(score_slice)),
        "flagged": len(detections), "detections": detections,
        "note": "Unusual multivariate behaviour relative to the fit window; feature deviations are "
                "listed as observable evidence, not causes.",
    }
