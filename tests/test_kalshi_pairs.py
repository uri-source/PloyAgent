from __future__ import annotations

from pathlib import Path

import pytest

from ploy_agent.kalshi.pairs import load_pairs_yaml


def test_load_pairs_yaml(tmp_path: Path):
    p = tmp_path / "pairs.yaml"
    p.write_text(
        """
pairs:
  - id: test-pair
    label: Test match
    poly_market_id: "0xabc"
    kalshi_ticker: "KXTEST-1"
    outcome_map: same
    active: true
""",
        encoding="utf-8",
    )
    specs = load_pairs_yaml(p)
    assert len(specs) == 1
    assert specs[0].id == "test-pair"
    assert specs[0].kalshi_ticker == "KXTEST-1"


def test_load_pairs_invalid_outcome_map(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
pairs:
  - id: x
    poly_market_id: "a"
    kalshi_ticker: "b"
    outcome_map: invalid
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="outcome_map"):
        load_pairs_yaml(p)
