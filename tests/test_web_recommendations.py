from __future__ import annotations

from ploy_agent.web.app import _payload_dict, _pick_dicts, _recommendation_dicts


def test_payload_dict_parses_json_string() -> None:
    assert _payload_dict('{"edge_cents": 5.5}')["edge_cents"] == 5.5


def test_pick_dicts_direction() -> None:
    class P:
        strategy_id = "baseline_model"
        market_id = "m1"
        question = "Test?"
        mid = 0.5
        model_prob = 0.6
        market_prob = 0.5
        edge_cents = 10.0
        confidence = 0.7
        reasoning = "edge"
        depth_1c = 100.0
        score = 2.5
        kelly_frac = 0.1

    d = _pick_dicts([P()])[0]
    assert d["direction"] == "BUY"
    assert d["edge_cents"] == 10.0


def test_recommendation_dicts() -> None:
    rows = [
        {
            "id": 1,
            "ts": "2026-01-01",
            "market_id": "m1",
            "strategy_id": "cross_market_arb",
            "score": 3.0,
            "status": "pending",
            "question": "IPO?",
            "category": "tech",
            "payload_json": {"edge_cents": -4.0, "reasoning": "arb"},
        }
    ]
    d = _recommendation_dicts(rows)[0]
    assert d["direction"] == "SELL"
    assert d["status"] == "pending"
