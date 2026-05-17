"""Statistical confidence scoring — no LLM, no API key, pure market data."""

from __future__ import annotations

import math


def statistical_confidence(
    *,
    depth_1c: float,
    spread: float | None = None,
    n_siblings: int = 0,
    sum_deviation: float = 0.0,
    edge_cents: float = 0.0,
    mid: float = 0.5,
    is_binary_pair: bool = False,
) -> tuple[float, str]:
    """
    Compute a data-driven confidence score (0.0–1.0) from market microstructure.

    Factors:
      1. Liquidity (depth_1c): deep books → higher confidence
      2. Spread width: tight spread → price is well-established → higher confidence
      3. Sibling agreement: more corroborating markets → higher confidence
      4. Edge magnitude: extreme edges are more likely noise → penalize
      5. Mid extremity: prices near 0 or 1 are mechanically constrained → less informative
      6. Binary pair bonus: 2-outcome arbs are structurally cleaner than multi-outcome

    Returns (confidence, reasoning_string).
    """
    scores: list[tuple[str, float]] = []
    reasons: list[str] = []

    # 1. Liquidity score: log scale, saturates around $100K
    # depth=0 → 0.0, depth=1000 → 0.45, depth=10000 → 0.60, depth=100000 → 0.75
    liq = min(math.log1p(max(depth_1c, 0)) / math.log1p(100_000), 1.0)
    liq_score = 0.3 + 0.4 * liq  # range: 0.3–0.7
    scores.append(("liquidity", liq_score))
    if depth_1c < 1000:
        reasons.append(f"thin book ({depth_1c:.0f})")
    elif depth_1c > 50000:
        reasons.append(f"deep book ({depth_1c:.0f})")

    # 2. Spread score: tight = good. spread=0 → 1.0, spread=0.05 → 0.5, spread=0.10 → 0.25
    if spread is not None and spread >= 0:
        spr_score = 1.0 / (1.0 + 20.0 * spread)
    else:
        spr_score = 0.5  # unknown spread → neutral
    scores.append(("spread", spr_score))
    if spread is not None and spread > 0.04:
        reasons.append(f"wide spread ({spread:.3f})")

    # 3. Sibling agreement: more siblings confirming the arb → higher trust
    if n_siblings > 0:
        sib_score = min(0.5 + 0.15 * n_siblings, 0.9)
        if is_binary_pair:
            sib_score = min(sib_score + 0.1, 0.95)  # binary pairs are cleaner
            reasons.append("binary pair arb")
        else:
            reasons.append(f"{n_siblings + 1}-way arb (dev {sum_deviation:.3f})")
    else:
        sib_score = 0.4  # no sibling data → lower confidence
    scores.append(("siblings", sib_score))

    # 4. Edge magnitude penalty: huge edges (>30¢) are suspicious
    edge_abs = abs(edge_cents)
    if edge_abs > 50:
        edge_pen = 0.2
        reasons.append(f"extreme edge ({edge_abs:.1f}¢) — likely noise")
    elif edge_abs > 30:
        edge_pen = 0.4
        reasons.append(f"large edge ({edge_abs:.1f}¢)")
    elif edge_abs > 15:
        edge_pen = 0.6
    else:
        edge_pen = 0.8  # moderate edge is most trustworthy
    scores.append(("edge_size", edge_pen))

    # 5. Mid extremity: prices near 0 or 1 are mechanically bounded
    dist_from_extreme = min(mid, 1.0 - mid)
    if dist_from_extreme < 0.05:
        ext_score = 0.3
        reasons.append(f"extreme price ({mid:.3f})")
    elif dist_from_extreme < 0.15:
        ext_score = 0.5
    else:
        ext_score = 0.7
    scores.append(("mid_position", ext_score))

    # Weighted average
    weights = {
        "liquidity": 0.30,
        "spread": 0.15,
        "siblings": 0.25,
        "edge_size": 0.20,
        "mid_position": 0.10,
    }
    total_w = sum(weights.values())
    conf = sum(weights[name] * score for name, score in scores) / total_w

    # Clamp to [0.15, 0.95]
    conf = max(0.15, min(0.95, conf))

    if not reasons:
        reasons.append("standard signal")
    reasoning = "Confidence factors: " + "; ".join(reasons) + f". Score: {conf:.2f}."

    return round(conf, 3), reasoning
