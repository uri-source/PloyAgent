from ploy_agent.strategies.odds_math import american_to_implied_prob, devig_two_way


def test_american_to_implied_negative() -> None:
    p = american_to_implied_prob(-110)
    assert 0.51 < p < 0.53


def test_american_to_implied_positive() -> None:
    p = american_to_implied_prob(150)
    assert 0.39 < p < 0.41


def test_devig_two_way() -> None:
    a, b = devig_two_way(0.55, 0.50)
    assert abs(a + b - 1.0) < 1e-9
    assert a > b
