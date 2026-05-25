-- Analytics columns on recommendations for strategy/category/market-type breakdown
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS market_type TEXT;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS question TEXT;

CREATE INDEX IF NOT EXISTS idx_rec_strategy ON recommendations (strategy_id);
CREATE INDEX IF NOT EXISTS idx_rec_category ON recommendations (category);
CREATE INDEX IF NOT EXISTS idx_rec_market_type ON recommendations (market_type);
CREATE INDEX IF NOT EXISTS idx_rec_resolved ON recommendations (resolved_outcome) WHERE resolved_outcome IS NOT NULL;
