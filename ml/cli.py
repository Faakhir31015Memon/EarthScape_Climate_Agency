"""Runnable CLI for the EarthScape ML package. Every command prints JSON on stdout.

Examples:
    python ml/cli.py train --target temperature --granularity daily --horizon 30 [--region Karachi]
    python ml/cli.py evaluate --target temperature --region Karachi --horizon 30 --folds 5
    python ml/cli.py forecast --model temperature-Karachi-daily-h30@v20261016T120000Z \
        --region Karachi --target temperature --horizon 30 --json out.json
    python ml/cli.py anomalies --region Karachi --variable temperature --method statistical
    python ml/cli.py registry --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # executed as a plain script: bootstrap package import
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ml import mlkit  # noqa: E402


def _emit(payload: Any, out_json: str | None) -> None:
    text = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    if out_json:
        Path(out_json).write_text(text, encoding="utf-8")
    print(text)


def _load_tidy(args: argparse.Namespace):
    import pandas as pd
    if getattr(args, "csv", None):
        return mlkit.load_observations(files=[Path(p) for p in args.csv])
    return mlkit.load_observations()


def cmd_train(args: argparse.Namespace) -> None:
    tidy = _load_tidy(args)
    regions = [args.region] if args.region else sorted(
        {r for r in tidy["region"].unique().tolist()} if not tidy.empty else [])
    reports = []
    for region in regions:
        reports.append(mlkit.train_model(args.target, region, args.granularity,
                                          args.horizon, args.folds,
                                          save=not args.no_save, tidy=tidy))
    _emit({"command": "train", "targets": len(regions), "results": reports}, args.json)


def cmd_evaluate(args: argparse.Namespace) -> None:
    tidy = _load_tidy(args)
    if args.model:  # stored evaluation, no retraining
        _emit({"command": "evaluate", "mode": "stored",
               "result": mlkit.evaluate_model(args.model)}, args.json)
        return
    if not args.region or not args.target:
        raise SystemExit("evaluate needs --model <ref> or (--target and --region)")
    result = mlkit.train_model(args.target, args.region, args.granularity,
                               args.horizon, args.folds, save=False, tidy=tidy)
    _emit({"command": "evaluate", "mode": "walk_forward_ladder", "result": result}, args.json)


def cmd_forecast(args: argparse.Namespace) -> None:
    result = mlkit.forecast(args.model, args.target, args.region, args.horizon,
                            origin_date=args.origin_date, tidy=_load_tidy(args))
    _emit({"command": "forecast", "result": result}, args.json)


def cmd_anomalies(args: argparse.Namespace) -> None:
    kwargs: dict[str, Any] = {}
    if args.window_days:
        kwargs["window_days"] = args.window_days
    if args.min_baseline:
        kwargs["min_baseline"] = args.min_baseline
    result = mlkit.detect_anomalies(args.region, args.variable, args.method,
                                    origin=args.origin, granularity=args.granularity,
                                    tidy=_load_tidy(args), **kwargs)
    _emit({"command": "anomalies", "result": result}, args.json)


def cmd_registry(args: argparse.Namespace) -> None:
    _emit({"command": "registry", "models": mlkit.registry_entries(),
           "capabilities": mlkit.model_capabilities()}, args.json)


def cmd_availability(args: argparse.Namespace) -> None:
    _emit({"command": "availability", "sources": mlkit.data_sources(),
           "slices": mlkit.availability_report()}, args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ml/cli.py",
                                     description="EarthScape ML CLI (JSON on stdout)")
    parser.add_argument("--csv", nargs="*", help="explicit source files instead of auto-discovery")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("train", help="train the model ladder and save artifacts")
    p.add_argument("--target", required=True, help="canonical variable, e.g. temperature")
    p.add_argument("--region", help="omit to train every region available on disk")
    p.add_argument("--granularity", default="daily")
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--json", help="also write output to this file")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("evaluate", help="walk-forward ladder evaluation (no artifacts saved) "
                                        "or stored metrics via --model")
    p.add_argument("--target")
    p.add_argument("--region")
    p.add_argument("--model", help="name@version of a saved model")
    p.add_argument("--granularity", default="daily")
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("forecast", help="forecast from a saved model")
    p.add_argument("--model", required=True, help="name or name@version")
    p.add_argument("--region", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--origin-date", dest="origin_date")
    p.add_argument("--json")
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("anomalies", help="run anomaly detection")
    p.add_argument("--region", required=True)
    p.add_argument("--variable", required=True)
    p.add_argument("--method", default="statistical",
                   choices=["statistical", "threshold", "isolation_forest"])
    p.add_argument("--granularity", default="daily")
    p.add_argument("--origin")
    p.add_argument("--window-days", dest="window_days", type=int)
    p.add_argument("--min-baseline", dest="min_baseline", type=int)
    p.add_argument("--json")
    p.set_defaults(func=cmd_anomalies)

    p = sub.add_parser("registry", help="print model_registry-ready JSON documents")
    p.add_argument("--json")
    p.set_defaults(func=cmd_registry)

    p = sub.add_parser("availability", help="what data exists and what is modellable")
    p.add_argument("--json")
    p.set_defaults(func=cmd_availability)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
