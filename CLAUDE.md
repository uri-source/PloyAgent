# CLAUDE.md — Polymarket Edge Agent (v0)

## Project Overview

NBA in-game Polymarket edge-detection agent. Four decoupled async Python services ingest live
market data, enrich with game state, compute fair value via statistical models + LLM confidence,
and surface ranked recommendations to a web dashboard. **No automated trading — recommendations
only.** All data lives in TimescaleDB.

**Current state:** Core pipeline (ingest → enrich → reason → rank) is complete and working.
**Missing from PRD:** Slack notification + human approve/reject loop (top priority next work).

---

## Architecture

```
Polymarket WS/REST ──► ingestion   ──► prices, order_book_snapshots
ESPN / Odds API    ──► enrichment  ──► game_state
                       reasoning   ──► fair_values        (triggered by price move >2¢ or 60s)
                       notifier    ──► recommendations     (runs every 60s, writes top N rows)
                       web (FastAPI)◄── reads all tables   (dashboard at :8765)
```

All services are independent Python processes sharing a TimescaleDB instance. They communicate
only through the database — no message broker, no shared memory.

### Service entry points

| Command | Module | Role |
|---|---|---|
| `ploy-ingest` | `ingestion/__main__.py` | Polymarket WS listener + REST backfill |
| `ploy-enrich` | `enrichment/__main__.py` | Game state poller (ESPN or Odds API) |
| `ploy-reason` | `reasoning/__main__.py` | Win-prob model + Claude confidence |
| `ploy-notify` | `notifier/__main__.py` | Composite scorer, writes recommendation rows |
| `ploy-web` | `web/app.py` | FastAPI dashboard (auto-refresh 30s) |
| `ploy-slack-events` | `notifier/slack_events.py` | Slack button click listener (port 8766) |
| `ploy-migrate` | `db/migrate.py` | Run all SQL migrations in order |
| `ploy-train-model` | `reasoning/train_model.py` | Retrain logistic regression |
| `ploy-backtest` | `backtest/__main__.py` | Historical accuracy harness |

---

## Development Setup

```bash
# 1. Start TimescaleDB
docker compose -f infra/docker-compose.yml up -d

# 2. Install (editable, with dev deps)
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 3. Configure
cp .env.example .env
# Edit .env — minimum required: ANTHROPIC_API_KEY

# 4. Run migrations
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ploy_agent
ploy-migrate

# 5. Start services (separate terminals)
ploy-ingest
ploy-enrich
ploy-reason
ploy-notify
ploy-web       # → http://127.0.0.1:8765
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
pytest tests/test_scoring.py   # single file
```
`asyncio_mode = "auto"` — all async tests just work with `async def test_*`.
Tests must not touch the database or external APIs. Use `asyncpg` mocks or pure-unit tests.

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
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/ploy_agent` | |
| `ANTHROPIC_API_KEY` | _(empty)_ | Falls back to neutral confidence (0.55) if unset |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Do not downgrade below Sonnet |
| `AGENT_STRATEGIES` | `baseline_model` | Comma-separated; see strategy list below |
| `SPORTS_PROVIDER` | `espn` | `espn` (free) or `odds` (needs `ODDS_API_KEY`) |
| `ODDS_API_KEY` | _(empty)_ | Required for `sportsbook_consensus` strategy |
| `MIN_EDGE_CENTS` | `3.0` | Discard edges below this (covers 2% fee + spread) |
| `RANK_TOP_N` | `5` | Recommendations persisted per notifier tick |
| `PLOY_INSECURE_SSL` | `false` | Local macOS TLS workaround only — never production |

---

## Data Model

Five primary tables + two supporting tables in TimescaleDB.

```
markets               — market metadata (one row per Polymarket market)
prices                — L2 book ticks (hypertable on ts)
order_book_snapshots  — full book snapshots every 30s + per-trade (hypertable on ts)
game_state            — NBA game state polled every 10s (hypertable on ts)
fair_values           — model output per reasoning tick (hypertable on ts)
recommendations       — ranked picks (status: pending/approved/rejected)
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
`game_state` dict, `model` (trained logistic params), `http` (shared AsyncClient).

`StrategyResult` must populate: `model_prob`, `market_prob`, `edge_cents`, `confidence`,
`reasoning`, and optionally `sources` and `signal_json`.

### Available strategies

| ID | Requires | Description |
|---|---|---|
| `baseline_model` | — | Logistic regression on score diff + time remaining + possession |
| `stale_quote` | — | Detects quotes that haven't moved despite game state change |
| `sportsbook_consensus` | `odds_api` | Devigged sharp-book lines as fair value |
| `cross_market_arb` | — | Flags complementary markets that don't sum to 1.0 |
| `behavior_fade` | — | Fades overreaction moves (price spike > threshold in short window) |
| `player_adjust` | — | Adjusts probability for key player foul/injury signals |

**To add a strategy:** create `src/ploy_agent/strategies/my_strategy.py`, implement `Strategy`,
add to `STRATEGIES` dict in `registry.py`, document in this file.

---

## Core Business Logic

### Edge calculation
```python
edge_cents = (model_prob - market_mid) * 100
# Discard if abs(edge_cents) < MIN_EDGE_CENTS (default 3¢)
```

