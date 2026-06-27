from __future__ import annotations

import pytest

from ploy_agent.kalshi.client import parse_orderbook


def test_parse_orderbook_yes_no_mids():
    payload = {
        "orderbook": {
            "yes": [[45, 100], [44, 50]],
            "no": [[52, 80]],
        }
    }
    bid, ask, mid, depth = parse_orderbook(payload)
    assert bid == 0.45
    assert ask == 0.48  # (100-52)/100
    assert mid is not None
    assert mid > 0.4
    assert depth >= 0


def test_parse_orderbook_fp_dollars():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.2140", "50"], ["0.2000", "100"]],
            "no_dollars": [["0.7790", "50"]],
        }
    }
    bid, ask, mid, depth = parse_orderbook(payload)
    assert bid == 0.214
    assert ask == pytest.approx(0.221)
    assert mid == pytest.approx(0.2175)
    assert depth > 0
