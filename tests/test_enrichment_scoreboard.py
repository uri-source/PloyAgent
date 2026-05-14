from __future__ import annotations

from ploy_agent.enrichment.sports import LiveGame, _parse_espn_scoreboard_payload


def _minimal_espn_event(game_id: str = "401000001") -> dict:
    return {
        "id": game_id,
        "competitions": [
            {
                "id": game_id,
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {
                            "displayName": "Yankees",
                            "shortDisplayName": "NYY",
                            "abbreviation": "NYY",
                        },
                        "score": "5",
                    },
                    {
                        "homeAway": "away",
                        "team": {
                            "displayName": "Red Sox",
                            "shortDisplayName": "BOS",
                            "abbreviation": "BOS",
                        },
                        "score": "3",
                    },
                ],
                "status": {
                    "type": {"completed": False, "shortDetail": "Top 7th"},
                    "period": 7,
                    "displayClock": "1 OUT",
                },
            }
        ],
    }


def test_parse_espn_scoreboard_sets_league() -> None:
    data = {"events": [_minimal_espn_event()]}
    games = _parse_espn_scoreboard_payload(data, "mlb")
    assert len(games) == 1
    g = games[0]
    assert isinstance(g, LiveGame)
    assert g.league == "mlb"
    assert g.game_id == "401000001"
    assert g.home_team == "Yankees"
    assert g.away_team == "Red Sox"
    assert g.home_score == 5
    assert g.away_score == 3
    assert g.period == 7
    assert g.espn_summary_league_key() == "mlb"


def test_live_game_default_league_key_for_summary() -> None:
    g = LiveGame(
        game_id="1",
        home_team="A",
        away_team="B",
        home_score=0,
        away_score=0,
        period=None,
        time_remaining=None,
        possession=None,
        completed=False,
    )
    assert g.espn_summary_league_key() == "nba"
