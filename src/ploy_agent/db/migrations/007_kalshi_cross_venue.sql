-- Kalshi market data + curated Polymarket ↔ Kalshi pairs for cross-venue arb

CREATE TABLE IF NOT EXISTS kalshi_markets (
  ticker TEXT PRIMARY KEY,
  title TEXT,
  status TEXT,
  series_ticker TEXT,
  close_time TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kalshi_prices (
  ticker TEXT NOT NULL REFERENCES kalshi_markets (ticker) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL,
  bid DOUBLE PRECISION,
  ask DOUBLE PRECISION,
  mid DOUBLE PRECISION,
  depth_1c DOUBLE PRECISION,
  snapshot_kind TEXT NOT NULL DEFAULT 'poll'
);

SELECT create_hypertable('kalshi_prices', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_kalshi_prices_ticker_ts ON kalshi_prices (ticker, ts DESC);

CREATE TABLE IF NOT EXISTS cross_venue_pairs (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  poly_market_id TEXT NOT NULL REFERENCES markets (id) ON DELETE CASCADE,
  kalshi_ticker TEXT NOT NULL REFERENCES kalshi_markets (ticker) ON DELETE CASCADE,
  outcome_map TEXT NOT NULL DEFAULT 'same',
  resolution_aligned BOOLEAN NOT NULL DEFAULT TRUE,
  notes TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cross_venue_poly ON cross_venue_pairs (poly_market_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_cross_venue_kalshi ON cross_venue_pairs (kalshi_ticker) WHERE active;
