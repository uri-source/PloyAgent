from ploy_agent.common.scoring import composite_score, edge_cents, hours_until, time_factor_hours


def test_edge_cents() -> None:
    assert abs(edge_cents(0.55, 0.50) - 5.0) < 1e-6


def test_time_factor_hours() -> None:
    assert time_factor_hours(0.0) == 1.0
    assert time_factor_hours(1.0) == 0.5


def test_composite_score_shape() -> None:
    s = composite_score(5.0, 10.0, 0.8, hours_to_resolution=2.0)
    assert s > 0


def test_hours_until_none() -> None:
    assert hours_until(None) == 24.0
