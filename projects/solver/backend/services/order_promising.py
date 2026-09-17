# -*- coding: utf-8 -*-
"""Order promising service (MVP-2 hardening).

Solves the *Available-to-Promise + earliest delivery* question with two
engines sharing the same public API:

* :class:`PyJobShopEngine` (default when pyjobshop is importable) — a
  declarative PyJobShop model where each feasible (material, day) pair
  becomes an optional task, and ``select_exactly_one`` forces the
  solver to pick the single best delivery day. PyJobShop wraps
  OR-Tools CP-SAT, so the answer is provably optimal with respect to
  the discrete-time inventory model.

* :class:`CpSatEngine` (fallback) — the original hand-written
  :mod:`ortools.sat.python.cp_model` model retained for environments
  where pyjobshop is unavailable.

Public API (unchanged from the prior MVP-2 version):

* :func:`promise_order`  one order in, one decision out
* :func:`promise_batch`  many orders in, many independent decisions out

Each engine returns the same shape so the 9 MVP-2 tests + the
``/api/atp/promise`` route work without change.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Protocol

from ortools.sat.python import cp_model

from backend.services.atp_service import _coerce_date, _material_key, compute_atp


_SCALE = 100
_MAX_TIME_SECONDS = 2.0
_MAX_ALTERNATES = 5
_SERVED_BONUS = 1_000_000
_LATENESS_WEIGHT = 5


# ---------------------------------------------------------------------------
# Engine protocol
# ---------------------------------------------------------------------------


class _Engine(Protocol):
    name: str

    def solve_one_material(
        self,
        *,
        atp_result: dict[str, Any],
        material_id: str,
        quantity: float,
        desired_date: date | None,
        horizon_days: int,
    ) -> dict[str, Any]: ...


def _make_engine() -> _Engine:
    """Pick pyjobshop if importable, else fall back to cp_model."""
    try:
        from backend.services._promise_pyjobshop import PyJobShopEngine  # noqa: F401
        return PyJobShopEngine()
    except Exception:
        return _CpSatEngine()


# ---------------------------------------------------------------------------
# Public API (unchanged signatures)
# ---------------------------------------------------------------------------


def promise_order(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    request: dict[str, Any],
    horizon_days: int = 30,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("request must be a dict")
    material_id = str(request.get("material_id") or "").strip()
    quantity = float(request.get("quantity") or 0)
    if not material_id:
        raise ValueError("request.material_id is required")
    if quantity <= 0:
        raise ValueError("request.quantity must be > 0")
    desired = _parse_request_date(request.get("desired_date"))

    atp = compute_atp(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=open_orders,
        horizon_days=horizon_days,
    )

    engine = _make_engine()
    primary = engine.solve_one_material(
        atp_result=atp,
        material_id=material_id,
        quantity=quantity,
        desired_date=desired,
        horizon_days=horizon_days,
    )

    alternates: list[dict[str, Any]] = []
    if not primary["can_promise"]:
        alternates = _find_alternates(
            atp=atp,
            materials=materials,
            exclude_material=material_id,
            quantity=quantity,
            desired_date=desired,
            horizon_days=horizon_days,
            engine=engine,
        )

    return {
        "status": "ok" if primary["can_promise"] else "infeasible",
        "engine": primary["engine"],
        "material_id": material_id,
        "quantity": quantity,
        "desired_date": desired.isoformat() if desired else "",
        "promised_date": primary["promised_date"],
        "can_promise": primary["can_promise"],
        "on_time": primary["on_time"],
        "lateness_days": primary["lateness_days"],
        "qty_available": primary["qty_available"],
        "solve_ms": primary["solve_ms"],
        "alternates": alternates,
    }


def promise_batch(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    requests: list[dict[str, Any]],
    horizon_days: int = 30,
) -> dict[str, Any]:
    atp = compute_atp(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=open_orders,
        horizon_days=horizon_days,
    )
    engine = _make_engine()
    decisions: list[dict[str, Any]] = []
    for req in requests:
        result = engine.solve_one_material(
            atp_result=atp,
            material_id=str(req.get("material_id") or "").strip(),
            quantity=float(req.get("quantity") or 0),
            desired_date=_parse_request_date(req.get("desired_date")),
            horizon_days=horizon_days,
        )
        decisions.append({"request": req, "result": result})
    promised = sum(1 for d in decisions if d["result"]["can_promise"])
    return {
        "status": "ok",
        "engine": f"{engine.name} (batch)",
        "horizon_days": horizon_days,
        "total": len(decisions),
        "promised": promised,
        "short": len(decisions) - promised,
        "decisions": decisions,
    }


# ---------------------------------------------------------------------------
# Helpers shared by both engines
# ---------------------------------------------------------------------------


def _parse_request_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return _coerce_date(value)


def _series_for_material(atp: dict[str, Any], material_id: str) -> list[dict[str, Any]] | None:
    for row in atp.get("matrix", []):
        if row.get("material_id") == material_id and not row.get("isolated"):
            return row.get("series") or []
    return None


def _find_alternates(
    *,
    atp: dict[str, Any],
    materials: list[dict[str, Any]],
    exclude_material: str,
    quantity: float,
    desired_date: date | None,
    horizon_days: int,
    engine: _Engine,
) -> list[dict[str, Any]]:
    target_category = ""
    for m in materials:
        if _material_key(m) == exclude_material:
            target_category = str(m.get("category") or "").lower()
            break
    if not target_category:
        return []
    candidates: list[dict[str, Any]] = []
    for m in materials:
        mid = _material_key(m)
        if not mid or mid == exclude_material:
            continue
        if str(m.get("category") or "").lower() != target_category:
            continue
        result = engine.solve_one_material(
            atp_result=atp,
            material_id=mid,
            quantity=quantity,
            desired_date=desired_date,
            horizon_days=horizon_days,
        )
        if result["can_promise"]:
            candidates.append(
                {
                    "material_id": mid,
                    "name": m.get("name") or mid,
                    "promised_date": result["promised_date"],
                    "lateness_days": result["lateness_days"],
                    "on_time": result["on_time"],
                }
            )
    candidates.sort(key=lambda r: (not r["on_time"], r["lateness_days"], r["material_id"]))
    return candidates[:_MAX_ALTERNATES]


# ---------------------------------------------------------------------------
# CP-SAT engine (original MVP-2 implementation, kept as fallback)
# ---------------------------------------------------------------------------


class _CpSatEngine:
    name = "OR-Tools CP-SAT"

    def solve_one_material(
        self,
        *,
        atp_result: dict[str, Any],
        material_id: str,
        quantity: float,
        desired_date: date | None,
        horizon_days: int,
    ) -> dict[str, Any]:
        series = _series_for_material(atp_result, material_id)
        if not series:
            return {
                "engine": f"{self.name} (no-series)",
                "promised_date": None,
                "can_promise": False,
                "on_time": False,
                "lateness_days": horizon_days,
                "qty_available": 0.0,
                "solve_ms": 0.0,
            }

        n = len(series)
        base_date = _coerce_date(atp_result.get("base_date")) or date.today()
        qty_int = int(round(quantity * _SCALE))
        desired_idx = n
        if desired_date is not None:
            desired_idx = max(0, min(n - 1, (desired_date - base_date).days))

        model = cp_model.CpModel()
        deliver = [model.NewBoolVar(f"deliver_{d}") for d in range(n)]
        served = model.NewBoolVar("served")
        for d in range(n):
            model.Add(deliver[d] <= served)
        model.Add(served <= sum(deliver))
        model.Add(sum(deliver) <= 1)
        for d, entry in enumerate(series):
            available_int = int(round(float(entry["atp"]) * _SCALE))
            model.Add(qty_int * deliver[d] <= available_int).OnlyEnforceIf(deliver[d])
        lateness_terms = [
            d * (d - desired_idx) * deliver[d] for d in range(n) if d > desired_idx
        ]
        objective = (
            _SERVED_BONUS * (1 - served)
            + sum(d * deliver[d] for d in range(n))
            + _LATENESS_WEIGHT * sum(lateness_terms)
        )
        model.Minimize(objective)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = _MAX_TIME_SECONDS
        t0 = time.perf_counter()
        status = solver.Solve(model)
        solve_ms = round((time.perf_counter() - t0) * 1000, 2)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) and solver.Value(served) == 1:
            for d in range(n):
                if solver.Value(deliver[d]) == 1:
                    promised_iso = series[d]["date"]
                    promised_date = _coerce_date(promised_iso) or base_date
                    on_time = (promised_date <= desired_date) if desired_date else True
                    lateness = (
                        max(0, (promised_date - desired_date).days) if desired_date else 0
                    )
                    return {
                        "engine": self.name,
                        "promised_date": promised_iso,
                        "can_promise": True,
                        "on_time": on_time,
                        "lateness_days": lateness,
                        "qty_available": quantity,
                        "solve_ms": solve_ms,
                    }
        best = max((float(e["atp"]) for e in series), default=0.0)
        return {
            "engine": self.name,
            "promised_date": None,
            "can_promise": False,
            "on_time": False,
            "lateness_days": horizon_days,
            "qty_available": max(0.0, best),
            "solve_ms": solve_ms,
        }