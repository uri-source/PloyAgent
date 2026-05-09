-- Idempotent for databases created before home/away team columns existed
ALTER TABLE game_state ADD COLUMN IF NOT EXISTS home_team TEXT;
ALTER TABLE game_state ADD COLUMN IF NOT EXISTS away_team TEXT;
