"""Machine-learning layer for EarthScape Climate Agency.

Modules:
``features``  time-series feature engineering (leakage-safe, train-only fits)
``training``  the reproducible training pipeline
``inference`` recursive multi-step forecasting + persistence helpers
``evaluation`` metrics and per-run reports
``anomaly``   anomaly detection enrichment (delegates to common.analytics)
"""
