# EarthScape Climate Agency - Machine Learning Layer

Models here forecast **daily weather state** at monitored stations from real
stored observations (`climate_observations`). They are not climate-change
detectors: a forecast describes day-to-day atmospheric variability, while
climate trends are long-run statistics of that variability. The two are kept
apart in every API response and report.

## Methodology

1. **Data** - only documents already stored in MongoDB Atlas
   (`climate_observations`, canonical metric fields per
   `backend/common/analytics.py`). If the collection holds nothing for a
   scope/metric, training raises `InsufficientData` and the API reports
   "no training data available". Nothing is invented: `train_model()` reads the
   observation repository and has no parameter that accepts a file, so a
   synthetic fixture cannot reach a model even by accident.
2. **Features** (`ml/features/build.py`) - per location, in chronological
   order: calendar terms (year, month, day, day-of-year and month sin/cos),
   geography (lat/lon), target lags (1/7/14/30 days), rolling mean/std/min/max
   over 7/30/90 calendar days ending strictly before the row, region+month
   climatology baseline and climatology anomaly (mean of past-window value
   minus its month baseline), covariate lag-1 values, and missing-data
   indicators (`missing_lag_*`, `*_available`, `roll_count_*`,
   `days_since_last_obs`, `obs_density_365`).
3. **Split** - strictly chronological: the oldest ~80% of observation days
   train, the newest ~20% is the holdout. No shuffling anywhere.
4. **Modeling** - a single-step regressor per algorithm candidate (Ridge with
   train-only median imputation + scaling, RandomForest, XGBoost when
   installed). Multi-day forecasts are produced **recursively**: the model's
   own prediction for day *d* is fed back as history before predicting *d+1*.
   The holdout evaluation uses the identical recursion, so the reported MAE
   and RMSE are honest multi-step errors, not optimistic one-step errors.
5. **Baselines** - naive persistence (last value before the origin) and
   seasonal-naive (value 365 days earlier). Skill vs baseline = 1 - MAE /
   baseline MAE; a negative skill means the model is worse than just waiting,
   and the registry says so.
6. **Metrics** (`ml/evaluation/metrics.py`) - MAE, RMSE, R2, MAPE only when
   every holdout actual is strictly positive (otherwise `mape_or_null: null`).
7. **Uncertainty** (`ml/evaluation`) - empirical residual quantiles (2.5%/97.5%)
   grouped by lead time from the same holdout recursion, frozen in the
   artifact. When the holdout is too small, `uncertainty.available` is false
   with an explicit note instead of a made-up band. Bands are historical error
   envelopes, not probability guarantees.
8. **Persistence** - artifact (joblib) under `settings.MODEL_STORE/models/`,
   markdown + JSON report under `MODEL_STORE/reports/`, registry document in
   `model_registry` (unique name+version), prediction documents in
   `predictions`.

## Leakage controls

* every feature at date *d* uses only values dated `< d` (verified by the
  shared code path: training rows and recursive inference call the same
  `LocationHistory.features_at`);
* climatology baselines and imputations are fitted on the training window
  only and frozen in the artifact (`feature_definition.leakage_controls`);
* chronological split, no shuffling; the holdout never contributes to fitting;
* missing values are never replaced by zero; they stay NaN with indicator
  columns (or a fitted train median for the linear model only).

## Retraining

`POST /api/v1/models/train` with
`{target_variable, horizon_days, regions[]|location_codes[], start, end, algorithms[], min_train_rows}`
returns 202; the run executes in the bounded `common.tasks.pool()` thread pool,
transitions `training -> candidate | failed` in `model_registry` and can be
followed via `GET /api/v1/models/training-status` or the detail endpoint.
`POST /api/v1/models/<id>/retrain` repeats the stored `training_options` under
the next version. Activation (`.../activate`) is administrator-only and
retires the previous active version of the same model name.

Horizon requests are capped by `FORECAST_MAX_HORIZON_DAYS` (default 45).

## Metrics contract

`evaluation_metrics` always reports: `mae`, `rmse`, `mape_or_null`, `r2`,
`baseline_mae` (naive), `seasonal_baseline_mae`, `skill_vs_baseline`,
`horizon_days` and `split_strategy: "chronological"` measured on real data.
The per-run markdown table compares every candidate and both baselines on the
same holdout days.

## Honest limitations

* a pooled daily statistical model over station data is interpolation of the
  stored record, not atmospheric physics; regime shifts beyond the knowledge
  cutoff are not learned;
* recursive multi-step error compounds with lead time;
* region-scope forecasts are the mean of per-station point forecasts -
  station values are not a regional reanalysis;
* correlation in features or anomalies is co-variation only; the platform
  never infers causation from it;
* anomaly explanations (`ml/anomaly/detector.py`) describe the observable
  deviation only;
* precipitation predictions are clamped at 0 (disclosed in caveats).
