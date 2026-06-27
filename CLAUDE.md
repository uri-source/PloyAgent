# CLAUDE.md — Polymarket Edge Agent (v0)

## Project Overview

Multi-sport Polymarket edge-detection agent. Default stack is **price-only**: Polymarket +
optional **Kalshi** cross-venue arb (World Cup pairs). ESPN/Odds enrichment is **optional**
(`ENRICHMENT_ENABLED=false` by default). Surfaces ranked recommendations and paper-trading sim.
**No automated trading — recommendations only.**

**Default stack:** Polymarket + Kalshi cross-venue arb (no ESPN). See [docs/cross-venue-world-cup.md](docs/cross-venue-world-cup.md).

---

## Architecture

```
Polymarket WS/REST ──► ingestion        ──► prices, order_book_snapshots
Kalshi REST poll   ──► kalshi-ingest    ──► kalshi_prices (curated pairs)
ESPN / Odds API    ──► enrichment (opt)  ──► game_state
                       reasoning         ──► fair_values
                       notifier          ──► recommendations + paper sim feed
                       sim-forward       ──► sim_trades
                       web (FastAPI)     ◄── dashboard /paper / analytics
```

All services are independent Python processes sharing a TimescaleDB instance. They communicate
only through the database — no message broker, no shared memory.

### Service entry points

| Command | Module | Role |
|---|---|---|
| `ploy-ingest` | `ingestion/__main__.py` | Polymarket WS listener + REST backfill |
| `ploy-kalshi-ingest` | `kalshi/__main__.py` | Kalshi REST poll for curated cross-venue pairs |
| `ploy-kalshi` | `kalshi/cli.py` | `load-pairs` YAML → `cross_venue_pairs` |
| `ploy-enrich` | `enrichment/__main__.py` | Optional ESPN/Odds game state (`ENRICHMENT_ENABLED`) |
| `ploy-reason` | `reasoning/__main__.py` | Win-prob model + confidence (parallel, sem=8) |
| `ploy-notify` | `notifier/__main__.py` | Composite scorer, Slack/Telegram posting, P&L resolution |
| `ploy-web` | `web/app.py` | FastAPI dashboard + SSE + analytics/accuracy/calibration APIs |
| `ploy-slack-events` | `notifier/slack_events.py` | Slack button click listener (port 8766) |
| `ploy-sim` | `sim/__main__.py` | Paper-trading simulation across threshold profiles |
| `ploy-migrate` | `db/migrate.py` | Run all SQL migrations in order |
| `ploy-train-model` | `reasoning/train_model.py` | Retrain logistic regression from DB data |
| `ploy-backtest` | `backtest/__main__.py` | Full backtest: Brier, calibration, P&L sim |

---

## Quick Start

### Option A: Docker Compose (recommended)
```bash
cp .env.example .env   # Edit: add SLACK_BOT_TOKEN, SLACK_CHANNEL
docker compose up -d   # Starts TimescaleDB + all 8 services
# Dashboard: http://localhost:8765
```

### Option B: Local development
```bash
# 1. Start TimescaleDB
docker compose -f infra/docker-compose.yml up -d

# 2. Install (editable, with dev deps)
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 3. Configure
cp .env.example .env
# Edit .env — minimum: SLACK_BOT_TOKEN + SLACK_CHANNEL for alerts

# 4. Run migrations
ploy-migrate

# 5. Start all services
bash scripts/start_all.sh          # Linux/Mac
# or: powershell scripts/start_all.ps1  # Windows
# or manually in separate terminals:
ploy-ingest && ploy-enrich && ploy-reason && ploy-notify && ploy-web
```

### Python version
**3.11+** required. The codebase uses `from __future__ import annotations` on every module.

### Linting
```bash
ruff check src/ tests/
ruff format src/ tests/
```
Line length: **100**. Target: **py311**.

### Tests
```bash
pytest                          # all tests
pytest tests/test_model.py     # single file
```
`asyncio_mode = "auto"` — all async tests just work with `async def test_*`.
Tests must not touch the database or external APIs. Pure-unit only.

---

