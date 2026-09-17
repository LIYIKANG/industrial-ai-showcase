# -*- coding: utf-8 -*-
"""Special control (特控) rule engine for the chemical ATP layer.

MVP-4 expands the original 3-level model with named sub-categories
for level 0 (剧毒 / 易制爆 / 易制毒). Levels 1 and 2 are kept as
regulatory / company-defined control.

Sub-categories (``special_control_subtype``):
* ``toxic``               -- 剧毒品
* ``explosive_precursor`` -- 易制爆危险化学品
* ``drug_precursor``      -- 易制毒化学品
* ``none``                -- not level 0 (or level 0 without a named subtype)

The module is pure-function; ``compute_special_control_rules`` returns
the rule dict per material. The rule carries the approval role needed
to release the material (drug_precursor -> chief_safety_officer, etc.)
so the workflow engine in MVP-4 can validate the approver.
"""

from __future__ import annotations

from typing import Any

DEFAULT_BUFFERS = {0: 1.0, 1: 0.20, 2: 0.10}

LEVEL_LABELS = {
    0: "特控一级（剧毒/易制爆/易制毒）",
    1: "重点监管工艺",
    2: "企业自定义特控",
}

SUBTYPE_LABELS = {
    "toxic": "剧毒品",
    "explosive_precursor": "易制爆",
    "drug_precursor": "易制毒",
    "none": "未划分",
}

APPROVAL_ROLES = {
    "drug_precursor": "chief_safety_officer",
    "explosive_precursor": "plant_manager",
    "toxic": "plant_manager",
    "none": "planner",
}

DEFAULT_SUBTYPE_QUOTA = {
    "toxic": 0.20,
    "explosive_precursor": 0.15,
    "drug_precursor": 0.10,
}


def compute_special_control_rules(
    materials,
    overrides=None,
):
    """Return a ``{material_id: rule}`` map.

    ``overrides`` lets the user tune buffer percentages per material via
    the Excel master sheet (column ``special_control_buffer``).
    """
    overrides = overrides or {}
    rules = {}
    for material in materials:
        mid = str(material.get("id") or material.get("material_id") or "").strip()
        if not mid:
            continue
        raw_level = material.get("special_control_level")
        if raw_level not in (None, ""):
            level = int(raw_level)
        else:
            level = 2
        level = 0 if level < 0 else (2 if level > 2 else level)
        buffer = DEFAULT_BUFFERS[level]
        if mid in overrides and "buffer" in overrides[mid]:
            try:
                buffer = float(overrides[mid]["buffer"])
            except (TypeError, ValueError):
                pass
        subtype = str(material.get("special_control_subtype") or "").strip().lower()
        if subtype not in SUBTYPE_LABELS:
            subtype = "none" if level != 0 else "toxic"
        raw_quota = material.get("special_control_quota")
        if raw_quota not in (None, ""):
            quota = float(raw_quota)
        else:
            quota = DEFAULT_SUBTYPE_QUOTA.get(subtype, 0.20)
        quota = max(0.0, min(1.0, quota))
        rules[mid] = {
            "level": level,
            "level_label": LEVEL_LABELS[level],
            "subtype": subtype,
            "subtype_label": SUBTYPE_LABELS[subtype],
            "buffer": max(0.0, min(1.0, buffer)),
            "isolated": level == 0,
            "quota": quota,
            "approval_role": APPROVAL_ROLES.get(subtype, "planner"),
        }
    return rules


def adjust_atp(raw_atp, rule):
    """Apply buffer to a raw ATP figure. Returns 0 for isolated materials."""
    if not rule:
        return max(0.0, raw_atp)
    if rule.get("isolated"):
        return 0.0
    return max(0.0, raw_atp * (1.0 - rule["buffer"]))


def should_isolate(rule):
    return bool(rule and rule.get("isolated"))


def summarise(material_id, rule):
    if not rule:
        return {
            "material_id": material_id,
            "controlled": False,
            "level": None,
            "buffer": 0.0,
            "label": "普通",
        }
    return {
        "material_id": material_id,
        "controlled": rule["level"] < 2,
        "level": rule["level"],
        "level_label": rule.get("level_label", ""),
        "subtype": rule.get("subtype", "none"),
        "subtype_label": rule.get("subtype_label", ""),
        "buffer": rule["buffer"],
        "quota": rule.get("quota", 0.0),
        "isolated": rule.get("isolated", False),
        "approval_role": rule.get("approval_role", "planner"),
        "label": rule.get("subtype_label") or rule.get("level_label", "普通"),
    }


def _label(level):
    return LEVEL_LABELS.get(level, "普通")