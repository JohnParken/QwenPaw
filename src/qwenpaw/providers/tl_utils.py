"""Small, dependency-free helpers for identifying TL models and formatters."""

from __future__ import annotations


def is_tl_formatter(formatter: object) -> bool:
    """Return whether a formatter explicitly identifies itself as TL."""
    return getattr(formatter, "_qwenpaw_tl_formatter", None) is True


def is_tl_model(model: object) -> bool:
    """Return whether a model's exposed formatter identifies itself as TL."""
    return is_tl_formatter(getattr(model, "formatter", None))


__all__ = ["is_tl_formatter", "is_tl_model"]