## Configuration (Settings)

All config lives in `src/ploy_agent/common/config.py` as a `pydantic-settings` `BaseSettings`
class. Every field maps 1:1 to an env var. Access via the singleton:

```python
from ploy_agent.common.config import settings
```

**Never** import `os.environ` directly — always go through `settings`.

### Key env vars

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://...localhost:5432/ploy_agent` | Port 5433 for local Docker |
| `ANTHROPIC_API_KEY` | _(empty)_ | Falls back to statistical confidence if unset |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Do not downgrade below Sonnet |
| `AGENT_STRATEGIES` | `cross_venue_arb,cross_market_arb,book_imbalance` | Default price-only; `consensus` last if enabled |
| `ENRICHMENT_ENABLED` | `false` | Set `true` + docker compose `--profile sports` for ESPN |
| `KALSHI_ENABLED` | `true` | Kalshi ingest + `cross_venue_arb` |
| `CROSS_VENUE_MIN_EDGE_CENTS` | `8` | Min fee-adjusted gap for cross-venue signals |
| `CROSS_VENUE_PAIRS_PATH` | `config/cross_venue/world_cup_pairs.yaml` | Curated Poly ↔ Kalshi pairs |
| `ENRICHMENT_ESPN_LEAGUES` | `nba` | Multi-sport: `nba,mlb,nfl,nhl,wnba` |
| `POLY_GAMMA_TAGS` | _(empty)_ | Comma-separated Gamma tags for market discovery |
| `MIN_EDGE_CENTS` | `3.0` | Floor — covers 2% fee + spread |
| `RANK_TOP_N` | `5` | Recommendations persisted per notifier tick |
| `AUTO_APPROVE_RECS` | `true` | Skip human approval; entry_price set at signal time |
| `SLACK_BOT_TOKEN` | _(empty)_ | Required for Slack alerts |
| `SLACK_CHANNEL` | _(empty)_ | Channel ID to post picks to |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Required for Telegram alerts |
| `TELEGRAM_CHAT_ID` | _(empty)_ | Telegram chat to post picks to |
| `ALERT_MIN_EDGE` | `0` | Min edge for alerts (0 = use MIN_EDGE_CENTS) |
| `ALERT_MIN_DEPTH` | `0` | Min depth_1c for alerts |
| `ALERT_MIN_SCORE` | `0` | Min composite score for alerts |
| `SIM_FORWARD_RUN_HOURS` | `336` | Forward sim auto-stop (0 = unlimited) |
| `SIM_FORWARD_INTERVAL_SEC` | `5` | Forward loop tick interval |

---

## Data Model

Seven primary tables + supporting tables in TimescaleDB.

```
markets               — market metadata (one row per Polymarket market)
prices                — L2 book ticks (hypertable on ts)
order_book_snapshots  — full book snapshots every 30s + per-trade (hypertable on ts)
game_state            — multi-sport game state polled every 10s (hypertable on ts)
fair_values           — model output per reasoning tick (hypertable on ts)
recommendations       — ranked picks (status: pending/approved/rejected, P&L tracking)
sim_trades            — paper-trading positions per sim profile
kalshi_markets        — Kalshi ticker metadata
kalshi_prices         — Kalshi quotes (hypertable on ts)
cross_venue_pairs     — curated Polymarket market_id ↔ Kalshi ticker
sim_profiles          — threshold configurations for paper trading
sim_runs              — replay/forward run metadata
market_game_map       — joins market_id → game_id (used by enrichment)
market_resolution_cache — LLM resolution safety gate cache
```

**Hypertables** (TimescaleDB partitioned by time): `prices`, `order_book_snapshots`,
`game_state`, `fair_values`. Always include a `ts DESC` index when querying these.

Migrations live in `src/ploy_agent/db/migrations/` numbered sequentially (`001_` through `006_`).
**Always add new columns or tables via a new numbered migration file.** Never alter existing
migration files — `ploy-migrate` is idempotent but detects conflicts.

---

## Strategy System

Strategies are the core signal layer. Each is a class implementing `Strategy` (ABC):

