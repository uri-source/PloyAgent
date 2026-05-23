-- Paper-trading simulation: threshold profiles and simulated trades

CREATE TABLE IF NOT EXISTS sim_runs (
  id BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  mode TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS sim_profiles (
  id TEXT PRIMARY KEY,
  min_edge_cents DOUBLE PRECISION NOT NULL,
  min_confidence DOUBLE PRECISION NOT NULL,
  min_model_prob DOUBLE PRECISION NOT NULL,
  strategy_ids TEXT[],
  max_open_per_market INT NOT NULL DEFAULT 1,
  cooldown_sec INT NOT NULL DEFAULT 900,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sim_trades (
  id BIGSERIAL PRIMARY KEY,
  sim_run_id BIGINT REFERENCES sim_runs (id) ON DELETE SET NULL,
  profile_id TEXT NOT NULL REFERENCES sim_profiles (id) ON DELETE CASCADE,
  market_id TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  strategy_id TEXT NOT NULL,
  category TEXT,
  question TEXT,
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'open',
  direction TEXT NOT NULL,
  entry_price DOUBLE PRECISION NOT NULL,
  exit_price DOUBLE PRECISION,
  model_prob DOUBLE PRECISION NOT NULL,
  confidence DOUBLE PRECISION NOT NULL,
  edge_cents DOUBLE PRECISION NOT NULL,
  score DOUBLE PRECISION NOT NULL DEFAULT 0,
  resolved_outcome SMALLINT,
  pnl_cents DOUBLE PRECISION,
  close_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_sim_trades_profile_opened ON sim_trades (profile_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_sim_trades_market_profile ON sim_trades (market_id, profile_id);
CREATE INDEX IF NOT EXISTS idx_sim_trades_profile_category ON sim_trades (profile_id, category);
CREATE INDEX IF NOT EXISTS idx_sim_trades_run ON sim_trades (sim_run_id);
CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades (profile_id, status);
