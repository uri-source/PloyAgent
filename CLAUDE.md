# CLAUDE.md — Polymarket Edge Agent (v0)

## Project Overview

Multi-sport Polymarket edge-detection agent. Five decoupled async Python services ingest live
market data, enrich with game state (NBA, MLB, NFL, NHL, WNBA), compute fair value via
statistical models + optional LLM confidence, and surface ranked recommendations via Slack
with human approve/reject buttons. **No automated trading — recommendations only.**
All data lives in TimescaleDB.

**Current state:** Fully operational. Pipeline running, Slack notifications live, 7 strategies
active, real-time SSE dashboard with P&L tracking and calibration curves.

---

## Architecture

```
Polymarket WS/REST ──► ingestion   ──► prices, order_book_snapshots
ESPN / Odds API    ──► enrichment  ──► game_state
                       reasoning   ──► fair_values        (parallel eval, 8 markets concurrently)
                       notifier    ──► recommendations     (5s tick, smart dedup, alert filters)
                       web (FastAPI)◄── reads all tables   (dashboard + SSE at :8765)
                       slack-events ◄── Slack buttons      (approve/reject at :8766)
```

All services are independent Python processes sharing a TimescaleDB instance. They communicate
only through the database — no message broker, no shared memory.

### Service entry points

| Command | Module | Role |
|---|---|---|
| `ploy-ingest` | `ingestion/__main__.py` | Polymarket WS listener + REST backfill |
| `ploy-enrich` | `enrichment/__main__.py` | Multi-sport game state poller (ESPN/Odds API) |
| `ploy-reason` | `reasoning/__main__.py` | Win-prob model + confidence (parallel, sem=8) |
| `ploy-notify` | `notifier/__main__.py` | Composite scorer, Slack posting, P&L resolution |
| `ploy-web` | `web/app.py` | FastAPI dashboard + SSE + accuracy/calibration APIs |
| `ploy-slack-events` | `notifier/slack_events.py` | Slack button click listener (port 8766) |
| `ploy-migrate` | `db/migrate.py` | Run all SQL migrations in order |
| `ploy-train-model` | `reasoning/train_model.py` | Retrain logistic regression from DB data |
| `ploy-backtest` | `backtest/__main__.py` | Full backtest: Brier, calibration, P&L sim |

---

## Quick Start

### Option A: Docker Compose (recommended)
```bash
cp .env.example .env   # Edit: add SLACK_BOT_TOKEN, SLACK_CHANNEL
docker compose up -d   # Starts TimescaleDB + all 7 services
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
pytest                          # all 54 tests
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
| `AGENT_STRATEGIES` | `baseline_model` | Comma-separated; `consensus` must be last |
| `SPORTS_PROVIDER` | `espn` | `espn` (free) or `odds` (needs `ODDS_API_KEY`) |
| `ENRICHMENT_ESPN_LEAGUES` | `nba` | Multi-sport: `nba,mlb,nfl,nhl,wnba` |
| `POLY_GAMMA_TAGS` | _(empty)_ | Comma-separated Gamma tags for market discovery |
| `MIN_EDGE_CENTS` | `3.0` | Floor — covers 2% fee + spread |
| `RANK_TOP_N` | `5` | Recommendations persisted per notifier tick |
| `SLACK_BOT_TOKEN` | _(empty)_ | Required for Slack alerts |
| `SLACK_CHANNEL` | _(empty)_ | Channel ID to post picks to |
| `ALERT_MIN_EDGE` | `0` | Min edge for Slack alerts (0 = use MIN_EDGE_CENTS) |
| `ALERT_MIN_DEPTH` | `0` | Min depth_1c for alerts |
| `ALERT_MIN_SCORE` | `0` | Min composite score for alerts |

---

## Data Model

```
markets               — market metadata (one row per Polymarket market)
prices                — L2 book ticks (hypertable on ts)
order_book_snapshots  — full book snapshots every 30s + per-trade (hypertable on ts)
game_state            — multi-sport game state polled every 10s (hypertable on ts)
fair_values           — model output per reasoning tick (hypertable on ts)
recommendations       — ranked picks (status: pending/approved/rejected, P&L tracking)
market_game_map       — joins market_id → game_id (used by enrichment)
market_resolution_cache — LLM resolution safety gate cache
```

**Hypertables** (TimescaleDB partitioned by time): `prices`, `order_book_snapshots`,
`game_state`, `fair_values`. Always include a `ts DESC` index when querying these.

Migrations live in `src/ploy_agent/db/migrations/` numbered sequentially (`001_`, `002_`, ...).
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
| `cross_market_arb` | — | Flags complementary markets that don't sum to 1.0 (binary + multi-outcome) |
| `behavior_fade` | — | Fades overreaction moves (price spike > threshold in short window) |
| `player_adjust` | — | Adjusts probability for key player foul/injury signals |
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
score = abs(edge_cents) * log(1 + depth_1c) * confidence * time_factor
time_factor = 1 / (1 + hours_to_resolution)
```
Implementation: `src/ploy_agent/common/scoring.py`. Do not inline this formula elsewhere.

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
- **Smart dedup** — 15-min cooldown per market, but re-alerts if edge doubles
- **Alert filters** — `ALERT_MIN_EDGE`, `ALERT_MIN_DEPTH`, `ALERT_MIN_SCORE` drop weak picks
- **P&L resolution** — detects closed markets, computes hypothetical profit/loss
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
- Current ranked edges (top picks)
- Price ticks, game state, fair values, recommendation history
- **Real-time SSE** (`/events`) — live feed of price ticks, signals, recommendations
- **P&L tracking** (`/api/pnl`) — cumulative profit/loss, per-strategy breakdown
- **Accuracy** (`/api/accuracy`) — per-strategy Brier scores vs resolved outcomes
- **Calibration** (`/api/calibration`) — predicted vs actual bucketed curve

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
docker compose down      # Stop everything (data persists in volume)
```

The `docker-compose.yml` at project root includes:
- `timescaledb` with healthcheck
- `migrate` (runs once, then exits)
- `ingest`, `enrich`, `reason`, `notify`, `web`, `slack-events` (restart: unless-stopped)

All services use `DATABASE_URL=postgresql://postgres:postgres@timescaledb:5432/ploy_agent`
internally. The `.env` file is shared via `env_file`.

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
  common/         config, db pool, logging, scoring, statistical confidence
  ingestion/      Polymarket WS + REST, L2 book management
  enrichment/     ESPN / Odds API multi-sport data, market→game mapping
  reasoning/      win-prob model, Claude confidence, resolution gate, trainer
  strategies/     Strategy ABC + 7 implementations + registry
  notifier/       composite ranking, Slack posting, P&L resolution, smart dedup
  web/            FastAPI dashboard + SSE + accuracy/calibration APIs
  backtest/       full historical accuracy harness
  db/             migration runner + SQL files
scripts/          start_all.sh, start_all.ps1
tests/            54 unit tests (model, confidence, book_math, resolution, scoring, etc.)
```

---

## Testing Guidelines

- Unit tests only — no DB, no network, no external APIs
- Test pure functions: scoring math, model predictions, odds math, resolution heuristics
- For async service code, mock `asyncpg.Connection` and `httpx.AsyncClient`
- Test file per module: `tests/test_<module>.py`
- Run `pytest` before every PR — 54 tests, all must pass
- Key test files: `test_model.py` (14), `test_confidence.py` (6), `test_book_math.py` (11),
  `test_resolution.py` (8), `test_scoring.py` (4), `test_registry.py` (3)

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
