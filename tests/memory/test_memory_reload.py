from pathlib import Path

from yoreni.config.schema import Config, MemoryConfig, ProvidersConfig
from yoreni.memory.service import MemoryService


def test_reload_config_swaps_embedding_and_budgets(tmp_path: Path) -> None:
    service = MemoryService(tmp_path, MemoryConfig())
    assert service.embedder is None

    memory = Config.model_validate({
        "memory": {
            "core_token_budget": 5000,
            "embedding": {"model": "nomic-embed-text", "api_base": "http://127.0.0.1:11434/v1"},
        },
    }).memory

    service.reload_config(memory, providers=ProvidersConfig())

    assert service.embedder is not None
    assert service.embedder.settings.model == "nomic-embed-text"
    assert service.retriever.embedder is service.embedder
    assert service.core.token_budget == 5000

    service.reload_config(MemoryConfig(), providers=ProvidersConfig())

    assert service.embedder is None
    assert service.retriever.embedder is None
