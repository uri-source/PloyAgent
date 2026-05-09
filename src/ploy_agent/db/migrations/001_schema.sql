-- Polymarket Edge Agent v0 — core schema (TimescaleDB)
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS markets (
  id TEXT PRIMARY KEY,
  slug TEXT,
  question TEXT,
  resolution_criteria TEXT,
  end_date TIMESTAMPTZ,
  category TEXT,
  status TEXT,
  condition_id TEXT,
  clob_asset_id TEXT NOT NULL,
  companion_clob_asset_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_game_map (
  market_id TEXT PRIMARY KEY REFERENCES markets (id) ON DELETE CASCADE,
  game_id TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'parse',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prices (
  market_id TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL,
  bid DOUBLE PRECISION,
  ask DOUBLE PRECISION,
  mid DOUBLE PRECISION,
  depth_1c DOUBLE PRECISION,
  volume_24h DOUBLE PRECISION,
  snapshot_kind TEXT NOT NULL DEFAULT 'tick'
);

SELECT create_hypertable('prices', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_prices_market_ts ON prices (market_id, ts DESC);

CREATE TABLE IF NOT EXISTS order_book_snapshots (
  market_id TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  bids_json JSONB NOT NULL,
  asks_json JSONB NOT NULL,
  trigger_reason TEXT NOT NULL
);

SELECT create_hypertable('order_book_snapshots', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_obs_market_ts ON order_book_snapshots (market_id, ts DESC);

CREATE TABLE IF NOT EXISTS game_state (
  game_id TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  home_score INT,
  away_score INT,
  period INT,
  time_remaining TEXT,
  possession TEXT,
  home_team TEXT,
  away_team TEXT,
  home_lineup JSONB,
  away_lineup JSONB
);

SELECT create_hypertable('game_state', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_game_state_game_ts ON game_state (game_id, ts DESC);

CREATE TABLE IF NOT EXISTS fair_values (
  market_id TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL,
  model_prob DOUBLE PRECISION NOT NULL,
  market_prob DOUBLE PRECISION NOT NULL,
  edge_cents DOUBLE PRECISION NOT NULL,
  confidence DOUBLE PRECISION NOT NULL,
  reasoning TEXT NOT NULL DEFAULT '',
  sources_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

SELECT create_hypertable('fair_values', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_fair_market_ts ON fair_values (market_id, ts DESC);

CREATE TABLE IF NOT EXISTS recommendations (
  id BIGSERIAL PRIMARY KEY,
  market_id TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL,
  score DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  human_notes TEXT,
  slack_channel TEXT,
  slack_ts TEXT,
  payload_json JSONB
);

CREATE INDEX IF NOT EXISTS idx_rec_market_ts ON recommendations (market_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_rec_status ON recommendations (status, ts DESC);

CREATE TABLE IF NOT EXISTS market_resolution_cache (
  market_id TEXT PRIMARY KEY REFERENCES markets (id) ON DELETE CASCADE,
  is_safe BOOLEAN NOT NULL,
  reason TEXT,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
