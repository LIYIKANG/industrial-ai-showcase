"""Critical material (关键物料) rule engine for the chemical ATP layer.

Three orthogonal axes are exposed:

* ``supply_risk`` - ``single`` / ``dual`` / ``multiple`` - multiplies ATP
  with a safety factor to keep capacity in reserve.
* ``lead_time_bucket`` - ``short`` / ``mid`` / ``long`` - extends the ATP
  horizon for the material so the planner sees what is on the way.
* ``price_sensitive`` - flag for downstream multi-objective weighting in
  MVP-3.  Has no effect on ATP itself in MVP-1.

The module is pure-function and has no I/O.  ``apply_critical_material_rules``
returns the (already-deducted) effective ATP, the adjusted horizon and a
human-readable summary.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

RISK_MULTIPLIERS = {
    "single": 0.80,
    "dual": 0.95,
    "multiple": 1.00,
}
LEAD_TIME_DAYS = {"short": 7, "mid": 30, "long": 90}


def compute_critical_material_rules(
    materials: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rules: dict[str, dict[str, Any]] = {}
    for material in materials:
        mid = str(material.get("id") or material.get("material_id") or "").strip()
        if not mid:
            continue
        risk = (material.get("supply_risk") or "multiple").lower()
        if risk not in RISK_MULTIPLIERS:
            risk = "multiple"
        bucket = (material.get("lead_time_bucket") or "mid").lower()
        if bucket not in LEAD_TIME_DAYS:
            bucket = "mid"
        rules[mid] = {
            "supply_risk": risk,
            "risk_multiplier": RISK_MULTIPLIERS[risk],
            "lead_time_bucket": bucket,
            "lead_time_days": int(material.get("lead_time_days") or LEAD_TIME_DAYS[bucket]),
            "price_sensitive": bool(material.get("price_sensitive", False)),
        }
    return rules


def apply_critical_material_rules(
    material_id: str,
    raw_atp: float,
    rule: dict[str, Any] | None,
) -> float:
    if not rule:
        return max(0.0, raw_atp)
    return max(0.0, raw_atp * rule["risk_multiplier"])


def adjusted_horizon(base_date: date, rule: dict[str, Any] | None, base_horizon_days: int) -> list[date]:
    """Daily list that extends far enough for long lead time materials."""
    horizon = base_horizon_days
    if rule:
        horizon = max(horizon, rule["lead_time_days"] + 7)
    return [base_date + timedelta(days=offset) for offset in range(horizon + 1)]


def summarise(material_id: str, rule: dict[str, Any] | None) -> dict[str, Any]:
    if not rule:
        return {
            "material_id": material_id,
            "critical": False,
            "supply_risk": "multiple",
            "lead_time_bucket": "mid",
            "risk_multiplier": 1.0,
        }
    critical = rule["risk_multiplier"] < 1.0 or rule["lead_time_bucket"] in ("long",) or rule["price_sensitive"]
    return {
        "material_id": material_id,
        "critical": critical,
        "supply_risk": rule["supply_risk"],
        "lead_time_bucket": rule["lead_time_bucket"],
        "lead_time_days": rule["lead_time_days"],
        "risk_multiplier": rule["risk_multiplier"],
        "price_sensitive": rule["price_sensitive"],
    }
