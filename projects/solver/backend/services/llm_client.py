"""LLM client backed by the OpenAI SDK.

Both Ollama and DeepSeek expose OpenAI-compatible HTTP endpoints, so a single
``openai.OpenAI`` client handles both providers - we just swap ``base_url`` and
``api_key``. The Ollama native ``/api/chat`` ``format=json`` is emulated via
strict JSON prompts + a caller-side extractor; DeepSeek keeps the real
``response_format={"type": "json_object"}`` enforcement.
"""

from __future__ import annotations

import os
import time
from typing import Any

from openai import OpenAI

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"

_PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "name": "Ollama 本地模型",
        "privacy": "local",
        "requires_api_key": False,
        "base_url_env": "OLLAMA_BASE_URL",
        "default_url": DEFAULT_OLLAMA_URL,
        "default_model_env": "OLLAMA_MODEL",
        "default_model": DEFAULT_OLLAMA_MODEL,
        "static_models": None,
        "hint": "请先运行 ollama serve。",
    },
    "deepseek": {
        "name": "DeepSeek API",
        "privacy": "cloud",
        "requires_api_key": True,
        "base_url_env": "DEEPSEEK_BASE_URL",
        "default_url": DEFAULT_DEEPSEEK_URL,
        "default_model_env": "DEEPSEEK_MODEL",
        "default_model": DEFAULT_DEEPSEEK_MODEL,
        "static_models": ["deepseek-chat", "deepseek-reasoner"],
        "hint": None,
    },
}


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, default)))
    except ValueError:
        return default


def _build_client(provider: str, api_key: str | None, *, timeout: float) -> tuple[OpenAI, str]:
    cfg = _PROVIDERS[provider]
    base = os.getenv(cfg["base_url_env"], cfg["default_url"]).rstrip("/")
    if provider == "ollama":
        key = api_key or "ollama"  # SDK requires a non-empty key
    else:
        key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    client = OpenAI(base_url=f"{base}/v1", api_key=key, timeout=timeout)
    return client, base


def _selected_model(provider: str, model: str | None) -> str:
    cfg = _PROVIDERS[provider]
    return model or os.getenv(cfg["default_model_env"], cfg["default_model"])


def catalog() -> dict[str, Any]:
    providers = []
    for pid, cfg in _PROVIDERS.items():
        live = status(pid)
        providers.append(
            {
                "id": pid,
                "name": cfg["name"],
                "privacy": cfg["privacy"],
                "requires_api_key": cfg["requires_api_key"],
                "default_model": live.get("selected_model") or cfg["default_model"],
                "models": live.get("models") or cfg.get("static_models") or [],
                "connected": live.get("connected", False),
                "configured": live.get("configured", not cfg["requires_api_key"]),
            }
        )
    return {"providers": providers}


def status(
    provider: str = "ollama",
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    provider = provider.strip().lower()
    cfg = _PROVIDERS.get(provider)
    if not cfg:
        raise ValueError(f"不支持的模型供应商：{provider}")

    base = os.getenv(cfg["base_url_env"], cfg["default_url"]).rstrip("/")
    selected = _selected_model(provider, model)
    privacy = cfg["privacy"]
    requires_key = cfg["requires_api_key"]
    fallback_models = cfg.get("static_models") or []

    key = ""
    if provider == "ollama":
        key = api_key or "ollama"
    else:
        key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()

    if requires_key and not key:
        return {
            "provider": provider,
            "connected": False,
            "privacy": privacy,
            "configured": False,
            "base_url": base,
            "selected_model": selected,
            "models": fallback_models,
            "model_ready": False,
            "error": f"未提供 {cfg['name']} API Key。",
        }

    try:
        client, base = _build_client(provider, key, timeout=_env_float(f"{provider.upper()}_STATUS_TIMEOUT", 8))
        response = client.models.list()
        models = [item.id for item in getattr(response, "data", []) if getattr(item, "id", None)]
        models = models or fallback_models
        return {
            "provider": provider,
            "connected": True,
            "privacy": privacy,
            "configured": True,
            "base_url": base,
            "selected_model": selected,
            "models": models,
            "model_ready": not models or selected in models,
        }
    except Exception as exc:  # SDK raises OpenAIError; bare Exception covers transport too
        payload: dict[str, Any] = {
            "provider": provider,
            "connected": False,
            "privacy": privacy,
            "configured": bool(key) or not requires_key,
            "base_url": base,
            "selected_model": selected,
            "models": fallback_models,
            "model_ready": False,
            "error": str(exc),
        }
        if cfg.get("hint"):
            payload["hint"] = cfg["hint"]
        return payload


def chat(
    text: str,
    *,
    provider: str = "ollama",
    model: str | None = None,
    api_key: str | None = None,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 1800,
) -> dict[str, Any]:
    prompt = (text or "").strip()
    if not prompt:
        return {"ok": False, "error": "输入不能为空。"}

    provider = provider.strip().lower()
    cfg = _PROVIDERS.get(provider)
    if not cfg:
        return {"ok": False, "provider": provider, "error": f"不支持的模型供应商：{provider}"}

    base = os.getenv(cfg["base_url_env"], cfg["default_url"]).rstrip("/")
    selected = _selected_model(provider, model)
    if provider == "ollama":
        key = api_key or "ollama"
    else:
        key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
        if not key:
            return {"ok": False, "provider": provider, "error": "未提供 DeepSeek API Key。"}

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    timeout = _env_float(f"{provider.upper()}_TIMEOUT", 300 if provider == "ollama" else 180)
    try:
        client, base = _build_client(provider, key, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "provider": provider, "model": selected, "error": str(exc)}

    request_kwargs: dict[str, Any] = {
        "model": selected,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    # Only DeepSeek honours response_format on the OpenAI-compat surface.
    # For Ollama we rely on the strict JSON prompt + caller-side extractor.
    if json_mode and provider == "deepseek":
        request_kwargs["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    try:
        response = client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        return {"ok": False, "provider": provider, "model": selected, "error": str(exc)}

    content = ""
    if getattr(response, "choices", None):
        content = response.choices[0].message.content or ""

    usage = getattr(response, "usage", None)
    metrics: dict[str, Any] = {}
    if usage is not None:
        for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, attr, None)
            if value is not None:
                metrics[attr] = value

    return {
        "ok": True,
        "provider": provider,
        "privacy": cfg["privacy"],
        "model": getattr(response, "model", selected) or selected,
        "base_url": base,
        "content": content,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "metrics": metrics,
    }
