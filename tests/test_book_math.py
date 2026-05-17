"""Tests for ingestion/book_math.py — bid/ask parsing and depth calculation."""
from __future__ import annotations

from ploy_agent.ingestion.book_math import best_bid_ask_from_book, depth_within_one_cent, mid_from_ba


def test_basic_bid_ask():
    bids = [{"price": "0.45", "size": "100"}, {"price": "0.44", "size": "200"}]
    asks = [{"price": "0.55", "size": "100"}, {"price": "0.56", "size": "200"}]
    bb, ba = best_bid_ask_from_book(bids, asks)
    assert bb == 0.45
    assert ba == 0.55


def test_zero_bid_filtered():
    """Bids with price 0 should be excluded."""
    bids = [{"price": "0"}, {"price": "0.00"}, {"price": "0.30"}]
    asks = [{"price": "0.70"}]
    bb, ba = best_bid_ask_from_book(bids, asks)
    assert bb == 0.30
    assert ba == 0.70


def test_empty_bids():
    bb, ba = best_bid_ask_from_book([], [{"price": "0.55"}])
    assert bb is None
    assert ba == 0.55


def test_empty_asks():
    bb, ba = best_bid_ask_from_book([{"price": "0.45"}], [])
    assert bb == 0.45
    assert ba is None


def test_both_empty():
    bb, ba = best_bid_ask_from_book([], [])
    assert bb is None
    assert ba is None


def test_invalid_price_strings():
    bids = [{"price": "abc"}, {"price": "0.40"}]
    asks = [{"price": ""}, {"price": "0.60"}]
    bb, ba = best_bid_ask_from_book(bids, asks)
    assert bb == 0.40
    assert ba == 0.60


# --- mid ---

def test_mid_both_sides():
    assert mid_from_ba(0.45, 0.55) == 0.50


def test_mid_bid_only():
    assert mid_from_ba(0.45, None) == 0.45


def test_mid_ask_only():
    assert mid_from_ba(None, 0.55) == 0.55


def test_mid_both_none():
    assert mid_from_ba(None, None) is None


# --- depth ---

def test_depth_within_one_cent():
    bids = [
        {"price": "0.50", "size": "100"},
        {"price": "0.495", "size": "50"},
        {"price": "0.48", "size": "200"},  # outside 1 cent
    ]
    asks = [
        {"price": "0.52", "size": "80"},
        {"price": "0.525", "size": "40"},
        {"price": "0.54", "size": "300"},  # outside 1 cent
    ]
    d = depth_within_one_cent(bids, asks, best_bid=0.50, best_ask=0.52)
    assert d == 100 + 50 + 80 + 40  # 270
