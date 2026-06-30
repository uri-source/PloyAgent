from __future__ import annotations

from datetime import date

from ploy_agent.kalshi.wc_game_mapper import (
    match_wc_game_pairs,
    parse_kalshi_outcome,
    parse_poly_moneyline,
    score_match,
)


def test_parse_poly_jordan_argentina_win():
    aliases = {"jor": "jordan", "arg": "argentina"}
    pm = parse_poly_moneyline(
        market_id="1897442",
        question="Will Argentina win on 2026-06-27?",
        event_slug="fifwc-jor-arg-2026-06-27",
        aliases=aliases,
    )
    assert pm is not None
    assert pm.outcome == "team_b"
    assert pm.team_a == "jordan"
    assert pm.team_b == "argentina"
    assert pm.kickoff_date == date(2026, 6, 27)


def test_parse_poly_draw():
    aliases = {"jor": "jordan", "arg": "argentina"}
    pm = parse_poly_moneyline(
        market_id="1897441",
        question="Will Jordan vs Argentina end in a draw?",
        event_slug="fifwc-jor-arg-2026-06-27",
        aliases=aliases,
    )
    assert pm is not None
    assert pm.outcome == "draw"


def test_parse_kalshi_jordan_argentina():
    aliases = {"jor": "jordan", "arg": "argentina"}
    km = {
        "ticker": "KXWCGAME-26JUN27JORARG-ARG",
        "event_ticker": "KXWCGAME-26JUN27JORARG",
        "title": "Jordan vs Argentina Winner?",
        "yes_sub_title": "Argentina",
        "occurrence_datetime": "2026-06-27T19:00:00Z",
    }
    ko = parse_kalshi_outcome(km, aliases)
    assert ko is not None
    assert ko.outcome == "team_b"
    assert ko.team_a == "jordan"
    assert ko.team_b == "argentina"


def test_score_match_high_confidence():
    aliases = {"jor": "jordan", "arg": "argentina"}
    pm = parse_poly_moneyline(
        market_id="1897442",
        question="Will Argentina win on 2026-06-27?",
        event_slug="fifwc-jor-arg-2026-06-27",
        aliases=aliases,
    )
    ko = parse_kalshi_outcome(
        {
            "ticker": "KXWCGAME-26JUN27JORARG-ARG",
            "event_ticker": "KXWCGAME-26JUN27JORARG",
            "title": "Jordan vs Argentina Winner?",
            "yes_sub_title": "Argentina",
            "occurrence_datetime": "2026-06-27T19:00:00Z",
        },
        aliases,
    )
    assert pm and ko
    conf = score_match(pm, ko, slug_codes_match=True)
    assert conf >= 0.85


def test_match_wc_game_pairs_integration():
    aliases = {"jor": "jordan", "arg": "argentina"}
    poly_rows = [
        {
            "id": "1897442",
            "question": "Will Argentina win on 2026-06-27?",
            "event_slug": "fifwc-jor-arg-2026-06-27",
        },
    ]
    kalshi = [
        {
            "ticker": "KXWCGAME-26JUN27JORARG-ARG",
            "event_ticker": "KXWCGAME-26JUN27JORARG",
            "title": "Jordan vs Argentina Winner?",
            "yes_sub_title": "Argentina",
            "occurrence_datetime": "2026-06-27T19:00:00Z",
        },
    ]
    pairs = match_wc_game_pairs(
        poly_rows, kalshi, aliases, min_confidence=0.85, review_confidence=0.60
    )
    assert len(pairs) == 1
    assert pairs[0].active is True
    assert pairs[0].kalshi_ticker.endswith("-ARG")
