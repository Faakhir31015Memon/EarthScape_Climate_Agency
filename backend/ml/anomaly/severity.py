"""Severity mapping for anomaly detections.

Documented mapping (robust z = |deviation| / (1.4826 * MAD) against the
day-of-year climatology, plus threshold breach level):

    none      : z <  2.0  and no threshold breach     — within normal variation
    info      : z >= 2.0  or marginal breach          — noteworthy deviation
    warning   : z >= 3.0  or extreme breach           — strong deviation from this
                                                           station's own climatology
    severe    : z >= 4.0  or (extreme breach and z >= 3.0)
              or a value outside hard physical limits by >25% of the span

The mapping is monotone in evidence: at least two independent signals must
agree for 'severe'. Threshold-only detections cap at 'warning' because a
generic limit says nothing about local climate.
"""

from __future__ import annotations

from typing import Optional

Z_INFO = 2.0
Z_WARNING = 3.0
Z_SEVERE = 4.0

SEVERITY_ORDER = ["none", "info", "warning", "severe"]

MAPPING_DOC = {
    "none": f"robust z < {Z_INFO} and no threshold breach",
    "info": f"robust z >= {Z_INFO}, or a marginal physical-limit breach",
    "warning": f"robust z >= {Z_WARNING}, or an extreme physical-limit breach (capped: "
               f"threshold-only detections never exceed warning)",
    "severe": f"robust z >= {Z_SEVERE}, or an extreme breach combined with z >= {Z_WARNING}",
}


def severity_for(robust_z: Optional[float], breach_level: Optional[str] = None) -> str:
    z = abs(robust_z) if robust_z is not None else None
    extreme_breach = breach_level == "extreme"
    marginal_breach = breach_level == "marginal"
    if z is not None and z >= Z_SEVERE:
        return "severe"
    if z is not None and extreme_breach and z >= Z_WARNING:
        return "severe"
    if z is not None and z >= Z_WARNING:
        return "warning"
    if extreme_breach:
        return "warning"      # threshold-only cap
    if z is not None and z >= Z_INFO or marginal_breach:
        return "info"
    return "none"


def rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else -1
