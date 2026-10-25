"""Per-run metrics reports written next to the artifacts in ``settings.MODEL_STORE``.

Both Markdown (for the documentation agent) and JSON (for tooling) are produced
for every model version; the report only ever contains numbers measured during
that run.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings

from common import storage_paths


def reports_dir() -> Path:
    directory = Path(settings.MODEL_STORE) / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def write_run_report(model_document: Dict[str, Any], run_info: Dict[str, Any],
                     comparison_table: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    """Write ``<name>-v<version>.md``/``.json`` under MODEL_STORE/reports."""
    name = str(model_document.get("name", "model"))
    version = model_document.get("version", 0)
    slug = f"{name}-v{version}".replace("/", "-").replace(" ", "-")
    metrics = model_document.get("evaluation_metrics") or {}
    period = model_document.get("training_period") or {}

    lines: List[str] = [
        f"# Training report: {name} v{version}",
        "",
        f"- Algorithm: `{model_document.get('algorithm')}`",
        f"- Target variable: `{model_document.get('target_variable')}` "
        f"({run_info.get('target_unit', '')})".rstrip(),
        f"- Status: `{model_document.get('status')}`",
        f"- Trained: {model_document.get('created_at')}",
        f"- Duration: {_fmt(model_document.get('duration_seconds'))} s",
        f"- Training period: {period.get('start')} .. {period.get('end')} "
        f"({len(period.get('locations') or [])} locations, "
        f"regions: {', '.join(period.get('regions') or []) or 'n/a'})",
        f"- Rows: train {_fmt(model_document.get('train_size'))} / "
        f"test {_fmt(model_document.get('test_size'))}",
        f"- Split strategy: {metrics.get('split_strategy')} (no shuffling)",
        "",
        "## Holdout metrics (multi-step recursive, real observations only)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| MAE | {_fmt(metrics.get('mae'))} |",
        f"| RMSE | {_fmt(metrics.get('rmse'))} |",
        f"| MAPE (%) | {_fmt(metrics.get('mape_or_null'))} |",
        f"| R² | {_fmt(metrics.get('r2'))} |",
        f"| Naive-baseline MAE | {_fmt(metrics.get('baseline_mae'))} |",
        f"| Seasonal-naive baseline MAE | {_fmt(metrics.get('seasonal_baseline_mae'))} |",
        f"| Skill vs naive baseline | {_fmt(metrics.get('skill_vs_baseline'))} |",
        f"| Evaluated horizon | {_fmt(metrics.get('horizon_days'))} days |",
        "",
        f"Uncertainty method: {_fmt(model_document.get('uncertainty_method'))}",
        "",
    ]
    if comparison_table:
        lines.append("## Holdout comparison (same split, same evaluation days)")
        lines.append("")
        lines.append("| Model | MAE | RMSE | MAPE % | R² | Skill vs naive |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in comparison_table:
            lines.append(
                f"| {row.get('model')} | {_fmt(row.get('mae'))} | {_fmt(row.get('rmse'))} | "
                f"{_fmt(row.get('mape_pct'))} | {_fmt(row.get('r2'))} | {_fmt(row.get('skill_vs_naive'))} |")
        lines.append("")
    importance = model_document.get("feature_importance") or []
    if importance:
        lines.append("## Top features")
        lines.append("")
        for item in importance[:10]:
            lines.append(f"- `{item.get('feature')}`: {_fmt(item.get('importance'))}")
        lines.append("")
    if run_info.get("notes"):
        lines.append(f"## Run notes\n\n{run_info['notes']}\n")
    lines.append("## Limitations")
    lines.append("")
    for limitation in model_document.get("limitations") or []:
        lines.append(f"- {limitation}")
    lines.append("")

    markdown_path = reports_dir() / f"{slug}.md"
    json_path = reports_dir() / f"{slug}.json"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    payload = {
        "model": {k: v for k, v in model_document.items() if k not in ("feature_definition",)},
        "run": run_info,
        "comparison_table": comparison_table,
        "report_uri": storage_paths.stored_key(markdown_path),
    }
    payload.pop("_id", None)
    json_path.write_text(json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False),
                         encoding="utf-8")
    return {"markdown": storage_paths.stored_key(markdown_path),
            "json": storage_paths.stored_key(json_path)}


__all__ = ["write_run_report", "reports_dir"]
