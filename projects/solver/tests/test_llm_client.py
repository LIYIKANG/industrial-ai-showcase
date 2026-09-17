from unittest.mock import patch

import pytest

from backend.services import llm_client


def test_catalog_returns_both_providers():
    catalog = llm_client.catalog()
    ids = {provider["id"] for provider in catalog["providers"]}
    assert ids == {"ollama", "deepseek"}


def test_ollama_status_unreachable_returns_connected_false():
    with patch("backend.services.llm_client.OpenAI") as openai_cls:
        openai_cls.return_value.models.list.side_effect = RuntimeError("connection refused")
        result = llm_client.status("ollama")
    assert result["connected"] is False
    assert result["provider"] == "ollama"
    assert result["privacy"] == "local"
    assert "connection refused" in result["error"]


def test_deepseek_status_without_key_is_not_configured():
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("DEEPSEEK_API_KEY", None)
        result = llm_client.status("deepseek", api_key=None)
    assert result["connected"] is False
    assert result["configured"] is False
    assert "API Key" in result["error"]


def test_unknown_provider_status_raises():
    with pytest.raises(ValueError):
        llm_client.status("gpt-9000")


def test_chat_empty_prompt_rejected():
    result = llm_client.chat("   ", provider="ollama")
    assert result["ok"] is False
    assert "为空" in result["error"] or "empty" in result["error"].lower()


def test_chat_unknown_provider_rejected():
    result = llm_client.chat("hello", provider="unknown")
    assert result["ok"] is False
    assert "不支持" in result["error"]
