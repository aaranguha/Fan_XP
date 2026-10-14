-- 2026-10-14: accounts + buyer/seller pathways.
-- Run once in the Supabase SQL editor. Every statement is idempotent.
--
-- Accounts themselves live in Supabase Auth (auth.users); name, phone and
-- roles ("buyer", "seller") are stored as that user's user_metadata, so no
-- profile table is needed. These tables hold what sellers own and release.

-- A seat a seller says they hold (season ticket holder or reseller).
-- One owner per physical seat.
CREATE TABLE IF NOT EXISTS seller_seats (
  id          BIGSERIAL    PRIMARY KEY,
  user_id     UUID         NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  team_slug   TEXT         NOT NULL,
  section     TEXT         NOT NULL,
  row_label   TEXT         NOT NULL,
  seat_num    TEXT         NOT NULL,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (team_slug, section, row_label, seat_num)
);
CREATE INDEX IF NOT EXISTS idx_seller_seats_user ON seller_seats(user_id);

-- A seller putting one of their seats up for a specific home game at their
-- own price. Shows on that game's seat map as available to claim.
CREATE TABLE IF NOT EXISTS seat_releases (
  id              BIGSERIAL      PRIMARY KEY,
  seller_seat_id  BIGINT         NOT NULL REFERENCES seller_seats(id) ON DELETE CASCADE,
  game_date       DATE           NOT NULL,
  price_usd       NUMERIC(10,2)  NOT NULL CHECK (price_usd > 0),
  status          TEXT           NOT NULL DEFAULT 'available'
                                 CHECK (status IN ('available','claimed','withdrawn')),
  created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
  UNIQUE (seller_seat_id, game_date)
);
CREATE INDEX IF NOT EXISTS idx_seat_releases_date ON seat_releases(game_date);

-- nfl_sellers: one row per real seller account per team, alongside the
-- existing demo rows (one per team+section, texting STH_PHONE). The old
-- UNIQUE (team_slug, section) now applies to demo rows only.
ALTER TABLE nfl_sellers ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL;
ALTER TABLE nfl_sellers DROP CONSTRAINT IF EXISTS nfl_sellers_team_slug_section_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_nfl_sellers_demo_section
  ON nfl_sellers(team_slug, section) WHERE user_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_nfl_sellers_account_team
  ON nfl_sellers(user_id, team_slug) WHERE user_id IS NOT NULL;

-- Requests remember which signed-in buyer made them, which game they were
-- for, and which seller release (if any) they claimed.
ALTER TABLE nfl_seat_requests ADD COLUMN IF NOT EXISTS buyer_user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL;
ALTER TABLE nfl_seat_requests ADD COLUMN IF NOT EXISTS game_date     DATE;
ALTER TABLE nfl_seat_requests ADD COLUMN IF NOT EXISTS release_id    BIGINT REFERENCES seat_releases(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_nfl_requests_buyer ON nfl_seat_requests(buyer_user_id);

-- Only the API (service role) touches these; no browser access.
ALTER TABLE seller_seats  ENABLE ROW LEVEL SECURITY;
ALTER TABLE seat_releases ENABLE ROW LEVEL SECURITY;
