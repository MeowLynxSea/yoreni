"""Tests for lazy provider exports from yoreni.providers."""

from __future__ import annotations

import importlib
import sys


def test_importing_providers_package_is_lazy(monkeypatch) -> None:
    original_package = sys.modules["yoreni.providers"]
    monkeypatch.delitem(sys.modules, "yoreni.providers", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.anthropic_provider", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.openai_compat_provider", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.openai_codex_provider", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.xai_oauth", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.xai_grok_provider", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.github_copilot_provider", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.azure_openai_provider", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.bedrock_provider", raising=False)

    try:
        providers = importlib.import_module("yoreni.providers")

        assert "yoreni.providers.anthropic_provider" not in sys.modules
        assert "yoreni.providers.openai_compat_provider" not in sys.modules
        assert "yoreni.providers.openai_codex_provider" not in sys.modules
        assert "yoreni.providers.xai_oauth" not in sys.modules
        assert "yoreni.providers.xai_grok_provider" not in sys.modules
        assert "yoreni.providers.github_copilot_provider" not in sys.modules
        assert "yoreni.providers.azure_openai_provider" not in sys.modules
        assert "yoreni.providers.bedrock_provider" not in sys.modules
        assert providers.__all__ == [
            "LLMProvider",
            "LLMResponse",
            "LLMUsage",
            "AnthropicProvider",
            "OpenAICompatProvider",
            "OpenAICodexProvider",
            "XAIGrokProvider",
            "GitHubCopilotProvider",
            "AzureOpenAIProvider",
            "BedrockProvider",
        ]
    finally:
        # Importing a replacement subpackage also replaces yoreni.providers on the
        # parent package. Restore both views so this isolation test cannot pollute
        # later tests that resolve a module through a dotted monkeypatch target.
        monkeypatch.undo()
        setattr(sys.modules["yoreni"], "providers", original_package)


def test_explicit_provider_import_still_works(monkeypatch) -> None:
    original_package = sys.modules["yoreni.providers"]
    monkeypatch.delitem(sys.modules, "yoreni.providers", raising=False)
    monkeypatch.delitem(sys.modules, "yoreni.providers.anthropic_provider", raising=False)

    try:
        namespace: dict[str, object] = {}
        exec("from yoreni.providers import AnthropicProvider", namespace)

        assert namespace["AnthropicProvider"].__name__ == "AnthropicProvider"
        assert "yoreni.providers.anthropic_provider" in sys.modules
    finally:
        monkeypatch.undo()
        setattr(sys.modules["yoreni"], "providers", original_package)
