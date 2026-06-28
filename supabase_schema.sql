-- FanXP Supabase Schema
-- Run this in the Supabase SQL editor: https://supabase.com/dashboard/project/_/sql
--
-- Three tables:
--   games    — one row per game (home team + date)
--   listings — seat-level price snapshots (pre_game and halftime/mid_game)
--   no_shows — seats confirmed present in both snapshots (never sold)

-- ── games ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS games (
  id                   SERIAL PRIMARY KEY,
  league               TEXT    NOT NULL DEFAULT 'nba',  -- 'nba', 'mlb', 'nfl'
  home_team            TEXT    NOT NULL,                -- team slug e.g. 'thunder'
  opponent             TEXT    NOT NULL,
  game_date            DATE    NOT NULL,
  day_of_week          TEXT,
  tipoff_local         TEXT,                            -- 'HH:MM' local time
  arena                TEXT,
  city                 TEXT,
  home_draw_score      FLOAT,
  opponent_draw_score  FLOAT,
  game_appeal_score    FLOAT,
  opponent_wins        INTEGER,
  opponent_losses      INTEGER,
  opponent_win_pct     FLOAT,
  created_at           TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(home_team, game_date, league)
);

-- ── listings ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listings (
  id              BIGSERIAL PRIMARY KEY,
  game_id         INTEGER     NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  league          TEXT        NOT NULL DEFAULT 'nba',
  home_team       TEXT        NOT NULL,
  game_date       DATE        NOT NULL,
  snapshot        TEXT        NOT NULL,   -- 'pre_game' or 'halftime' / 'mid_game'
  section         TEXT,
  row             TEXT,
  seat            TEXT,
  price_usd       FLOAT,
  selection_type  TEXT,                   -- 'standard' or 'resale'
  scraped_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS listings_game_snapshot_idx ON listings(game_id, snapshot);
CREATE INDEX IF NOT EXISTS listings_team_date_idx     ON listings(home_team, game_date);

-- ── no_shows ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS no_shows (
  id              BIGSERIAL PRIMARY KEY,
  game_id         INTEGER     NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  league          TEXT        NOT NULL DEFAULT 'nba',
  home_team       TEXT        NOT NULL,
  game_date       DATE        NOT NULL,
  section         TEXT,
  row             TEXT,
  seat            TEXT,
  price_usd       FLOAT,
  selection_type  TEXT,
  scraped_at      TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS no_shows_game_id_idx   ON no_shows(game_id);
CREATE INDEX IF NOT EXISTS no_shows_team_date_idx ON no_shows(home_team, game_date);

-- ── Enable Row Level Security (RLS) ───────────────────────────────────────────
-- The Python scraper uses the service role key (bypasses RLS).
-- The mobile app uses the anon key, which needs read-only access.

ALTER TABLE games    ENABLE ROW LEVEL SECURITY;
ALTER TABLE listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE no_shows ENABLE ROW LEVEL SECURITY;

-- Allow anyone (anon) to read all rows — the app just displays public data.
CREATE POLICY "Public read games"    ON games    FOR SELECT USING (true);
CREATE POLICY "Public read listings" ON listings FOR SELECT USING (true);
CREATE POLICY "Public read no_shows" ON no_shows FOR SELECT USING (true);
