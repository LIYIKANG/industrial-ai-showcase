"""参数上下限与求解设置的持久化（JSON 文件）。

单机单用户场景，用一个 JSON 文件足够；换多用户/多产线时把 load/save
换成数据库读写即可，其余代码不受影响。
"""

from __future__ import annotations

import copy
import json
import logging
import threading

from . import config

log = logging.getLogger(__name__)

_lock = threading.Lock()


def _defaults() -> dict:
    return {
        "limits": copy.deepcopy(config.DEFAULT_LIMITS),
        "settings": copy.deepcopy(config.DEFAULT_SETTINGS),
    }


def load() -> dict:
    """读取配置。文件不存在或损坏时回落到默认值，不抛异常。"""
    with _lock:
        if not config.LIMITS_FILE.exists():
            return _defaults()
        try:
            raw = json.loads(config.LIMITS_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("配置文件损坏，已回落到默认值：%s", config.LIMITS_FILE, exc_info=True)
            return _defaults()

    cfg = _defaults()
    # 逐项合并而非整体覆盖：新增参数时老配置文件仍然可用
    for key, val in (raw.get("limits") or {}).items():
        if key in cfg["limits"] and isinstance(val, dict):
            cfg["limits"][key].update(
                {k: v for k, v in val.items() if k in ("lo", "hi", "enabled")}
            )
    for key, val in (raw.get("settings") or {}).items():
        if key in cfg["settings"]:
            cfg["settings"][key] = val
    return cfg


def save(limits: dict | None = None, settings: dict | None = None) -> dict:
    """写入配置，返回合并后的完整配置。"""
    cfg = load()
    if limits:
        for key, val in limits.items():
            if key not in cfg["limits"]:
                continue
            item = cfg["limits"][key]
            if "lo" in val:
                item["lo"] = None if val["lo"] is None else float(val["lo"])
            if "hi" in val:
                item["hi"] = None if val["hi"] is None else float(val["hi"])
            if "enabled" in val:
                item["enabled"] = bool(val["enabled"])
            if (
                item.get("lo") is not None
                and item.get("hi") is not None
                and item["lo"] > item["hi"]
            ):
                raise ValueError(f"{key}：下限 {item['lo']} 不能大于上限 {item['hi']}")
    if settings:
        for key, val in settings.items():
            if key in cfg["settings"]:
                cfg["settings"][key] = val

    with _lock:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.LIMITS_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log.info("配置已保存：%s", config.LIMITS_FILE)
    return cfg


def reset() -> dict:
    """恢复出厂默认。"""
    cfg = _defaults()
    with _lock:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.LIMITS_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return cfg
