"""Multi-objective order ranking (MVP-2).

When supply cannot satisfy all open orders, the planner needs a *ranking*
plus a *strategy justification*.  MVP-2 implements three canonical
multi-objective decision rules and a Pareto-front sampler:

*   `weighted_sum`   - linear scalarisation; tunable weights for
    customer priority, due-date penalty, and order quantity.
*   `lexicographic`  - sort by primary objective, then break ties with
    the secondary, then tertiary.  Mirrors the SAP-BOP order used in
    MVP-1.
*   `epsilon_constraint` - rank by primary objective, drop any order
    that violates a hard secondary constraint (e.g. due date already
    blown, or ATP below a safety-stock-aware threshold).
*   `pareto`         - sweep a small grid of weight vectors, return
    every distinct ranking together with the non-dominated Pareto
    front in the (priority, due, quantity) objective space.

All strategies are pure functions; the input is the same set of
`materials / inventory / inbound / open_orders / priorities` and the
ATP series produced by :mod:ackend.services.atp_service.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import Any

from backend.core.special_control import compute_special_control_rules
from backend.services.atp_service import _coerce_date, _material_key, compute_atp


CUSTOMER_TIER_RANK = {"premium": 0, "comfort": 1, "base": 2}
DEFAULT_TIER_WEIGHT = {"premium": 100.0, "comfort": 50.0, "base": 10.0}
DEFAULT_WEIGHTS = {"priority": 0.5, "lateness": 0.3, "quantity": 0.2}
EPSILON_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
PARETO_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
MAX_PARETO_RANKINGS = 6


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank_orders(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    priorities: list[dict[str, Any]] | None = None,
    strategy: str = "weighted_sum",
    horizon_days: int = 30,
    weights: dict[str, float] | None = None,
    epsilon: dict[str, float] | None = None,
) -> dict[str, Any]:
    if strategy not in {"weighted_sum", "lexicographic", "epsilon_constraint", "pareto"}:
        return {
            "status": "not_implemented",
            "engine": "MVP-2 multi-objective",
            "strategy": strategy,
            "mvp_note": "supported strategies: weighted_sum, lexicographic, epsilon_constraint, pareto.",
            "ranking": [],
        }

    started = time.perf_counter()
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    epsilon = {"max_lateness_days": 0, "min_priority": 0.0, **(epsilon or {})}

    atp = compute_atp(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=[],
        priorities=priorities,
        horizon_days=horizon_days,
    )

    base_date = _coerce_date(atp.get("base_date")) or date.today()
    enriched = _enrich_orders(
        open_orders=open_orders or [],
        materials=materials,
        atp_matrix=atp["matrix"],
        base_date=base_date,
        priorities=priorities,
    )

    if strategy == "weighted_sum":
        ranking = _rank_weighted_sum(enriched, weights)
    elif strategy == "lexicographic":
        ranking = _rank_lexicographic(enriched)
    elif strategy == "epsilon_constraint":
        ranking = _rank_epsilon(enriched, epsilon)
    else:  # pareto
        rankings, front = _rank_pareto(enriched, weights)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "ok",
            "engine": "MVP-2 multi-objective",
            "strategy": strategy,
            "rankings": rankings,
            "pareto_front": front,
            "weights_grid": PARETO_GRID,
            "elapsed_ms": elapsed_ms,
            "kpis": _kpis(enriched, rankings[0] if rankings else []),
        }

    kpis = _kpis(enriched, ranking)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "status": "ok",
        "engine": "MVP-2 multi-objective",
        "strategy": strategy,
        "ranking": ranking,
        "kpis": kpis,
        "weights": weights,
        "epsilon": epsilon,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _enrich_orders(
    *,
    open_orders: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    atp_matrix: list[dict[str, Any]],
    base_date: date,
    priorities: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    category_by_id = {_material_key(m): str(m.get("category") or "").lower() for m in materials}
    safety_by_id = {_material_key(m): float(m.get("safety_stock") or 0) for m in materials}
    # Latest available ATP per material (after horizon = end-of-horizon ATP).
    last_atp_by_id: dict[str, float] = {}
    series_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in atp_matrix:
        mid = row["material_id"]
        series_by_id[mid] = row.get("series") or []
        if series_by_id[mid]:
            last_atp_by_id[mid] = float(series_by_id[mid][-1]["atp"])
        else:
            last_atp_by_id[mid] = 0.0

    weight_by_key: dict[tuple[str, str], float] = {}
    for row in priorities or []:
        tier = str(row.get("customer_tier") or "").lower()
        cat = str(row.get("material_category") or row.get("category") or "").lower()
        try:
            weight_by_key[(tier, cat)] = float(row.get("weight") or 0)
        except (TypeError, ValueError):
            continue

    enriched: list[dict[str, Any]] = []
    for order in open_orders:
        mid = str(order.get("material_id") or "").strip()
        tier = str(order.get("customer_tier") or "base").lower()
        category = category_by_id.get(mid, "")
        priority_weight = weight_by_key.get((tier, category), DEFAULT_TIER_WEIGHT.get(tier, 0))
        quantity = float(order.get("quantity") or 0)
        available = last_atp_by_id.get(mid, 0.0)
        promised_qty = min(quantity, max(0.0, available)) if available > 0 else 0.0
        fulfill_ratio = (promised_qty / quantity) if quantity else 0.0
        due = _coerce_date(order.get("due_date")) or base_date
        lateness = max(0, (base_date - due).days)  # negative days = still in time, clamp to 0
        safety = safety_by_id.get(mid, 0.0)
        can_fulfill = available >= quantity > 0
        enriched.append(
            {
                "order_id": str(order.get("id") or order.get("order_id") or ""),
                "material_id": mid,
                "material_category": category,
                "customer_id": str(order.get("customer_id") or ""),
                "customer_tier": tier,
                "tier_rank": CUSTOMER_TIER_RANK.get(tier, 3),
                "quantity": quantity,
                "available": available,
                "promised_qty": round(promised_qty, 2),
                "fulfill_ratio": round(fulfill_ratio, 4),
                "due_date": due.isoformat(),
                "lateness_days": lateness,
                "priority_weight": priority_weight,
                "can_fulfill": can_fulfill,
                "safety_stock": safety,
                "approved": bool(order.get("approved", False)),
                "released_qty": float(order.get("released_qty") or 0),
            }
        )
    return enriched


def _rank_weighted_sum(enriched: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    w_pri = float(weights.get("priority", DEFAULT_WEIGHTS["priority"]))
    w_lat = float(weights.get("lateness", DEFAULT_WEIGHTS["lateness"]))
    w_qty = float(weights.get("quantity", DEFAULT_WEIGHTS["quantity"]))
    qty_max = max((o["quantity"] for o in enriched), default=1.0) or 1.0
    out = []
    for o in enriched:
        score = (
            w_pri * o["priority_weight"]
            + w_qty * (o["quantity"] / qty_max) * 100
            - w_lat * o["lateness_days"] * 5
            + 50 * o["fulfill_ratio"]  # bonus for already-fulfillable
        )
        out.append({**o, "score": round(score, 2)})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def _rank_lexicographic(enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 1) can-fulfill orders first; 2) tier rank ascending; 3) due date ascending; 4) quantity descending.
    out = sorted(
        enriched,
        key=lambda r: (
            not r["can_fulfill"],
            r["tier_rank"],
            r["due_date"],
            -r["quantity"],
            r["order_id"],
        ),
    )
    for r in out:
        r["score"] = round(
            1000 * r["fulfill_ratio"]
            + 100 * (3 - r["tier_rank"])
            - r["lateness_days"] * 5,
            2,
        )
    return out


def _rank_epsilon(enriched: list[dict[str, Any]], epsilon: dict[str, float]) -> list[dict[str, Any]]:
    max_late = int(epsilon.get("max_lateness_days", 0))
    min_priority = float(epsilon.get("min_priority", 0.0))
    feasible = [o for o in enriched if o["lateness_days"] <= max_late and o["priority_weight"] >= min_priority]
    # Sort the feasible by priority desc, due date asc, quantity desc.
    feasible.sort(key=lambda r: (-r["priority_weight"], r["due_date"], -r["quantity"]))
    for r in feasible:
        r["score"] = round(r["priority_weight"] * r["fulfill_ratio"], 2)
    excluded = [o for o in enriched if o not in feasible]
    for r in feasible:
        r["eligible"] = True
    for r in excluded:
        r["eligible"] = False
    return feasible + excluded


def _rank_pareto(
    enriched: list[dict[str, Any]],
    base_weights: dict[str, float],
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    """Sweep a small grid of weight vectors and return the Pareto front."""
    distinct_rankings: list[list[dict[str, Any]]] = []
    fingerprints: set = set()
    for pri, lat in product(PARETO_GRID, PARETO_GRID):
        if pri + lat > 1.0:
            continue
        qty = round(1.0 - pri - lat, 2)
        if qty < 0:
            continue
        ranking = _rank_weighted_sum(enriched, {"priority": pri, "lateness": lat, "quantity": qty})
        fingerprint = tuple((r["order_id"] for r in ranking))
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        distinct_rankings.append(ranking)
        if len(distinct_rankings) >= MAX_PARETO_RANKINGS:
            break
    if not distinct_rankings:
        return [], []
    # Compute Pareto front across the 3 objective values of each ranking.
    candidates = [
        {
            "ranking_id": i,
            "weights": r[0].get("score", 0),  # placeholder, overwritten below
            "priority": sum(o["priority_weight"] for o in r),
            "lateness": sum(o["lateness_days"] for o in r),
            "quantity": sum(o["promised_qty"] for o in r),
        }
        for i, r in enumerate(distinct_rankings)
    ]
    for c, r in zip(candidates, distinct_rankings):
        c["weights"] = r[0]["score"]  # not used in domination, kept for completeness
    front = []
    for i, ci in enumerate(candidates):
        dominated = False
        for j, cj in enumerate(candidates):
            if i == j:
                continue
            if (
                cj["priority"] >= ci["priority"]
                and cj["lateness"] <= ci["lateness"]
                and cj["quantity"] >= ci["quantity"]
                and (
                    cj["priority"] > ci["priority"]
                    or cj["lateness"] < ci["lateness"]
                    or cj["quantity"] > ci["quantity"]
                )
            ):
                dominated = True
                break
        if not dominated:
            front.append(ci)
    return distinct_rankings, front


def _kpis(enriched: list[dict[str, Any]], ranking: list[dict[str, Any]]) -> dict[str, Any]:
    if not ranking:
        return {"orders": 0, "promised": 0, "short": 0, "ots_pct": 0.0, "weighted_score_sum": 0.0}
    promised = sum(1 for o in ranking if o["can_fulfill"])
    total = len(ranking)
    score_sum = sum(float(o.get("score", 0)) for o in ranking)
    return {
        "orders": total,
        "promised": promised,
        "short": total - promised,
        "ots_pct": round(100.0 * promised / total, 2) if total else 0.0,
        "weighted_score_sum": round(score_sum, 2),
        "avg_lateness_days": round(sum(o["lateness_days"] for o in ranking) / total, 2) if total else 0.0,
    }

# ---------------------------------------------------------------------------
# MVP-3 additions: supply-gap allocator + cross-strategy comparison
# ---------------------------------------------------------------------------

ALL_STRATEGIES = ('weighted_sum', 'lexicographic', 'epsilon_constraint', 'pareto')


def allocate_under_gap(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    priorities: list[dict[str, Any]] | None = None,
    strategy: str = 'lexicographic',
    horizon_days: int = 30,
    weights: dict[str, float] | None = None,
    epsilon: dict[str, float] | None = None,
) -> dict[str, Any]:
    ranking_result = rank_orders(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=open_orders,
        priorities=priorities,
        strategy=strategy,
        horizon_days=horizon_days,
        weights=weights,
        epsilon=epsilon,
    )
    if ranking_result.get('status') != 'ok':
        return ranking_result

    ranking = (
        ranking_result['ranking']
        if 'ranking' in ranking_result
        else ranking_result['rankings'][0]
    )

    atp = compute_atp(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=[],
        priorities=priorities,
        horizon_days=horizon_days,
    )
    remaining: dict[str, list[float]] = {}
    for row in atp['matrix']:
        mid = row['material_id']
        remaining[mid] = [float(s['atp']) for s in row.get('series', [])]
    base_date = _coerce_date(atp.get('base_date')) or date.today()

    special_rules = compute_special_control_rules(materials)

    decisions: list[dict[str, Any]] = []
    promised_count = 0
    short_count = 0
    special_blocked = 0

    for order in ranking:
        mid = order['material_id']
        # Check special-control first: an isolated material has 0 ATP
        # by design and would otherwise be reported as 'no_atp_series'.
        rule = special_rules.get(mid, {})
        if rule.get('isolated') and not order.get('approved'):
            decision = {
                **order,
                'allocation': 'blocked',
                'delivered_qty': 0.0,
                'delivery_date': None,
                'reason': 'special_control_isolated',
                'subtype': rule.get('subtype', ''),
                'subtype_label': rule.get('subtype_label', ''),
                'approval_role': rule.get('approval_role', 'planner'),
            }
            decisions.append(decision)
            special_blocked += 1
            short_count += 1
            continue

        # For approved isolated orders, treat the per-order released
        # quantity as the daily available ATP (the approval workflow
        # has already vetted the release from the isolated pool). If
        # the regular ATP series is empty (level-0 isolation zeroes it
        # out), build a synthetic series of the same length as the
        # ranking so the standard day-by-day scan can find a slot.
        if rule.get('isolated') and order.get('approved'):
            released = float(order.get('released_qty') or order['quantity'])
            n = len(remaining[mid]) or len(ranking) or 10
            remaining[mid] = [released] * n

        if mid not in remaining or not remaining[mid]:
            decision = {
                **order,
                'allocation': 'short',
                'delivered_qty': 0.0,
                'delivery_date': None,
                'reason': 'no_atp_series',
            }
            decisions.append(decision)
            short_count += 1
            continue

        qty = float(order['quantity'])
        due = _coerce_date(order.get('due_date')) or base_date
        n = len(remaining[mid])
        due_idx = max(0, min(n - 1, (due - base_date).days))
        chosen_idx = None
        for idx in range(due_idx, n):
            if remaining[mid][idx] >= qty:
                chosen_idx = idx
                break
        if chosen_idx is None:
            decision = {
                **order,
                'allocation': 'short',
                'delivered_qty': 0.0,
                'delivery_date': None,
                'reason': 'insufficient_atp',
            }
            decisions.append(decision)
            short_count += 1
            continue

        for idx in range(chosen_idx, n):
            remaining[mid][idx] = max(0.0, remaining[mid][idx] - qty)
        promised_count += 1
        # Delivery date comes from the atp series when present, otherwise
        # we fall back to base_date + chosen_idx days. The latter covers
        # the synthetic-series case where an approved isolated order
        # was released into a freshly-constructed pool.
        mat_index = next(
            (i for i, r in enumerate(atp['matrix']) if r['material_id'] == mid),
            None,
        )
        series = atp['matrix'][mat_index]['series'] if mat_index is not None else []
        if 0 <= chosen_idx < len(series):
            delivery_iso = series[chosen_idx]['date']
        else:
            from datetime import timedelta as _td
            delivery_iso = (base_date + _td(days=chosen_idx)).isoformat()
        decision = {
            **order,
            'allocation': 'promised',
            'delivered_qty': qty,
            'delivery_date': delivery_iso,
            'reason': 'ok',
        }
        decisions.append(decision)

    return {
        'status': 'ok',
        'engine': 'MVP-3 allocator',
        'strategy': strategy,
        'allocation': decisions,
        'summary': {
            'orders': len(decisions),
            'promised': promised_count,
            'short': short_count,
            'special_control_blocked': special_blocked,
            'ots_pct': round(100.0 * promised_count / len(decisions), 2) if decisions else 0.0,
        },
        'kpis': ranking_result.get('kpis', {}),
    }


def compare_strategies(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    priorities: list[dict[str, Any]] | None = None,
    horizon_days: int = 30,
    weights: dict[str, float] | None = None,
    epsilon: dict[str, float] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for strat in ALL_STRATEGIES:
        alloc = allocate_under_gap(
            materials=materials,
            inventory=inventory,
            inbound=inbound,
            open_orders=open_orders,
            priorities=priorities,
            strategy=strat,
            horizon_days=horizon_days,
            weights=weights,
            epsilon=epsilon,
        )
        if alloc.get('status') != 'ok':
            rows.append({'strategy': strat, 'status': alloc.get('status')})
            continue
        s = alloc['summary']
        promised = [d for d in alloc['allocation'] if d['allocation'] == 'promised']
        tier_mix: dict[str, int] = {}
        for d in promised:
            tier_mix[d['customer_tier']] = tier_mix.get(d['customer_tier'], 0) + 1
        rows.append(
            {
                'strategy': strat,
                'status': 'ok',
                'promised': s['promised'],
                'short': s['short'],
                'ots_pct': s['ots_pct'],
                'special_control_blocked': s['special_control_blocked'],
                'premium_promised': tier_mix.get('premium', 0),
                'comfort_promised': tier_mix.get('comfort', 0),
                'base_promised': tier_mix.get('base', 0),
                'allocation': alloc['allocation'],
            }
        )
    return {
        'status': 'ok',
        'engine': 'MVP-3 comparator',
        'horizon_days': horizon_days,
        'strategies': rows,
    }
