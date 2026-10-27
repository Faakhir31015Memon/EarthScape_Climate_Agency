from __future__ import annotations

from typing import Any, Dict, List

from apps.ingestion.connectors.base import (
    ConnectorError,
    HttpError,
    IngestionRequest,
    NotConfigured,
    RateLimited,
    SourceConnector,
    SourceValidationError,
    Unreachable,
    assemble_observation,
    sanitise_parameters,
)
from apps.ingestion.connectors.nasa_power import NasaPowerConnector
from apps.ingestion.connectors.noaa_cdo import NcdcCdoConnector
from apps.ingestion.connectors.open_meteo import LiveClimateConnector

CONNECTORS: Dict[str, SourceConnector] = {
    "nasa_power": NasaPowerConnector(),
    "noaa_cdo": NcdcCdoConnector(),
    "open_meteo": LiveClimateConnector(),
}

# alternate source names accepted from the API
ALIASES: Dict[str, str] = {
    "nasa": "nasa_power",
    "noaa": "noaa_cdo",
    "noaa_cdo": "noaa_cdo",
    "live": "open_meteo",
    "live_api": "open_meteo",
    "open_meteo": "open_meteo",
}


def resolve_name(name: str) -> str:
    key = (name or "").strip().lower()
    return ALIASES.get(key, key)


def get_connector(name: str) -> SourceConnector:
    resolved = resolve_name(name)
    connector = CONNECTORS.get(resolved)
    if connector is None:
        raise SourceValidationError(
            f"Unknown source '{name}'. Available: {', '.join(sorted(CONNECTORS))}.")
    return connector


def connector_status(*, check_reachability: bool = False) -> List[Dict[str, Any]]:
    """Per-source configuration (+ optional live reachability) for the UI."""
    entries: List[Dict[str, Any]] = []
    for connector in CONNECTORS.values():
        entry = dict(connector.configuration_status())
        if check_reachability:
            try:
                entry["reachability"] = connector.preflight()
            except ConnectorError as exc:  # never let a status probe raise to the caller
                entry["reachability"] = {"reachable": False, "state": "unreachable",
                                         "detail": exc.message}
        entries.append(entry)
    return entries


__all__ = [
    "CONNECTORS", "SourceConnector", "IngestionRequest", "get_connector", "resolve_name",
    "connector_status", "ConnectorError", "NotConfigured", "Unreachable", "HttpError",
    "RateLimited", "SourceValidationError", "assemble_observation", "sanitise_parameters",
]
