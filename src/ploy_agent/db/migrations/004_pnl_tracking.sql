-- P&L tracking for approved recommendations
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS entry_price DOUBLE PRECISION;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS resolved_outcome SMALLINT;  -- 1=YES, 0=NO, NULL=unresolved
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS pnl_cents DOUBLE PRECISION;  -- hypothetical profit/loss
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS edge_direction TEXT;  -- 'buy' or 'sell'

-- Telegram message tracking (mirrors slack_channel/slack_ts)
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS telegram_chat_id TEXT;

CREATE INDEX IF NOT EXISTS idx_rec_pnl ON recommendations (resolved_at DESC) WHERE pnl_cents IS NOT NULL;
