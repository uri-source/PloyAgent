# Polymarket Edge Agent (v0)

A real-time market intelligence system that continuously monitors Polymarket prediction markets (currently focused on NBA) and surfaces actionable trading edges to a Slack channel. It does **not** execute trades — it identifies opportunities and lets a human approve or reject each recommendation through interactive Slack buttons.

---

## End-to-End Pipeline

### 1. Live Market Data Ingestion

- Connects to Polymarket's WebSocket feed for real-time price ticks and trade events
- Maintains an in-memory Level 2 order book per market (best bid/ask, depth at 1 cent)
- Periodically snapshots full order books to TimescaleDB for historical analysis
- Discovers new NBA markets via Gamma REST API (tag-based filtering)
- Backfills market metadata and initial book state via CLOB REST API
- Automatic WebSocket reconnection with exponential backoff — if the connection drops, the book rebuilds from the next full snapshot with zero data loss

### 2. Game State Enrichment

- Polls ESPN (free, no API key) every 10 seconds for live NBA game state: score, quarter, clock, possession
- Maps Polymarket market IDs to ESPN game IDs so the reasoning engine knows which game each market tracks
- Supports an optional Odds API provider for sportsbook consensus lines (Pinnacle, Circa, DraftKings, FanDuel)

### 3. Reasoning Engine (Fair Value Computation)

- Triggers evaluation when a market price moves more than 2 cents, or every 60 seconds — whichever comes first
- Evaluates **both** in-game markets (with live game state) and futures/pre-game markets (without game state)
- Runs multiple pluggable strategies in parallel on each market:

  **Strategies currently active:**
  - **Baseline Model** — Logistic regression trained on historical NBA data. Inputs: score differential, time remaining, possession. Outputs a win probability that becomes the fair value.
  - **Cross-Market Arbitrage** — Identifies when complementary Polymarket markets (e.g., "Team A wins" vs "Team B wins") don't sum to 1.0. Handles both binary pairs (two-outcome events, confidence 0.72) and multi-outcome groups (3+ markets, confidence 0.55). Filters out non-mutually-exclusive markets (spread lines, over/unders) using a total-mid bounds check (0.5–1.5).

  **Strategies available but not active (configurable via `.env`):**
  - **Stale Quote Detection** — Flags markets where the price hasn't moved despite significant game state changes
  - **Sportsbook Consensus** — Devigged sharp-book lines as an independent fair value source (requires Odds API key)
  - **Behavior Fade** — Fades overreaction price spikes that exceed a threshold within a short time window
  - **Player Adjust** — Adjusts probability when key player foul trouble or injury signals appear in game state

- **Resolution Risk Gate** — Before any market is scored, it passes through a safety filter that checks whether the market's resolution criteria are unambiguous. Uses heuristic regex scanning for red-flag keywords and optionally an LLM classification call. Markets that fail are silently dropped and never recommended. Results are cached per market.

- **LLM Confidence Layer** (optional) — If an Anthropic API key is configured, Claude evaluates data staleness, market microstructure risk, and mapping uncertainty to produce a confidence score (0–1) and human-readable reasoning narrative. Without the key, the system falls back to a neutral 0.55 confidence — the statistical model still runs.

### 4. Composite Ranking & Notification

- Every 60 seconds, the notifier ranks all recent fair values using a composite score:
  ```
  score = |edge_cents| x log(1 + depth_at_1_cent) x confidence x (1 / (1 + hours_to_resolution))
  ```
- This naturally prioritizes: large edges, liquid markets, high-confidence signals, and markets resolving soon
- Persists the top N (default 5) as recommendation rows in the database
- **15-minute dedup cooldown** — if a market already has a pending recommendation from the last 15 minutes, it's skipped to prevent Slack spam

### 5. Slack Integration (Human-in-the-Loop)

- Posts ranked picks to a configured Slack channel using Block Kit rich formatting
- Each recommendation shows: market name, edge direction (BUY/SELL), edge size in cents, model probability vs market probability, confidence percentage, order book depth, composite score, strategy source, and reasoning narrative
- Each pick has **Approve** and **Reject** buttons
- A separate FastAPI service (port 8766) receives Slack interactivity payloads when a human clicks a button
- Button clicks update the recommendation status in the database and post a confirmation back to Slack
- Slack message references (channel + timestamp) are stored so messages can be updated when status changes

### 6. Web Dashboard

- FastAPI-powered dashboard at `http://127.0.0.1:8765`
- Auto-refreshes every 30 seconds
- Shows all markets, latest prices, fair values, and recommendation history
- Reads directly from TimescaleDB — no extra API layer needed

---

## What the Edge Numbers Mean

An "edge" of 6.5 cents means the model thinks the market is mispriced by 6.5 percentage points. After Polymarket's ~2% fee and typical bid-ask spread, the minimum viable edge is 3 cents (configurable). A **BUY** signal means the model's fair value is above the current market price; **SELL** means below.

---

## Quick Start

1. **Start TimescaleDB:**

   ```bash
   docker compose -f infra/docker-compose.yml up -d
   ```

2. **Create a virtualenv, install the package, run migrations:**

   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   cp .env.example .env
   # Edit .env — minimum required: ANTHROPIC_API_KEY (optional but recommended)
   ploy-migrate
   ```

3. **Configure strategies** in `.env` via `AGENT_STRATEGIES` (comma-separated): `baseline_model`, `stale_quote`, `sportsbook_consensus` (needs `ODDS_API_KEY`), `cross_market_arb`, `behavior_fade`, `player_adjust`.

4. **Configure Slack** (optional) in `.env`: set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` to enable Slack notifications with approve/reject buttons.

5. **Run services** (each in its own terminal):

   ```bash
   ploy-ingest          # Polymarket WS + REST
   ploy-enrich          # ESPN game state poller
   ploy-reason          # Fair value computation
   ploy-notify          # Ranking + Slack posting
   ploy-web             # Dashboard at http://127.0.0.1:8765
   ploy-slack-events    # Slack button click receiver (port 8766)
   ```

6. **Optional:** Streamlit metrics dashboard: `streamlit run src/ploy_agent/dashboard/app.py`

See [infra/README.md](infra/README.md) for hosting, geo-restrictions, and GCP notes.

### macOS / Python TLS errors (`CERTIFICATE_VERIFY_FAILED`)

The app uses **`truststore`** (OS Keychain) plus **`certifi`**. If HTTPS/WebSocket still fails:

1. Run **`Install Certificates.command`** for your Python install (under `/Applications/Python 3.*`), **or**
2. Set **`PLOY_INSECURE_SSL=true`** only for local debugging (disables certificate verification — never use in production).

---

## Infrastructure

- **Database:** TimescaleDB (PostgreSQL with time-series hypertables) — all services communicate through the database only, no message broker
- **Language:** Python 3.11+, fully async (asyncio + asyncpg + httpx + websockets)
- **Config:** pydantic-settings, all configuration through environment variables
- **Deployment:** Docker Compose for TimescaleDB, Python services run as independent processes

---

## What It Does NOT Do

- Does not place trades or interact with Polymarket's trading API
- Does not manage funds, wallets, or positions
- Does not bypass Polymarket's geo-restrictions (dev may need non-US egress)
- Does not guarantee profitable recommendations — it surfaces statistical edges for human review