```python
class Strategy(ABC):
    id: ClassVar[str]               # used in AGENT_STRATEGIES env var
    requires: ClassVar[frozenset]   # e.g. frozenset({"odds_api"})

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        ...
```

`StrategyContext` carries: `conn`, `market_id`, `mrow` (market record), `mid` (current price),
`game_state` dict, `model` (trained logistic params), `http` (shared AsyncClient),
`depth_1c`, `spread`.

`StrategyResult` must populate: `model_prob`, `market_prob`, `edge_cents`, `confidence`,
`reasoning`, and optionally `sources` and `signal_json`.

### Available strategies

| ID | Requires | Description |
|---|---|---|
| `baseline_model` | — | Logistic regression on score diff + time remaining + possession |
| `stale_quote` | — | Detects quotes that haven't moved despite game state change |
| `sportsbook_consensus` | `odds_api` | Devigged sharp-book lines as fair value |
| `cross_market_arb` | — | Intra-Polymarket complementary markets (binary + multi-outcome) |
| `cross_venue_arb` | `kalshi` | Polymarket vs Kalshi fee-adjusted arb (curated YAML pairs) |
| `book_imbalance` | — | Detects directional order flow imbalance in the L2 book |
| `consensus` | — | Ensemble: boosts signal when 2+ strategies agree on direction (**must be last**) |

**To add a strategy:** create `src/ploy_agent/strategies/my_strategy.py`, implement `Strategy`,
add to `STRATEGIES` dict in `registry.py`, add to `AGENT_STRATEGIES` env var.

### Consensus strategy details
Reads recent `fair_values` from other strategies for the same market. If 2+ agree on direction
(all BUY or all SELL), emits a confidence-weighted composite signal with a 1.25× boost. Must
run last in the strategy list so other strategies have already written their fair_values.

---

## Core Business Logic

### Edge calculation
```python
edge_cents = (model_prob - market_mid) * 100
# BUY when edge > 0 (model thinks YES underpriced)
# SELL when edge < 0 (model thinks YES overpriced)
# Discard if abs(edge_cents) < MIN_EDGE_CENTS (default 3¢)
```

### Composite score (ranking)
```python
score = abs(edge_cents) * log(1 + depth_1c) * confidence * time_factor * risk_reward
time_factor = 1 / (1 + hours_to_resolution)
risk_reward = sqrt(win_payout / loss_payout), clamped [0.15, 1.0]
```
Implementation: `src/ploy_agent/common/scoring.py`. Do not inline this formula elsewhere.

The **risk-reward factor** penalizes trades with asymmetric downside. For a BUY at 0.85,
win pays 15¢ but loss costs 85¢ — risk_reward = sqrt(15/85) ≈ 0.42, heavily discounting
the score even if edge is positive. This prevents the system from recommending high-confidence
but lopsided trades.

### Fair value decay
`common/fair_value_decay.py` — stale signals lose strength over time. A fair value computed
30 minutes ago is worth less than a fresh one. The decay factor multiplies `edge_cents` before
scoring. Used in `notifier/rank.py` when selecting top picks.

### Kelly fraction
`common/kelly.py` — display-only Kelly sizing for position sizing context. Shows what fraction
of bankroll a Kelly bettor would allocate. Not used for execution (no trading), but visible
in the dashboard for educational value.

### Adaptive edge threshold
`common/adaptive_edge.py` — dynamically adjusts the minimum edge threshold based on recent
recommendation accuracy. If the system is performing well, the threshold tightens; if poorly,
it loosens. Used as a floor for alert filtering in the notifier.

### P&L tracking
Binary market P&L computed in `common/pnl.py`:
- **BUY** at price p: win (outcome=1) pays `(1-p)*100¢`, loss pays `-p*100¢`
- **SELL** at price p: win (outcome=0) pays `p*100¢`, loss pays `-(1-p)*100¢`
- **Mark-to-market** (non-resolution close): `(exit - entry) * 100` for BUY, inverse for SELL

