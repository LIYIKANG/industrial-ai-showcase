# -*- coding: utf-8 -*-
"""PyJobShop engine for the order promising service.

Why a separate engine file:
* The PyJobShop model is more declarative (jobs / tasks / modes / renewable
  resources) so the public API and helpers stay readable in
  :mod:`backend.services.order_promising`.
* PyJobShop pre-validates every mode demand against the resource
  capacity at model-build time. We must therefore filter out infeasible
  (material, day) pairs *before* calling ``add_mode`` to avoid
  ``ValueError: All modes for task N have infeasible demands``.

Per-day capacity is expressed by giving each day its own renewable
resource with capacity = ``availability[day]`` (scaled by ``_SCALE`` so
the model stays in the integer domain). Each feasible day becomes an
optional task with ``earliest_start = latest_start = day``; the
``add_select_exactly_one`` constraint forces the solver to pick the
single best delivery day.

The objective uses PyJobShop's native ``weight_total_flow_time`` plus
``weight_total_tardiness`` to encode the lateness penalty.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from backend.services.atp_service import _coerce_date
from backend.services.order_promising import (
    _LATENESS_WEIGHT,
    _MAX_TIME_SECONDS,
    _SCALE,
    _series_for_material,
)


class PyJobShopEngine:
    name = "PyJobShop/CP-SAT"

    def solve_one_material(
        self,
        *,
        atp_result: dict[str, Any],
        material_id: str,
        quantity: float,
        desired_date: date | None,
        horizon_days: int,
    ) -> dict[str, Any]:
        from pyjobshop import Model, SolveStatus

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

        # Filter feasible days (PyJobShop rejects infeasible modes at build time).
        feasible_days: list[tuple[int, int]] = []
        for d, entry in enumerate(series):
            available_int = int(round(float(entry["atp"]) * _SCALE))
            if available_int >= qty_int:
                feasible_days.append((d, available_int))

        if not feasible_days:
            best = max((float(e["atp"]) for e in series), default=0.0)
            return {
                "engine": self.name,
                "promised_date": None,
                "can_promise": False,
                "on_time": False,
                "lateness_days": horizon_days,
                "qty_available": max(0.0, best),
                "solve_ms": 0.0,
            }

        m = Model()
        # 1 renewable resource per feasible day (capacity scaled by _SCALE).
        resources = [
            m.add_renewable(capacity=avail, name=f"d{d}")
            for d, avail in feasible_days
        ]
        # One job per candidate day. Each task MUST be optional=True so that
        # add_select_exactly_one can make sense: PyJobShop tasks default to
        # present=constant(True), which makes sum(present) == 1 infeasible.
        # The per-job due_date encodes the desired delivery day; the
        # weight_total_tardiness objective then penalises lateness.
        jobs = [
            m.add_job(due_date=desired_idx, name=f"order_{material_id}_d{d}")
            for d, _ in feasible_days
        ]
        tasks = []
        for i, (d, _) in enumerate(feasible_days):
            t = m.add_task(
                job=jobs[i],
                earliest_start=d,
                latest_start=d,
                optional=True,
                name=f"day_{d}",
            )
            m.add_mode(task=t, resources=[resources[i]], duration=1, demands=qty_int)
            tasks.append((d, t))

        m.add_select_exactly_one([t for _, t in tasks])

        m.set_objective(
            weight_total_flow_time=1,
            weight_total_tardiness=_LATENESS_WEIGHT,
        )

        t0 = time.perf_counter()
        res = m.solve(time_limit=_MAX_TIME_SECONDS, display=False)
        solve_ms = round((time.perf_counter() - t0) * 1000, 2)

        if res.status not in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
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

        # Extract the chosen day from the solution. PyJobShop reports
        # one ScheduledTask per task in the order they were added. The
        # task the solver actually scheduled carries ``present=True``;
        # all the others have ``present=False``.
        chosen_day = None
        scheduled = res.best.tasks
        for i, (day, _task) in enumerate(tasks):
            if i < len(scheduled) and getattr(scheduled[i], "present", False):
                chosen_day = day
                break
        # Defensive fallback: pick the task with the earliest non-negative
        # start, in case present flags are inconsistent.
        if chosen_day is None:
            for i, (day, _task) in enumerate(tasks):
                if i < len(scheduled) and getattr(scheduled[i], "start", -1) >= 0:
                    chosen_day = day
                    break

        if chosen_day is None:
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

        promised_iso = series[chosen_day]["date"]
        promised_date = _coerce_date(promised_iso) or base_date
        on_time = (promised_date <= desired_date) if desired_date else True
        lateness = max(0, (promised_date - desired_date).days) if desired_date else 0
        return {
            "engine": self.name,
            "promised_date": promised_iso,
            "can_promise": True,
            "on_time": on_time,
            "lateness_days": lateness,
            "qty_available": quantity,
            "solve_ms": solve_ms,
            "objective": res.objective,
        }
