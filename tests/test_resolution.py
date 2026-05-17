"""Tests for reasoning/resolution.py — resolution risk gate heuristics."""
from __future__ import annotations

from ploy_agent.reasoning.resolution import heuristic_resolution_safe


def test_empty_criteria_is_safe():
    safe, reason = heuristic_resolution_safe(None)
    assert safe is True


def test_clean_criteria_safe():
    safe, _ = heuristic_resolution_safe("Will the Lakers win this game?")
    assert safe is True


def test_twitter_mention_unsafe():
    safe, reason = heuristic_resolution_safe("Based on official Twitter announcement")
    assert safe is False
    assert "ambiguous" in reason


def test_reddit_mention_unsafe():
    safe, _ = heuristic_resolution_safe("According to Reddit discussion thread")
    assert safe is False


def test_wikipedia_mention_unsafe():
    safe, _ = heuristic_resolution_safe("Per Wikipedia article on the subject")
    assert safe is False


def test_third_party_unsafe():
    safe, _ = heuristic_resolution_safe("Resolved by third-party source")
    assert safe is False


def test_officially_unsafe():
    safe, _ = heuristic_resolution_safe("Officially announced by the league")
    assert safe is False


def test_case_insensitive():
    safe, _ = heuristic_resolution_safe("ACCORDING TO news reports")
    assert safe is False
