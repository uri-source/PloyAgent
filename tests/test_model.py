"""Tests for reasoning/model.py — win probability model and question alignment."""
from __future__ import annotations

import pytest

from ploy_agent.reasoning.model import _sigmoid, align_prob_to_yes, load_model, predict_home_win_prob


@pytest.fixture
def model() -> dict:
    return load_model()


# --- sigmoid ---

def test_sigmoid_zero():
    assert abs(_sigmoid(0.0) - 0.5) < 1e-6


def test_sigmoid_large_pos():
    assert _sigmoid(50.0) == 1.0


def test_sigmoid_large_neg():
    assert _sigmoid(-50.0) == 0.0


def test_sigmoid_symmetry():
    assert abs(_sigmoid(2.0) + _sigmoid(-2.0) - 1.0) < 1e-6


# --- predict_home_win_prob ---

def test_tied_game_near_50(model):
    """A tied game should produce close to 50% home win prob."""
    p = predict_home_win_prob(
        model, home_score=50, away_score=50, period=3,
        possession=None, home_team="LAL", away_team="BOS",
    )
    assert 0.35 < p < 0.65


def test_big_lead_favors_leader(model):
    """A 20-point lead late in the game should strongly favor the leader."""
    p = predict_home_win_prob(
        model, home_score=100, away_score=80, period=4,
        possession=None, home_team="LAL", away_team="BOS",
    )
    assert p > 0.8


def test_early_lead_less_decisive(model):
    """Same lead in Q1 should be less decisive than Q4."""
    p_q1 = predict_home_win_prob(
        model, home_score=20, away_score=10, period=1,
        possession=None, home_team="LAL", away_team="BOS",
    )
    p_q4 = predict_home_win_prob(
        model, home_score=20, away_score=10, period=4,
        possession=None, home_team="LAL", away_team="BOS",
    )
    # Q4 lead should be MORE decisive (further from 0.5)
    assert abs(p_q4 - 0.5) > abs(p_q1 - 0.5)


def test_possession_matters(model):
    """Home possession should slightly increase home win prob."""
    p_home = predict_home_win_prob(
        model, home_score=50, away_score=50, period=3,
        possession="LAL", home_team="LAL", away_team="BOS",
    )
    p_away = predict_home_win_prob(
        model, home_score=50, away_score=50, period=3,
        possession="BOS", home_team="LAL", away_team="BOS",
    )
    assert p_home > p_away


def test_overtime_handled(model):
    """Period 5 (overtime) should not crash and should clamp elapsed."""
    p = predict_home_win_prob(
        model, home_score=110, away_score=108, period=5,
        possession=None, home_team="LAL", away_team="BOS",
    )
    assert 0.0 < p < 1.0


def test_no_score_normalization_artifact(model):
    """Same 2-point lead at different total scores should be similar (not 50x different)."""
    p_early = predict_home_win_prob(
        model, home_score=5, away_score=3, period=1,
        possession=None, home_team="LAL", away_team="BOS",
    )
    p_late = predict_home_win_prob(
        model, home_score=55, away_score=53, period=1,
        possession=None, home_team="LAL", away_team="BOS",
    )
    # With raw diff, these should be identical (same diff, same period)
    assert abs(p_early - p_late) < 0.01


# --- align_prob_to_yes ---

def test_align_home_team_wins():
    """'Will Lakers beat Celtics' with p_home=0.7 → YES=0.7."""
    p = align_prob_to_yes("Will the Lakers beat the Celtics?", home_team="Lakers", away_team="Celtics", p_home_wins=0.7)
    assert p == 0.7


def test_align_away_team_wins():
    """'Will Celtics beat Lakers' with p_home=0.7 → YES=0.3."""
    p = align_prob_to_yes("Will the Celtics beat the Lakers?", home_team="Lakers", away_team="Celtics", p_home_wins=0.7)
    assert abs(p - 0.3) < 1e-6


def test_align_no_question():
    assert align_prob_to_yes(None, home_team="LAL", away_team="BOS", p_home_wins=0.5) is None


def test_align_no_team_mention():
    assert align_prob_to_yes("Will it rain tomorrow?", home_team="LAL", away_team="BOS", p_home_wins=0.5) is None
