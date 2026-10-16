"""Per-(region, variable, granularity) time-series assembly.

The app layer needs an honest answer to "can we model this?" before any model
runs, so ``SeriesAssembler`` returns a ``DataAvailability`` describing exactly
why a slice is or is not modellable (row count vs MIN_TRAINING_ROWS, date
coverage, gap count) instead of letting a model silently invent history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .. import mlconfig

GRANULARITY_FREQ = {"hourly": "h", "daily": "D", "monthly": "MS", "instantaneous": None}


@dataclass
class DataAvailability:
    """Verdict on whether a (region, variable, granularity) slice has enough history."""

    enough_rows: bool
    rows: int
    min_required: int
    reason: str
    variable: str
    region: str
    granularity: str
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    expected_slots: int = 0
    gap_count: int = 0                     # slots inside the range without an observation
    duplicate_collapses: int = 0           # same slot observed multiple times (mean-merged)
    unit: Optional[str] = None
    provenance_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enough_rows": self.enough_rows, "rows": self.rows, "min_required": self.min_required,
            "reason": self.reason, "variable": self.variable, "region": self.region,
            "granularity": self.granularity, "date_start": self.date_start, "date_end": self.date_end,
            "expected_slots": self.expected_slots, "gap_count": self.gap_count,
            "duplicate_collapses": self.duplicate_collapses, "unit": self.unit,
            "provenance_files": self.provenance_files,
        }


@dataclass
class AssembledSeries:
    """A gap-honest time series: reindexed onto its full calendar, NaN where missing."""

    series: pd.Series
    availability: DataAvailability
    latitude: Optional[float]
    longitude: Optional[float]
    elevation_m: Optional[float]

    @property
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({"timestamp": self.series.index, "value": self.series.values})


class SeriesAssembler:
    """Groups the tidy observation frame into calendar-aligned series."""

    def __init__(self, tidy: pd.DataFrame, min_rows: Optional[int] = None) -> None:
        self.tidy = tidy
        self.min_rows = mlconfig.min_training_rows() if min_rows is None else int(min_rows)

    def keys(self) -> list[tuple[str, str, str]]:
        if self.tidy.empty:
            return []
        grouped = self.tidy.groupby(["region", "variable", "granularity"], sort=True)
        return [(str(r), str(v), str(g)) for (r, v, g) in grouped.size().index]

    def assemble(self, region: str, variable: str, granularity: str = "daily") -> AssembledSeries:
        empty = pd.Series(dtype="float64", name=variable)
        empty.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
        if self.tidy.empty:
            avail = DataAvailability(False, 0, self.min_rows,
                                     "no observation data exists on disk for any region/variable",
                                     variable, region, granularity)
            return AssembledSeries(empty, avail, None, None, None)

        subset = self.tidy[(self.tidy["region"] == region)
                           & (self.tidy["variable"] == variable)
                           & (self.tidy["granularity"] == granularity)]
        if subset.empty:
            present = sorted(self.tidy[self.tidy["variable"] == variable]["region"].unique().tolist())
            reason = (f"no rows for region '{region}', variable '{variable}', granularity '{granularity}'"
                      + (f"; this variable exists for: {present}" if present else
                         f"; variables available for {region}: "
                         f"{sorted(self.tidy[self.tidy['region'] == region]['variable'].unique().tolist())}"))
            avail = DataAvailability(False, 0, self.min_rows, reason, variable, region, granularity)
            return AssembledSeries(empty, avail, None, None, None)

        subset = subset.sort_values("timestamp")
        dup_mask = subset.duplicated(subset=["timestamp"], keep=False)
        duplicates = int(dup_mask.sum())
        collapsed = subset.groupby(subset["timestamp"].dt.floor("s"))["value"].mean()
        collapsed.name = variable

        lat = subset["latitude"].dropna()
        lon = subset["longitude"].dropna()
        elevation = None
        if "elevation_m" in subset.columns:
            elev = pd.to_numeric(subset["elevation_m"], errors="coerce").dropna()
            elevation = float(elev.median()) if not elev.empty else None

        freq = GRANULARITY_FREQ.get(granularity)
        if freq is not None and len(collapsed) > 1:
            full = pd.date_range(collapsed.index.min().floor(freq), collapsed.index.max().ceil(freq), freq=freq)
            aligned = collapsed.reindex(full)
            aligned.index.name = "timestamp"
        else:
            aligned = collapsed
            aligned.index.name = "timestamp"

        rows = int(collapsed.notna().sum())
        gap_count = int(aligned.isna().sum()) if len(aligned) else 0
        unit = subset["unit"].mode().iat[0] if not subset["unit"].mode().empty else None
        provenance = sorted(subset["provenance_file"].unique().tolist())

        if rows < self.min_rows:
            reason = (f"insufficient history: {rows} observed rows for ({region}, {variable}, {granularity}); "
                      f"minimum is {self.min_rows} (MIN_TRAINING_ROWS). No model or forecast will be produced "
                      f"for this slice.")
            enough = False
        else:
            horizon_needed = 30 if granularity == "daily" else 24
            if rows < self.min_rows + horizon_needed:
                reason = (f"{rows} rows meet MIN_TRAINING_ROWS={self.min_rows} but leave little room for a "
                          f"{horizon_needed}-step holdout; long-horizon forecasts may be rejected.")
            else:
                reason = "sufficient history for training and a chronological holdout"
            enough = True

        avail = DataAvailability(
            enough_rows=enough, rows=rows, min_required=self.min_rows, reason=reason,
            variable=variable, region=region, granularity=granularity,
            date_start=str(collapsed.index.min()), date_end=str(collapsed.index.max()),
            expected_slots=int(len(aligned)), gap_count=gap_count,
            duplicate_collapses=duplicates, unit=unit, provenance_files=provenance,
        )
        return AssembledSeries(aligned, avail,
                               float(lat.median()) if not lat.empty else None,
                               float(lon.median()) if not lon.empty else None,
                               elevation)


def summarize_availability(tidy: pd.DataFrame, min_rows: Optional[int] = None) -> list[dict[str, Any]]:
    """Availability verdict for every (region, variable, granularity) slice present."""
    assembler = SeriesAssembler(tidy, min_rows=min_rows)
    return [assembler.assemble(r, v, g).availability.as_dict() for (r, v, g) in assembler.keys()]
