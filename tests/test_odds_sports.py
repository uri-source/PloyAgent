from __future__ import annotations

from ploy_agent.common.odds_sports import odds_sport_key_for_category


def test_odds_sport_key_nba_mlb_slugs() -> None:
    assert odds_sport_key_for_category("nba") == "basketball_nba"
    assert odds_sport_key_for_category("MLB") == "baseball_mlb"
    assert odds_sport_key_for_category("wnba") == "basketball_wnba"


def test_odds_sport_key_unknown_and_none() -> None:
    assert odds_sport_key_for_category(None) is None
    assert odds_sport_key_for_category("") is None
    assert odds_sport_key_for_category("   ") is None
    assert odds_sport_key_for_category("politics") is None


def test_odds_sport_key_passthrough_full_key() -> None:
    assert odds_sport_key_for_category("basketball_nba") == "basketball_nba"
    assert odds_sport_key_for_category("baseball_mlb") == "baseball_mlb"
