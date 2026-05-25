from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import asyncpg
import numpy as np

from ploy_agent.common.config import settings


def _word_in_train(needle: str, haystack: str) -> bool:
    """Word-boundary match — mirrors model.py _word_in for train/serve parity."""
    if not needle:
        return False
    return bool(re.search(r'\b' + re.escape(needle) + r'\b', haystack, re.IGNORECASE))


async def _fetch_training_data(database_url: str) -> list[dict[str, Any]]:
    """Pull resolved markets with game state snapshots for training."""
    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            """
            WITH resolved AS (
              SELECT DISTINCT ON (r.market_id)
                     r.market_id, r.resolved_outcome, r.strategy_id
              FROM recommendations r
              WHERE r.resolved_outcome IS NOT NULL
            ),
            game_snaps AS (
              SELECT DISTINCT ON (mg.market_id)
                     mg.market_id,
                     gs.home_score, gs.away_score, gs.period,
                     gs.possession, gs.home_team, gs.away_team
              FROM market_game_map mg
              JOIN game_state gs ON gs.game_id = mg.game_id
              ORDER BY mg.market_id, gs.ts DESC
            )
            SELECT r.market_id, r.resolved_outcome,
                   g.home_score, g.away_score, g.period,
                   g.possession, g.home_team, g.away_team
            FROM resolved r
            JOIN game_snaps g ON g.market_id = r.market_id
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _fetch_fair_value_data(database_url: str) -> list[dict[str, Any]]:
    """Fallback: use fair_values + final price resolution when no recommendations resolved."""
    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            """
            WITH final_prices AS (
              SELECT DISTINCT ON (market_id) market_id, mid
              FROM prices WHERE mid IS NOT NULL
              ORDER BY market_id, ts DESC
            ),
            resolved AS (
              SELECT fp.market_id,
                     CASE WHEN fp.mid > 0.9 THEN 1 WHEN fp.mid < 0.1 THEN 0 ELSE NULL END AS outcome
              FROM final_prices fp
              JOIN markets m ON m.id = fp.market_id
              WHERE m.status = 'closed'
            ),
            game_snaps AS (
              SELECT DISTINCT ON (mg.market_id)
                     mg.market_id,
                     gs.home_score, gs.away_score, gs.period,
                     gs.possession, gs.home_team, gs.away_team
              FROM market_game_map mg
              JOIN game_state gs ON gs.game_id = mg.game_id
              ORDER BY mg.market_id, gs.ts DESC
            )
            SELECT r.market_id, r.outcome AS resolved_outcome,
                   g.home_score, g.away_score, g.period,
                   g.possession, g.home_team, g.away_team
            FROM resolved r
            JOIN game_snaps g ON g.market_id = r.market_id
            WHERE r.outcome IS NOT NULL
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def _build_features(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Build feature matrix matching predict_home_win_prob runtime features."""
    X_list = []
    y_list = []

    for r in rows:
        hs = int(r["home_score"] or 0)
        as_ = int(r["away_score"] or 0)
        diff = float(hs - as_)
        period_n = int(r["period"] or 1)
        # Match runtime model.py: Q1-Q4 use elapsed_frac, OT uses 300s
        reg_sec = 2880.0
        if period_n <= 4:
            elapsed_frac = max(0.0, (period_n - 1) / 4.0)
            remaining = reg_sec * (1.0 - elapsed_frac)
        else:
            remaining = 300.0  # OT = 5 min
        remaining_ratio = remaining / reg_sec

        poss = 0.0
        possession = r.get("possession")
        home_team = str(r.get("home_team") or "").lower()
        away_team = str(r.get("away_team") or "").lower()
        if possession:
            pl = str(possession).lower()
            if home_team and _word_in_train(home_team, pl):
                poss = 1.0
            elif away_team and _word_in_train(away_team, pl):
                poss = -1.0

        # Features: raw diff (not normalized), remaining_ratio, possession
        X_list.append([diff, remaining_ratio, poss])
        y_list.append(int(r["resolved_outcome"]))

    return np.array(X_list, dtype=float), np.array(y_list, dtype=int)


def _train_synthetic(n: int = 8000) -> dict[str, Any]:
    """Fallback: synthetic training when no DB data available."""
    print(f"Training on {n} synthetic samples (no real data available)...")
    rng = np.random.default_rng(42)
    diff = rng.integers(-25, 26, size=n).astype(float)
    remaining_ratio = rng.uniform(0.0, 1.0, size=n)
    poss = rng.choice([-1.0, 0.0, 1.0], size=n)

    # Simulate realistic outcomes
    z = 0.04 * diff - 1.2 * remaining_ratio + 0.15 * poss + rng.normal(0, 0.3, size=n)
    y = (z > 0).astype(int)

    from sklearn.linear_model import LogisticRegression

    X = np.column_stack([diff, remaining_ratio, poss])
    clf = LogisticRegression(max_iter=500)
    clf.fit(X, y)
    acc = clf.score(X, y)
    print(f"Synthetic training accuracy: {acc:.3f}")

    return {
        "intercept": round(float(clf.intercept_[0]), 6),
        "coef_diff": round(float(clf.coef_[0][0]), 6),
        "coef_time": round(float(clf.coef_[0][1]), 6),
        "coef_poss": round(float(clf.coef_[0][2]), 6),
        "regulation_seconds": 2880,
        "training_source": "synthetic",
        "training_samples": n,
    }


def _train_real(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Train on real resolved market data."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    X, y = _build_features(rows)
    n = len(y)
    n_pos = int(y.sum())
    n_neg = n - n_pos
    print(f"Training on {n} real samples ({n_pos} YES, {n_neg} NO)...")

    if n < 20:
        print("Too few samples for real training. Falling back to synthetic.")
        return _train_synthetic()

    clf = LogisticRegression(max_iter=500)
    clf.fit(X, y)

    # Cross-validation if enough data
    if n >= 50:
        k = min(5, n // 10)
        scores = cross_val_score(clf, X, y, cv=k, scoring="accuracy")
        print(f"Cross-val accuracy ({k}-fold): {scores.mean():.3f} ± {scores.std():.3f}")

        # Brier score
        from sklearn.metrics import brier_score_loss
        probs = clf.predict_proba(X)[:, 1]
        brier = brier_score_loss(y, probs)
        print(f"Training Brier score: {brier:.4f}")

    acc = clf.score(X, y)
    print(f"Training accuracy: {acc:.3f}")

    return {
        "intercept": round(float(clf.intercept_[0]), 6),
        "coef_diff": round(float(clf.coef_[0][0]), 6),
        "coef_time": round(float(clf.coef_[0][1]), 6),
        "coef_poss": round(float(clf.coef_[0][2]), 6),
        "regulation_seconds": 2880,
        "training_source": "real",
        "training_samples": n,
        "training_accuracy": round(acc, 4),
    }


async def _amain(args: argparse.Namespace) -> None:
    db_url = args.database_url or settings.database_url

    if args.synthetic:
        model = _train_synthetic(args.samples)
    else:
        # Try real data first
        print("Fetching resolved recommendation data...")
        rows = await _fetch_training_data(db_url)
        if not rows:
            print("No resolved recommendations. Trying fair_values fallback...")
            rows = await _fetch_fair_value_data(db_url)

        if rows and len(rows) >= 20:
            model = _train_real(rows)
        else:
            print(f"Only {len(rows)} samples found (need ≥20). Using synthetic fallback.")
            model = _train_synthetic(args.samples)

    # Write output
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=2))
    print(f"\nWrote model to {out}")
    print(json.dumps(model, indent=2))

    # Also update default_model.json if requested
    if args.update_default:
        default_path = Path(__file__).parent / "default_model.json"
        default_path.write_text(json.dumps(model, indent=2))
        print(f"Updated default model at {default_path}")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Train logistic win-probability model from resolved market data."
    )
    p.add_argument("--database-url", default=None)
    p.add_argument("--out", type=Path, default=Path("artifacts/win_prob_logit.json"))
    p.add_argument("--samples", type=int, default=8000, help="Synthetic sample count (fallback)")
    p.add_argument(
        "--synthetic", action="store_true",
        help="Force synthetic training (skip DB lookup)",
    )
    p.add_argument(
        "--update-default", action="store_true",
        help="Also overwrite reasoning/default_model.json",
    )
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
