ALTER TABLE fair_values ADD COLUMN IF NOT EXISTS strategy_id TEXT NOT NULL DEFAULT 'baseline_model';
ALTER TABLE fair_values ADD COLUMN IF NOT EXISTS signal_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_fair_market_strategy_ts ON fair_values (market_id, strategy_id, ts DESC);

ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS strategy_id TEXT;

ALTER TABLE markets ADD COLUMN IF NOT EXISTS gamma_event_id TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS event_slug TEXT;

CREATE INDEX IF NOT EXISTS idx_markets_gamma_event ON markets (gamma_event_id);

CREATE TABLE IF NOT EXISTS game_events (
  game_id TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  event_type TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  home_score INT,
  away_score INT
);

SELECT create_hypertable('game_events', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_game_events_game_ts ON game_events (game_id, ts DESC);

CREATE TABLE IF NOT EXISTS market_links (
  market_id_a TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  market_id_b TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  link_type TEXT NOT NULL,
  gamma_event_id TEXT,
  PRIMARY KEY (market_id_a, market_id_b)
);

CREATE INDEX IF NOT EXISTS idx_market_links_event ON market_links (gamma_event_id);

CREATE TABLE IF NOT EXISTS player_impact (
  player_key TEXT PRIMARY KEY,
  team_abbr TEXT,
  epm_delta DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS game_lineups (
  game_id TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  home_active JSONB NOT NULL DEFAULT '[]'::jsonb,
  away_active JSONB NOT NULL DEFAULT '[]'::jsonb
);

SELECT create_hypertable('game_lineups', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_game_lineups_game_ts ON game_lineups (game_id, ts DESC);