### Composite score (ranking)
```python
score = edge_cents * log(1 + depth_1c) * confidence * time_factor
time_factor = 1 / (1 + hours_to_resolution)
```
Implementation: `src/ploy_agent/common/scoring.py`. Do not inline this formula elsewhere.

### Resolution risk gate
Before scoring any market, `resolution_gate()` in `reasoning/resolution.py` runs:
1. Heuristic regex scan for ambiguous keywords (`officially`, `according to`, `twitter`, etc.)
2. If API key present: LLM classification (Claude, max 200 tokens, sync)
3. Result cached in `market_resolution_cache`

If `safe=False`, the market is **silently dropped** (logged, never recommended).

### LLM role (Claude)
Claude does **not** compute game probabilities. The logistic regression model does that.
Claude's only job is:
- `confidence` (0–1): data staleness, mapping risk, microstructure uncertainty
- `reasoning`: human-readable narrative for the recommendation
- `sources`: list of `{type, detail}` dicts

Prompt in `reasoning/claude_confidence.py`. Max tokens: 400. Structured JSON output.
If `ANTHROPIC_API_KEY` is empty, falls back to `confidence=0.55`, neutral reasoning.

### WebSocket reconnect
`ingestion/ws_market.py` maintains a `LocalBook` (in-memory L2 book per asset). On disconnect,
exponential backoff then full-book re-subscribe. The book is rebuilt from the next `book` event —
no state is persisted in memory across reconnects.

---

## What's Missing (Priority Build List)

These are PRD requirements not yet implemented — build in this order:

### 1. Slack notification + approval loop — IMPLEMENTED
The notifier posts top picks to Slack with Approve/Reject buttons when `SLACK_BOT_TOKEN` and
`SLACK_CHANNEL` are set. Button clicks are received by `ploy-slack-events` (port 8766) and
update `recommendations.status` + `human_notes`.

**Files:**
- `notifier/slack.py` — Block Kit message builder + Slack API posting
- `notifier/slack_events.py` — FastAPI app receiving interactivity payloads
- Entry point: `ploy-slack-events`

**Setup:**
1. Create a Slack app at https://api.slack.com/apps with bot scopes: `chat:write`, `chat:update`
2. Set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` in `.env`
3. Set Interactivity Request URL to `http://<your-host>:8766/slack/interactions`
4. Run `ploy-slack-events` alongside the other services

### 2. Model retraining pipeline
`ploy-train-model` exists but there's no scheduled/automated trigger.
After 100+ resolved markets, add a cron or manual workflow to retrain and update
`reasoning/default_model.json`.

### 3. Calibration dashboard
Brier score over time per strategy. Add to the Streamlit dashboard (`dashboard/app.py`) or as a
new route in the FastAPI web app.

---

## Polymarket API Reference

All public, no auth required for read.

| API | URL | Used for |
|---|---|---|
| Gamma REST | `https://gamma-api.polymarket.com` | Market discovery, NBA filtering by tag |
| CLOB REST | `https://clob.polymarket.com` | Order books, trade history, market metadata |
| WebSocket | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Live price/trade stream |

**Geo-restriction:** Polymarket blocks US IPs on some endpoints. Dev may need a non-US egress.
Check `infra/README.md` for GCP egress setup.

---

## Logging

Uses `structlog` with JSON output (set `LOG_JSON=true` for production).

```python
from ploy_agent.common.logging_config import get_logger
log = get_logger("my.service")
log.info("event_name", key=value, ...)  # structured key=value, not f-strings
```

**Convention:** Event names are `snake_case` verbs or noun-verb pairs (`price_tick`,
`ws_reconnect`, `recommendation_persisted`). Never log raw PII or full order book objects.

---

## Database Access Pattern

All DB access is `asyncpg`. Connection pooling via `common/db.py`:

```python
from ploy_agent.common.db import get_pool, close_pool

pool = await get_pool()
async with pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ...")
```

Each service module has its own `repo.py` with typed query functions. Do not write raw SQL
outside of `repo.py` files or migration files.

---

## Adding a New Migration

1. Create `src/ploy_agent/db/migrations/00N_description.sql`
2. Write idempotent SQL (`IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, etc.)
3. Run `ploy-migrate` — it applies all unapplied files in numeric order
4. Commit the migration file; never edit an already-applied migration

---

## File Layout

```
src/ploy_agent/
  common/         config, db pool, logging, scoring formula
  ingestion/      Polymarket WS + REST, L2 book management
  enrichment/     ESPN / Odds API sports data, market→game mapping
  reasoning/      win-prob model, Claude confidence, resolution gate
  strategies/     Strategy ABC + all strategy implementations
  notifier/       composite ranking, recommendation persistence
  web/            FastAPI dashboard + Jinja templates
  dashboard/      Streamlit metrics (optional)
  backtest/       historical accuracy harness
  db/             migration runner + SQL files
```

---

## Testing Guidelines

- Unit tests only — no DB, no network, no external APIs
- Test pure functions: scoring math, odds math, resolution heuristics, strategy logic
- For async service code, mock `asyncpg.Connection` and `httpx.AsyncClient`
- Test file per module: `tests/test_<module>.py`
- Run `pytest` before every PR — CI will enforce this

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