Resolution triggers in `notifier/__main__.py::_resolve_pnl()`:
1. Market status is `'closed'` in DB
2. Market `end_date` is past AND final price is near 0 or 1 (>0.9 or <0.1)

Entry price: set from `payload.market_prob` at signal generation time (NOT latest market price).

### Recommendation deduplication
Each (market_id, strategy_id) is ONE position. The notifier checks for ANY unresolved
recommendation before creating a new row. Dashboard queries also deduplicate — each position
counted once for P&L, win rate, and capital deployed calculations.

### Statistical confidence (no-LLM mode)
`common/confidence.py` — weighted 5-factor score:
- Liquidity (depth_1c) — 30%
- Spread tightness — 15%
- Sibling market corroboration — 25%
- Edge magnitude — 20%
- Mid position (extremes penalized) — 10%

Used by `cross_market_arb`, `stale_quote`, and as Claude fallback when no API key.

### Resolution risk gate
Before scoring any market, `resolution_gate()` in `reasoning/resolution.py` runs:
1. Heuristic regex scan for ambiguous keywords
2. If API key present: LLM classification (cached Anthropic client, max 200 tokens)
3. Result cached in `market_resolution_cache`

If `safe=False`, the market is **silently dropped** (logged, never recommended).

### LLM role (Claude)
Claude does **not** compute game probabilities. The logistic regression model does that.
Claude's only job is:
- `confidence` (0–1): data staleness, mapping risk, microstructure uncertainty
- `reasoning`: human-readable narrative for the recommendation
- `sources`: list of `{type, detail}` dicts

Prompt in `reasoning/claude_confidence.py`. Max tokens: 400. Structured JSON output.
If `ANTHROPIC_API_KEY` is empty, falls back to `statistical_confidence()`.

### Win probability model
`reasoning/model.py` — logistic regression with 3 features:
- `coef_diff` × raw score difference (home - away)
- `coef_time` × remaining time ratio
- `coef_poss` × possession indicator (+1 home, -1 away, 0 neutral)

Coefficients stored in `reasoning/default_model.json`. Retrain with `ploy-train-model`.

### Notifier behavior
- **5-second tick** — near real-time notifications
- **Position-level dedup** — one recommendation per (market, strategy) until resolved
- **Re-alert on edge doubling** — if edge doubles and exceeds 2× MIN_EDGE, re-notify
- **Alert filters** — `ALERT_MIN_EDGE`, `ALERT_MIN_DEPTH`, `ALERT_MIN_SCORE` drop weak picks
- **P&L resolution** — detects closed/expired markets, computes hypothetical profit/loss
- **Slack thread replies** — posts outcome + P&L as thread reply to original alert

### WebSocket reconnect
`ingestion/ws_market.py` maintains a `LocalBook` (in-memory L2 book per asset). On disconnect,
exponential backoff then full-book re-subscribe. The book is rebuilt from the next `book` event —
no state is persisted in memory across reconnects.

---

## Slack Integration

**Setup:**
1. Create a Slack app at https://api.slack.com/apps
2. Bot scopes needed: `chat:write`, `chat:update`
3. Set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` in `.env`
4. Set Interactivity Request URL to `http://<host>:8766/slack/interactions`
5. Run `ploy-slack-events` alongside other services

**Flow:**
1. Notifier posts top picks with Approve/Reject buttons
2. Human clicks a button in Slack
3. `slack_events.py` receives the click, updates `recommendations.status`
4. When market resolves, a thread reply shows outcome + P&L

---

## Dashboard & APIs

The web dashboard at `:8765` includes:
- Pipeline status (service health from DB freshness)
- Current ranked edges (top picks with Kelly fraction, decay, risk-reward)
- Price ticks, game state, fair values, recommendation history
- **Real-time SSE** (`/events`) — live feed of price ticks, signals, recommendations
- **P&L tracking** (`/api/pnl`) — deduplicated cumulative P&L, per-strategy breakdown
- **Analytics** (`/api/analytics`) — every trade with capital deployed, ROI%, closed deals list,
  breakdowns by strategy/category/market-type, streak tracking
