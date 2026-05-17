from __future__ import annotations

import json
from typing import Any

from ploy_agent.common.config import settings


def confidence_and_reasoning(
    *,
    question: str | None,
    model_prob: float,
    market_prob: float,
    edge_cents: float,
    game_summary: str,
    depth_1c: float = 0.0,
    spread: float | None = None,
) -> tuple[float, str, list[dict[str, Any]]]:
    """
    LLM produces confidence + narrative only (not the game probability).
    Falls back to statistical confidence when no API key.
    """
    if not settings.anthropic_api_key:
        from ploy_agent.common.confidence import statistical_confidence

        conf, reasoning = statistical_confidence(
            depth_1c=depth_1c,
            spread=spread,
            edge_cents=edge_cents,
            mid=market_prob,
        )
        return (
            conf,
            f"No LLM key — statistical confidence. {reasoning}",
            [{"type": "statistical", "detail": "market_microstructure"}],
        )
    import anthropic

    if not hasattr(confidence_and_reasoning, "_client"):
        confidence_and_reasoning._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key
        )
    ac = confidence_and_reasoning._client
    msg = ac.messages.create(
        model=settings.anthropic_model,
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": (
                    "You assess a sports Polymarket edge. A separate model already computed "
                    f"model_prob={model_prob:.4f} vs market_prob={market_prob:.4f} "
                    f"(edge_cents={edge_cents:.2f}).\n"
                    "Return JSON ONLY: {\"confidence\":0-1,\"reasoning\":\"...\","
                    "\"sources\":[{\"type\":\"...\",\"detail\":\"...\"}]}\n"
                    "Do NOT restate or invent model_prob. Confidence reflects mapping risk, "
                    "data staleness, and market microstructure—not the scoreboard itself.\n\n"
                    f"MARKET: {question}\nSTATE: {game_summary}\n"
                ),
            }
        ],
    )
    block = msg.content[0]
    if block.type != "text":
        return 0.5, "unexpected_block", []
    raw = block.text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return 0.5, raw[:500], []
    data = json.loads(raw[start : end + 1])
    conf = float(data.get("confidence", 0.5))
    conf = max(0.0, min(1.0, conf))
    reasoning = str(data.get("reasoning", ""))
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    return conf, reasoning, sources
