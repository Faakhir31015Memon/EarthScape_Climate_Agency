"""Registry entry: the Mongo-ready document describing one trained model.

Matches the ``model_registry`` collection shape required by the backend. The
backend owns persistence; this package only produces the dict (via the CLI's
``registry`` command or ``mlkit.registry_entries()``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class RegistryEntry:
    model_name: str
    model_version: str
    algorithm: str
    target_variable: str
    granularity: str
    horizon_steps: int
    feature_definition: list[dict[str, str]]
    training_period: dict[str, Optional[str]]
    test_period: dict[str, Optional[str]]
    rows_used: int
    evaluation_metrics: dict[str, Any]
    artifact_uri: str
    status: str = "candidate"          # 'candidate' | 'registered'
    created_at: str = ""
    data_source_files: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    region: str = ""
    backtest: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe Mongo document."""
        doc = asdict(self)
        doc = _jsonify(doc)
        doc["_id_note"] = "backend assigns _id; this document matches model_registry"
        return doc


def _jsonify(value: Any) -> Any:
    import numpy as np

    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if value != value else round(value, 6)
    if isinstance(value, (int, str)) or value is None:
        return value
    if isinstance(value, np.generic):
        return _jsonify(value.item())
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
