"""Guards that every recommendation explanation is deterministic and fact-grounded.

These tests are the anti-hallucination contract: each builder is a pure function of
real numbers, the output is a single sentence, and it states the correct BUY/SELL
direction and the actual numbers — nothing invented.
"""

from __future__ import annotations

from ploy_agent.common.explain import direction_label
from ploy_agent.strategies.baseline_model import baseline_sentence
from ploy_agent.strategies.behavior_fade import behavior_fade_sentence
from ploy_agent.strategies.cross_market_arb import multi_sentence, pair_sentence
from ploy_agent.strategies.player_adjust import player_adjust_sentence


def _single_sentence(text: str) -> bool:
    # One concise sentence: no newlines, ends with a period.
    return "\n" not in text and text.strip().endswith(".")


def test_direction_label():
    assert direction_label(5.0) == "BUY"
    assert direction_label(0.0) == "BUY"
    assert direction_label(-5.0) == "SELL"


def test_pair_sentence_is_grounded():
    s = pair_sentence("Team A wins", 0.62, "Team B wins", 0.30, 0.70, 8.0)
    assert _single_sentence(s)
    assert s.startswith("BUY:")
    # names and the real numbers appear; the sum (0.92) is computed, not invented
    assert "Team A wins" in s and "Team B wins" in s
    assert "0.62" in s and "0.30" in s and "0.92" in s
    assert "8.0¢ edge" in s


def test_pair_sentence_sell_direction():
    s = pair_sentence("Yes", 0.55, "No", 0.60, 0.40, -5.0)
    assert s.startswith("SELL:")


def test_multi_sentence_is_grounded():
    s = multi_sentence("Player X 50+ pts", 0.48, 20, 0.93, 0.51, 3.6)
    assert _single_sentence(s)
    assert "Player X 50+ pts" in s
    assert "20 mutually-exclusive outcomes" in s
    assert "0.93" in s and "0.51" in s and "3.6¢ edge" in s


def test_baseline_sentence_is_grounded():
    s = baseline_sentence(
        p_yes=0.58, mid=0.50, edge_cents=8.0,
        away_team="Knicks", away_score=88, home_team="Spurs", home_score=84,
        period=4, possession="home",
    )
    assert _single_sentence(s)
    assert s.startswith("BUY:")
    # exactly the scoreboard facts, nothing else
    assert "Knicks 88–84 Spurs" in s
    assert "P4" in s and "home ball" in s
    assert "0.58" in s and "0.50" in s


def test_baseline_sentence_omits_missing_state():
    s = baseline_sentence(
        p_yes=0.40, mid=0.50, edge_cents=-10.0,
        away_team="A", away_score=0, home_team="B", home_score=0,
        period=None, possession=None,
    )
    assert s.startswith("SELL:")
    assert "ball" not in s  # no possession claim when unknown
    assert "P" not in s.split("from")[1]  # no period claim when unknown


def test_player_adjust_sentence_is_grounded():
    s = player_adjust_sentence(adj=0.012, dh=1.50, da=-0.80, p_yes=0.61, mid=0.55, edge_cents=6.0)
    assert _single_sentence(s)
    assert "+0.012" in s and "+1.50" in s and "-0.80" in s
    assert "0.61" in s and "0.55" in s


def test_behavior_fade_sentence_is_grounded():
    s = behavior_fade_sentence(
        price_delta=0.08, model_delta=0.02, fade_target=0.74, last_mid=0.80, edge_cents=-6.0,
    )
    assert _single_sentence(s)
    assert s.startswith("SELL:")
    assert "+0.08" in s and "+0.02" in s and "0.74" in s and "0.80" in s
