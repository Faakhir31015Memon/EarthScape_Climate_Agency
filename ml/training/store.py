"""Versioned model artifact storage.

Layout: ``artifacts/models/<model_name>/<version>/`` containing
``model.joblib`` (payload dict), ``metadata.json`` and ``feature_spec.json``.
Versions are timestamp-ordered directory names, so ``load_model`` without a
version picks the newest. Loading validates that the stored feature spec can
be honoured before any prediction happens.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import joblib

from .. import mlconfig
from ..features.pipeline import validate_feature_spec as validate_feature_or_raise


def _root() -> Path:
    return mlconfig.model_artifact_root()


def model_dir(name: str, version: str) -> Path:
    path = _root() / name / version
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_version() -> str:
    return datetime.now(timezone.utc).strftime("v%Y%m%dT%H%M%SZ")


def save_model(payload: dict[str, Any], metadata: dict[str, Any], name: str,
               version: Optional[str] = None) -> dict[str, str]:
    version = version or _new_version()
    directory = model_dir(name, version)
    artifact = directory / "model.joblib"
    joblib.dump(payload, artifact)
    spec = {k: payload.get(k) for k in ("feature_spec", "feature_names", "horizon_steps",
                                        "validated_horizon", "granularity", "algorithm")}
    meta = dict(metadata)
    try:
        uri = str(artifact.relative_to(mlconfig.REPO_ROOT)).replace("\\", "/")
    except ValueError:
        uri = str(artifact).replace("\\", "/")
    meta.update({"model_name": name, "model_version": version,
                 "artifact_uri": uri,
                 "saved_at": datetime.now(timezone.utc).isoformat()})
    (directory / "metadata.json").write_text(json.dumps(_jsonable(meta), indent=2), encoding="utf-8")
    (directory / "feature_spec.json").write_text(json.dumps(_jsonable(spec), indent=2), encoding="utf-8")
    return {"model_name": name, "model_version": version,
            "artifact_uri": meta["artifact_uri"], "directory": str(directory)}


class LoadedModel:
    def __init__(self, name: str, version: str, directory: Path, payload: dict[str, Any],
                 metadata: dict[str, Any], feature_spec: dict[str, Any]) -> None:
        self.name = name
        self.version = version
        self.directory = directory
        self.payload = payload
        self.metadata = metadata
        self.feature_spec = feature_spec

    @property
    def algorithm(self) -> str:
        return str(self.payload.get("algorithm", self.metadata.get("algorithm", "unknown")))

    @property
    def horizon_steps(self) -> int:
        return int(self.payload.get("horizon_steps", 0))

    @property
    def validated_horizon(self) -> int:
        return int(self.payload.get("validated_horizon", self.horizon_steps))

    def predict_validated(self, X_raw):
        """Refuse to predict when the incoming frame does not match the stored spec."""
        validate_feature_or_raise(self.feature_spec, list(X_raw.columns))
        estimator = self.payload.get("estimator")
        if estimator is None:
            raise ValueError("baseline model has no fitted estimator; use the naive predictor path")
        import pandas as pd
        names = list(self.feature_spec["feature_names"])
        if isinstance(X_raw, pd.DataFrame):
            X = X_raw[names]
        else:
            X = X_raw
        return estimator.predict(X)


def load_model(model_ref: str) -> LoadedModel:
    """``model_ref`` is ``name`` or ``name@version``; bare name picks the newest version."""
    if "@" in model_ref:
        name, version = model_ref.split("@", 1)
    else:
        name, version = model_ref, None
    base = _root() / name
    if not base.is_dir():
        available = sorted(p.name for p in _root().glob("*") if p.is_dir()) if _root().is_dir() else []
        raise FileNotFoundError(f"model '{name}' not found under {base}. "
                                f"Available: {available or 'none'}")
    if version is None:
        versions = sorted((p.name for p in base.iterdir() if (p / "model.joblib").is_file()))
        if not versions:
            raise FileNotFoundError(f"no saved versions for model '{name}' in {base}")
        version = versions[-1]
    directory = base / version
    artifact = directory / "model.joblib"
    if not artifact.is_file():
        raise FileNotFoundError(f"artifact missing: {artifact}")
    payload = joblib.load(artifact)
    metadata = {}
    meta_path = directory / "metadata.json"
    if meta_path.is_file():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    feature_spec = payload.get("feature_spec") or {}
    spec_path = directory / "feature_spec.json"
    if spec_path.is_file():
        stored = json.loads(spec_path.read_text(encoding="utf-8"))
        feature_spec = feature_spec or stored.get("feature_spec") or {}
        payload.setdefault("feature_spec", feature_spec)
        if stored.get("feature_spec"):
            payload["feature_spec"] = stored["feature_spec"]
    if not feature_spec or "feature_names" not in feature_spec:
        raise ValueError(f"model {model_ref} has no usable feature spec; refusing to load for prediction")
    return LoadedModel(name, version, directory, payload, metadata, feature_spec)


def attach_registry_entry(name: str, version: str, entry: dict[str, Any]) -> None:
    """Write the full registry document into the model's metadata.json sidecar."""
    meta_path = _root() / name / version / "metadata.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta["registry_entry"] = _jsonable(entry)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def list_models() -> list[dict[str, Any]]:
    """All saved models' metadata (used by the CLI registry command)."""
    root = _root()
    entries: list[dict[str, Any]] = []
    if not root.is_dir():
        return entries
    for name_dir in sorted(root.glob("*")):
        if not name_dir.is_dir():
            continue
        for version_dir in sorted(name_dir.glob("*")):
            meta_path = version_dir / "metadata.json"
            if meta_path.is_file():
                try:
                    entries.append(json.loads(meta_path.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
    return entries


def registry_documents() -> list[dict[str, Any]]:
    """Model_registry-ready documents: prefer the full registry entry saved in metadata."""
    docs = []
    for meta in list_models():
        entry = meta.get("registry_entry")
        docs.append(entry if entry else meta)
    return docs


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return None if value != value else round(value, 6)
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    try:
        import numpy as np
        if isinstance(value, np.generic):
            return _jsonable(value.item())
        if isinstance(value, (np.ndarray,)):
            return [_jsonable(v) for v in value.tolist()]
    except Exception:
        pass
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