- **Accuracy** (`/api/accuracy`) — per-strategy Brier scores vs resolved outcomes
- **Calibration** (`/api/calibration`) — predicted vs actual bucketed curve
- **Simulation** (`/api/sim/*`) — paper-trading P&L by threshold profile, per-market best fit

---

## Paper-trading simulation (`ploy-sim`)

One `ploy-reason` pipeline writes `fair_values`; many **sim profiles** (edge × confidence ×
model_prob thresholds) read the same data — do not run separate ingest/reason stacks per
threshold. The `sim-forward` service runs as a Docker container alongside other services.

### Setup
```bash
ploy-migrate                    # includes 005_simulation.sql
ploy-sim init-profiles          # 36 profiles (use --subset for 12)
ploy-sim replay --days 14       # historical walk → sim_trades
ploy-sim compare                # rank profiles by P&L
ploy-sim report --profile e5_c65_m60 --by market
ploy-sim forward                # live paper trading (alongside ploy-reason)
```

### Threshold gates (per profile)
- **BUY:** `edge_cents >= min_edge`, `confidence >= min_confidence`, `model_prob >= min_model_prob`
- **SELL:** `edge_cents <= -min_edge`, same confidence, `(1 - model_prob) >= min_model_prob`

### Trade close reasons
- **resolution** — market resolved (outcome known), binary P&L computed
- **signal_reverse** — opposing signal detected, mark-to-market P&L
- **forward_shutdown** — service stopped, positions closed at final mid price
- **mark_to_market** — replay end, positions closed at current price

All close types compute P&L: resolution uses binary payoff, others use mark-to-market
`(exit_price - entry_price) * 100` for BUY, inverse for SELL.

### How long to run (defaults)
| Mode | Default | Env | Success criteria |
|------|---------|-----|------------------|
| **Forward** | **14 days** then auto-stop | `SIM_FORWARD_RUN_HOURS=336` (`0` = unlimited) | ≥50 closed trades on top profile, or full window |
| **Replay** | **14-day** lookback | `SIM_REPLAY_DAYS=14` | ≥30 closed trades/profile (smoke); ≥100 for ranking |
| **Charts** | — | — | ≥20 closed trades on a profile |

Shorter smoke test: `ploy-sim forward --hours 48` or `ploy-sim replay --days 7`.

### Dashboard
Open **Simulation (paper trading)** on the web UI — defaults to **current forward run** only.
**Tracker:** `GET /api/sim/tracker` (forward run status, BUY/SELL counts, recent trades).
Summary/trades/series: `/api/sim/summary`, `/api/sim/series?profile_id=...` (optional `sim_run_id`
or `all_runs=true` for replay history). **Analytics:** `/analytics` and `/api/analytics/*`.

---

## Model Retraining

```bash
# Train from real resolved market data (falls back to synthetic if <20 samples)
ploy-train-model --update-default

# Force synthetic training
ploy-train-model --synthetic --out artifacts/model.json

# Full backtest report
ploy-backtest --full --min-edge 3.0
```

The trainer fetches resolved recommendations + game state from DB, fits logistic regression
with cross-validation, and reports Brier score. Use `--update-default` to overwrite
`reasoning/default_model.json` (takes effect on next reasoning restart).

---

## Deployment (Docker)

```bash
docker compose up -d     # All services + TimescaleDB
docker compose logs -f   # Tail all logs
docker compose down      # Stop everything (data persists in tsdata volume)
# NEVER use -v flag — it deletes the data volume
```

The `docker-compose.yml` at project root includes:
- `timescaledb` with healthcheck + `tsdata` named volume
- `migrate` (runs once, then exits)
- `ingest`, `enrich`, `reason`, `notify`, `web`, `slack-events`, `sim-forward`

All services use `DATABASE_URL=postgresql://postgres:postgres@timescaledb:5432/ploy_agent`
internally. The `.env` file is shared via `env_file`.

**Common Docker issues:**
- Container name conflicts: `docker rm -f polyagent-timescaledb-1` then retry
- Build after code changes: `docker compose up -d --build`

