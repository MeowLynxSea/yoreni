"""Runtime helpers for SDK calls."""

from __future__ import annotations


def ensure_single_model_selector(
    *,
    model: str | None,
    model_preset: str | None,
) -> None:
    if model is not None and model_preset is not None:
        raise ValueError("model and model_preset are mutually exclusive")

