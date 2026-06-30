-- Auto-mapped WC game pairs: match metadata + review queue

ALTER TABLE cross_venue_pairs
  ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS match_source TEXT NOT NULL DEFAULT 'manual',
  ADD COLUMN IF NOT EXISTS kalshi_event_ticker TEXT,
  ADD COLUMN IF NOT EXISTS poly_event_slug TEXT,
  ADD COLUMN IF NOT EXISTS review_notes TEXT;

CREATE INDEX IF NOT EXISTS idx_cross_venue_match_source
  ON cross_venue_pairs (match_source, active);
