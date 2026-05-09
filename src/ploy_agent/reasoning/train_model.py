from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression


def main() -> None:
    p = argparse.ArgumentParser(description="Train logistic win model matching runtime features.")
    p.add_argument("--out", type=Path, default=Path("artifacts/win_prob_logit.json"))
    p.add_argument("--samples", type=int, default=8000)
    args = p.parse_args()

    rng = np.random.default_rng(0)
    n = args.samples
    home_score = rng.integers(80, 130, size=n)
    away_score = rng.integers(80, 130, size=n)
    diff = home_score - away_score
    total = np.maximum(1, home_score + away_score)
    diff_term = diff.astype(float) / total.astype(float)
    remaining_ratio = rng.uniform(0.0, 1.0, size=n)
    poss = rng.choice([-1.0, 0.0, 1.0], size=n)
    z = 0.08 * diff_term - 1.2 * remaining_ratio + 0.15 * poss + rng.normal(0, 0.25, size=n)
    y = (z > 0).astype(int)

    X = np.column_stack([diff_term, remaining_ratio, poss])
    clf = LogisticRegression(max_iter=300)
    clf.fit(X, y)

    out = {
        "intercept": float(clf.intercept_[0]),
        "coef_diff": float(clf.coef_[0][0]),
        "coef_time": float(clf.coef_[0][1]),
        "coef_poss": float(clf.coef_[0][2]),
        "regulation_seconds": 2880,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
