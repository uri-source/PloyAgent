# Polymarket Edge Agent (v0)

Four-service Python stack: **ingestion** (Polymarket WS + Gamma/CLOB), **enrichment** (live NBA scores via **ESPN scoreboard** by default — no API key), **reasoning** (logistic win probability + Claude confidence), **notifier** (periodically persists ranked rows). Results are viewed in a **web dashboard** (`ploy-web`). Data lives in **TimescaleDB**.

## Quick start

1. Start TimescaleDB:

   ```bash
   docker compose -f infra/docker-compose.yml up -d
   ```

2. Create a virtualenv, install the package, run migrations:

   ```bash
   cd /path/to/PloyAgent
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ploy_agent
   ploy-migrate
   ```

3. Copy `.env.example` to `.env`. Set **`AGENT_STRATEGIES`** (comma-separated) to choose signal layers: `baseline_model`, `stale_quote`, `sportsbook_consensus` (needs **`ODDS_API_KEY`**), `cross_market_arb`, `behavior_fade`, `player_adjust`. Set **`ANTHROPIC_API_KEY`** for LLM confidence on the baseline path.

4. Run services (each in its own terminal):

   ```bash
   ploy-ingest
   ploy-enrich
   ploy-reason
   ploy-notify   # optional: writes recommendation history rows
   ```

5. Open the **results dashboard**: `ploy-web` → [http://127.0.0.1:8765](http://127.0.0.1:8765) (auto-refreshes every 30s).

6. Optional Streamlit metrics: `streamlit run src/ploy_agent/dashboard/app.py`

See [infra/README.md](infra/README.md) for hosting, geo-restrictions, and GCP notes.

### macOS / Python TLS errors (`CERTIFICATE_VERIFY_FAILED`)

The app uses **`truststore`** (OS Keychain) plus **`certifi`**. If HTTPS/WebSocket still fails:

1. Run **`Install Certificates.command`** for your Python install (under `/Applications/Python 3.*`), **or**
2. Set **`PLOY_INSECURE_SSL=true`** only for local debugging (disables certificate verification — never use in production).

### Sports data

- **`SPORTS_PROVIDER=espn`** (default): uses ESPN’s public NBA scoreboard JSON — suitable for prototyping without The Odds API.
- **`SPORTS_PROVIDER=odds`**: requires `ODDS_API_KEY`.