### AWS production (EC2 + ALB + Cognito)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml up -d
```

Scripts: `scripts/aws-bootstrap.sh`, `scripts/aws-ssm-pull-env.sh`, `scripts/aws-deploy.sh`, `scripts/aws-verify.sh`.

Full checklist: [docs/aws-hosting.md](docs/aws-hosting.md).

---

## Polymarket API Reference

All public, no auth required for read.

| API | URL | Used for |
|---|---|---|
| Gamma REST | `https://gamma-api.polymarket.com` | Market discovery, multi-sport filtering |
| CLOB REST | `https://clob.polymarket.com` | Order books, trade history, market metadata |
| WebSocket | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Live price/trade stream |

**Geo-restriction:** Polymarket blocks US IPs on some endpoints.

---

## Logging

Uses `structlog` with JSON output (set `LOG_JSON=true` for production).

```python
from ploy_agent.common.logging_config import get_logger
log = get_logger("my.service")
log.info("event_name", key=value, ...)  # structured key=value, not f-strings
```

**Convention:** Event names are `snake_case` verbs or noun-verb pairs (`price_tick`,
`ws_reconnect`, `pnl_resolved`). Never log raw PII or full order book objects.

---

## Database Access Pattern

All DB access is `asyncpg`. Connection pooling via `common/db.py` with async lock:

```python
from ploy_agent.common.db import get_pool, close_pool

pool = await get_pool()
async with pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ...")
```

Each service module has its own `repo.py` with typed query functions. Do not write raw SQL
outside of `repo.py` files or migration files.

---

## File Layout

```
src/ploy_agent/
  common/         config, db pool, logging, scoring, pnl, kelly, confidence,
                  adaptive_edge, fair_value_decay, market_type, odds_sports, ssl_utils
  ingestion/      Polymarket WS + REST, L2 book management
  enrichment/     ESPN / Odds API multi-sport data, market→game mapping
  reasoning/      win-prob model, Claude confidence, resolution gate, trainer
  strategies/     Strategy ABC + 8 implementations + registry
  notifier/       composite ranking, Slack/Telegram posting, P&L resolution, dedup
  web/            FastAPI dashboard + SSE + analytics/accuracy/calibration APIs
  backtest/       full historical accuracy harness
  sim/            paper-trading profiles, replay, forward, portfolio, metrics, tracker
  db/             migration runner + 6 SQL migration files
scripts/          start_all.sh, start_all.ps1
tests/            unit tests (model, confidence, book_math, resolution, scoring, etc.)
```

---

## Testing Guidelines

- Unit tests only — no DB, no network, no external APIs
- Test pure functions: scoring math, model predictions, odds math, resolution heuristics
- For async service code, mock `asyncpg.Connection` and `httpx.AsyncClient`
- Test file per module: `tests/test_<module>.py`
- Run `pytest` before every PR — all must pass

---

## Key Invariants (Never Break)

1. **No automated trade execution.** The agent surfaces recommendations only.
2. **LLM does not produce game probabilities.** The statistical model does. Claude only produces
   `confidence`, `reasoning`, and `sources`.
3. **Resolution gate is mandatory.** Every market must pass `resolution_gate()` before being
   scored. Never bypass or cache-skip for "obvious" markets.
4. **Edge threshold is a floor, not a target.** `MIN_EDGE_CENTS=3` covers Polymarket's 2% fee
   plus typical spread. Lowering it below 2 makes recommendations unprofitable on average.
5. **Migrations are append-only.** Edit schema through new files, never by altering old ones.
6. **Consensus strategy runs last.** It reads fair_values from other strategies — ordering matters.
7. **All strategies must check `MIN_EDGE_CENTS`.** Return `None` if `abs(edge) < threshold`.
8. **No `os.environ` access.** All config through `settings` singleton.
9. **One recommendation per position.** Dedup by (market_id, strategy_id) — never create
   duplicate rows for the same open position.
10. **Entry price from signal time.** Use `payload.market_prob`, never the latest market price
    (which may have moved to 0/1 if the market already resolved).
11. **Never `docker compose down -v`.** The `-v` flag deletes the TimescaleDB data volume.
