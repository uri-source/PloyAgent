import pytest

from ploy_agent.common.config import Settings
from ploy_agent.strategies import get_enabled
from ploy_agent.strategies.baseline_model import BaselineModelStrategy


def test_get_enabled_unknown_falls_back_to_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_STRATEGIES", "not_a_real_strategy")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    s = Settings()
    enabled = get_enabled(s)
    assert len(enabled) >= 1
    assert isinstance(enabled[0], BaselineModelStrategy)


def test_get_enabled_baseline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_STRATEGIES", "baseline_model")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    s = Settings()
    enabled = get_enabled(s)
    ids = [type(x).id for x in enabled]
    assert ids == ["baseline_model"]


def test_sportsbook_skipped_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_STRATEGIES", "sportsbook_consensus")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    s = Settings()
    enabled = get_enabled(s)
    assert any(isinstance(x, BaselineModelStrategy) for x in enabled)
