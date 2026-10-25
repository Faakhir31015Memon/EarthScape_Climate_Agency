"""Provider registry — add a new source by registration only.

``build_connector(name, config)`` instantiates the registered connector class.
Third-party sources (e.g. a university department scraper) plug in with::

    from connectors.registry import register
    register("my_source", MyConnector, title="My Source", description="...")
"""

from __future__ import annotations

from typing import Optional

_REGISTRY: dict = {}


def register(name: str, cls, title: str = "", description: str = "", requirements: Optional[list] = None) -> None:
    if name in _REGISTRY:
        raise ValueError(f"connector {name!r} already registered")
    _REGISTRY[name] = {
        "name": name,
        "class": cls,
        "title": title or name.replace("_", " ").title(),
        "description": description,
        "requirements": requirements or [],
    }


def registered() -> dict:
    return dict(_REGISTRY)


def provider_info(name: str) -> dict:
    entry = _REGISTRY.get(name)
    if entry is None:
        raise KeyError(f"no connector registered under {name!r}; available: {sorted(_REGISTRY)}")
    return {k: v for k, v in entry.items() if k != "class"}


def build_connector(name: str, config: Optional[dict] = None):
    entry = _REGISTRY.get(name)
    if entry is None:
        raise KeyError(f"no connector registered under {name!r}; available: {sorted(_REGISTRY)}")
    return entry["class"](config or {})


def _bootstrap() -> None:
    if _REGISTRY:
        return
    from connectors.nasa_power import NasaPowerConnector
    from connectors.noaa_cdc import NoaaCdcConnector
    from connectors.open_meteo import OpenMeteoConnector, OpenMeteoLiveConnector

    register("open_meteo", OpenMeteoConnector,
             title="Open-Meteo Archive",
             description="Historical daily/hourly reanalysis for any coordinate; no key required.",
             requirements=[])
    register("open_meteo_live", OpenMeteoLiveConnector,
             title="Open-Meteo Forecast (polling)",
             description="Recent-hours forecast endpoint used by periodic polling ingestion; no key required.",
             requirements=[])
    register("nasa_power", NasaPowerConnector,
             title="NASA POWER",
             description="Hourly/daily point aggregations from NASA LAARC; no key required.",
             requirements=[])
    register("noaa_cdc", NoaaCdcConnector,
             title="NOAA Climate Data Online",
             description="GHCND station observations with token auth and offset pagination.",
             requirements=["NOAA_API_TOKEN"])


_bootstrap()
