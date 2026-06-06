"""Deterministic, fact-grounded explanation builders for recommendations.

Every user-facing "why this" sentence is produced here (or by the per-strategy
builders that import `direction_label`) from real numeric inputs only. No LLM text
ever flows into these strings, so the explanation cannot hallucinate by construction.
"""

from __future__ import annotations


def direction_label(edge_cents: float) -> str:
    """BUY when the model thinks Yes is underpriced (edge > 0), else SELL."""
    return "BUY" if edge_cents >= 0 else "SELL"
