# Strategy Profitability Improvement — Design Spec

**Date:** 2026-05-25
**Status:** Approved
**Goal:** Flip the system from -6421c to profitable using data-driven filters (Phase 1), then improve signal quality (Phase 2).

---

## Problem Statement

Sim data from 1662 closed trades shows -6421c total P&L despite reasonable win rates. Root causes:

1. **Extreme entry prices** — BUY at 0.60-0.80 loses -5747c, SELL at 0.20-0.40 loses -5114c. Risk/reward is lopsided at extreme prices (e.g., BUY at 0.70: win pays 30c, loss costs 70c).
2. **`book_imbalance` BUY is catastrophic** — 513 trades at avg entry 0.705, -6489c. 37% win rate when 70%+ is needed to break even.
3. **`cross_market_arb` is fundamentally broken** — 91 trades, 12% win rate, -725c. Entry price cap doesn't help (85/91 trades already in 0.35-0.65 range).
4. **Small edges are noise** — 3-5c edge bucket loses the most (-4242c across 415 trades).

## Backtested Scenarios

| Scenario | Trades | P&L | Avg P&L/trade |
|---|---|---|---|
| Current (no filters) | 1662 | -6421c | -3.86c |
| Entry 0.35-0.65 | 660 | +6462c | +9.79c |
| Entry 0.35-0.65 + no arb | 575 | +7179c | +12.49c |
| Entry 0.40-0.60 | 521 | +5904c | +11.33c |
| Entry 0.30-0.70 | 942 | +2435c | +2.59c |

---

## Phase 1: Math Guardrails (implement first)

### 1.1 Entry Price Cap

**What:** Add `ENTRY_PRICE_MIN` (default 0.35) and `ENTRY_PRICE_MAX` (default 0.65) to Settings.

**Where enforced:**
- `notifier/__main__.py` — skip picks where `market_prob` is outside the range before calling `insert_recommendation`
- `sim/portfolio.py` — reject signals outside the range before opening positions
- `notifier/rank.py` — filter picks before returning from `top_picks()`

**Why 0.35-0.65:** Highest total P&L (+6462c) and near-best per-trade (+9.79c). Tighter than 0.40-0.60 would cut too much volume for marginal improvement.

### 1.2 Disable `cross_market_arb`

**What:** Remove `cross_market_arb` from `AGENT_STRATEGIES` in `.env` and `docker-compose.yml`.

**Why:** 12% win rate at every entry price. Entry price cap doesn't fix it — the arb detection finds false positives from stale/illiquid complement markets. Code stays in the repo, just not enabled.

### 1.3 Minimum Risk-Reward Ratio Gate

**What:** Add `MIN_RISK_REWARD` setting (default 0.30). Reject any trade where `risk_reward_factor(market_mid, edge_cents) < MIN_RISK_REWARD`.

**Where enforced:**
- `notifier/__main__.py` — filter picks after scoring
- `sim/portfolio.py` — reject signals that fail the gate

**Why:** The existing `risk_reward_factor` penalizes the composite score but doesn't hard-block. Trades at 0.64 entry with small edge still slip through. A hard floor at 0.30 catches the worst cases.

### 1.4 Raise Minimum Edge

**What:** Change `MIN_EDGE_CENTS` default from 3.0 to 5.0.

**Why:** 3-5c edge bucket loses -4242c across 415 trades. The 5-10c bucket is the volume sweet spot (1160 trades) and loses much less per trade. Raising the floor eliminates the noisiest signals.

### Phase 1 Files Modified

| File | Change |
|---|---|
| `common/config.py` | Add `ENTRY_PRICE_MIN`, `ENTRY_PRICE_MAX`, `MIN_RISK_REWARD`; change `MIN_EDGE_CENTS` default to 5.0 |
| `common/scoring.py` | Add `passes_risk_reward_gate(market_mid, edge_cents, min_rr)` helper |
| `notifier/__main__.py` | Filter picks by entry price range and risk-reward gate |
| `notifier/rank.py` | Filter picks by entry price range in `top_picks()` |
| `sim/portfolio.py` | Add entry price + risk-reward gates to `process_signal()` |
| `.env` | Remove `cross_market_arb` from `AGENT_STRATEGIES` |
| `docker-compose.yml` | Update `AGENT_STRATEGIES` value |

---

## Phase 2: Signal Quality (implement after Phase 1 runs for a few days)

### 2.1 Fix `book_imbalance` BUY Bias

**Problem:** BUY at avg entry 0.705 loses -6489c (37% win rate). SELL works (+823c).

**Fix:** Add directional asymmetry — BUY signals require 2x the imbalance threshold of SELL signals. Buy-side flow at high prices is often retail noise; sell-side flow at low prices more often reflects informed trading.

**File:** `strategies/book_imbalance.py` — add `BUY_IMBALANCE_MULTIPLIER = 2.0` constant, apply in `run()`.

### 2.2 Get `baseline_model` Generating Trades

**Problem:** Logistic model outputs probabilities close to market mid (small edges), rarely passes threshold gates.

**Fix:** Retrain with `ploy-train-model --update-default` once 50+ resolved markets exist with game state data. Until then, the entry price cap and lower MIN_EDGE help more signals through.

### 2.3 Wire Up Strategy Auto-Disable

**Problem:** Broken strategies accumulate losses over time.

**Fix:** `common/auto_disable.py` exists but isn't connected. Wire it into the notifier: if a strategy's last 20 resolved trades have <30% win rate, disable for 24h. Log warning. Re-enable and re-evaluate.

**Files:** `notifier/__main__.py` — check auto-disable before processing picks; `common/auto_disable.py` — ensure `check_strategy_health()` works with current schema.

### 2.4 Post-Resolution Feedback Loop

**Problem:** No learning from past mistakes.

**Fix:** After resolution, log (strategy_id, market_id, correct: bool). Over time, build a per-strategy rolling accuracy score. Weight composite scores by strategy accuracy: consistently-right strategies get boosted, consistently-wrong ones get dampened.

**Files:** `notifier/__main__.py` — log accuracy after `_resolve_pnl()`; new `common/strategy_accuracy.py` — rolling accuracy tracker.

---

## Success Criteria

- **Phase 1:** Re-run `ploy-sim replay --days 14` and confirm positive total P&L across all profiles
- **Phase 2:** Forward sim shows >50% win rate and positive P&L over 7+ days

## Non-Goals

- Changing the logistic regression model architecture
- Adding new strategies (focus on making existing ones work)
- Automated trading execution (remains recommendation-only)
