-- ============================================================
--  Fan XP — Phase 1 Transactional Schema
--  Run this in the Supabase SQL Editor (Dashboard → SQL Editor)
--  Safe to re-run: all statements use IF NOT EXISTS / DO NOTHING
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
-- pgcrypto gives us gen_random_bytes() for pass codes
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── ENUM: seat surrender lifecycle ───────────────────────────
DO $$ BEGIN
  CREATE TYPE surrender_status AS ENUM (
    'detected',       -- empty seat spotted in no_shows
    'sth_pinged',     -- Twilio SMS sent to STH
    'released',       -- STH replied YES
    'auction_active', -- live bidding window is open
    'sold',           -- auction closed, winner charged
    'expired'         -- STH didn't reply / no bids
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- ── venues ───────────────────────────────────────────────────
--  One record per physical stadium / arena.
CREATE TABLE IF NOT EXISTS venues (
  id         BIGSERIAL    PRIMARY KEY,
  name       TEXT         NOT NULL,
  city       TEXT         NOT NULL,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── season_ticket_holders ────────────────────────────────────
--  STH on file with the venue.  credit_balance accumulates
--  every time they surrender a seat.
CREATE TABLE IF NOT EXISTS season_ticket_holders (
  id             BIGSERIAL      PRIMARY KEY,
  venue_id       BIGINT         NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
  name           TEXT           NOT NULL,
  phone          TEXT           NOT NULL,                 -- E.164 format: +1XXXXXXXXXX
  credit_balance NUMERIC(10,2)  NOT NULL DEFAULT 0.00,
  created_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- ── seats ────────────────────────────────────────────────────
--  Physical seat inventory per venue.
--  (section, row, seat) must be unique within a venue.
CREATE TABLE IF NOT EXISTS seats (
  id       BIGSERIAL  PRIMARY KEY,
  venue_id BIGINT     NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
  section  TEXT       NOT NULL,
  row      TEXT       NOT NULL,
  seat     TEXT       NOT NULL,
  UNIQUE (venue_id, section, row, seat)
);

-- ── sth_seat_assignments ─────────────────────────────────────
--  Which STH owns which seat for a specific game.
--  Ties into the existing `games` table (game_id is the PK there).
CREATE TABLE IF NOT EXISTS sth_seat_assignments (
  id      BIGSERIAL  PRIMARY KEY,
  sth_id  BIGINT     NOT NULL REFERENCES season_ticket_holders(id) ON DELETE CASCADE,
  seat_id BIGINT     NOT NULL REFERENCES seats(id)                 ON DELETE CASCADE,
  game_id BIGINT     NOT NULL REFERENCES games(id)                 ON DELETE CASCADE,
  UNIQUE (seat_id, game_id)   -- one owner per seat per game
);

-- ── fans ─────────────────────────────────────────────────────
--  Walk-up fans inside the stadium who place bids.
--  Created on-the-fly when a fan first submits a bid.
CREATE TABLE IF NOT EXISTS fans (
  id         BIGSERIAL   PRIMARY KEY,
  name       TEXT        NOT NULL,
  phone      TEXT        NOT NULL UNIQUE,   -- E.164; winning pass texted here
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── seat_surrenders ──────────────────────────────────────────
--  One row per empty-seat detection event.
--  Tracks the full lifecycle from detection → SMS → auction.
CREATE TABLE IF NOT EXISTS seat_surrenders (
  id          BIGSERIAL        PRIMARY KEY,
  game_id     BIGINT           NOT NULL REFERENCES games(id)                 ON DELETE CASCADE,
  seat_id     BIGINT           NOT NULL REFERENCES seats(id)                 ON DELETE CASCADE,
  sth_id      BIGINT           NOT NULL REFERENCES season_ticket_holders(id) ON DELETE CASCADE,
  fan_id      BIGINT           REFERENCES fans(id),   -- fan who requested this seat (NULL until requested)
  status      surrender_status NOT NULL DEFAULT 'detected',
  message_sid TEXT,            -- Twilio MessageSid for outbound SMS
  updated_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

-- Safe to re-run against an already-created DB (table existed before fan_id was added)
ALTER TABLE seat_surrenders ADD COLUMN IF NOT EXISTS fan_id BIGINT REFERENCES fans(id);

-- ── auctions ─────────────────────────────────────────────────
--  Live 10-minute bidding window for a surrendered seat.
--  One auction per surrender (created when STH replies YES).
CREATE TABLE IF NOT EXISTS auctions (
  id              BIGSERIAL      PRIMARY KEY,
  surrender_id    BIGINT         NOT NULL REFERENCES seat_surrenders(id) ON DELETE CASCADE,
  status          TEXT           NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending','live','closed','cancelled')),
  highest_bid_usd NUMERIC(10,2)  NOT NULL DEFAULT 0.00,
  winning_fan_id  BIGINT         REFERENCES fans(id),   -- NULL until auction closes
  start_time      TIMESTAMPTZ,
  end_time        TIMESTAMPTZ,   -- start_time + 10 minutes
  created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- ── bids ─────────────────────────────────────────────────────
--  Every bid placed by a fan during an auction.
--  Amount must increase (enforced at app layer, not DB layer).
CREATE TABLE IF NOT EXISTS bids (
  id             BIGSERIAL      PRIMARY KEY,
  auction_id     BIGINT         NOT NULL REFERENCES auctions(id) ON DELETE CASCADE,
  fan_id         BIGINT         NOT NULL REFERENCES fans(id)     ON DELETE CASCADE,
  bid_amount_usd NUMERIC(10,2)  NOT NULL CHECK (bid_amount_usd > 0),
  created_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- ── gate_passes ──────────────────────────────────────────────
--  Digital 2nd-half entry pass texted to the winning fan.
--  unique_pass_code is a 12-char hex string (6 random bytes).
CREATE TABLE IF NOT EXISTS gate_passes (
  id               BIGSERIAL   PRIMARY KEY,
  auction_id       BIGINT      NOT NULL REFERENCES auctions(id) ON DELETE CASCADE,
  fan_id           BIGINT      NOT NULL REFERENCES fans(id)     ON DELETE CASCADE,
  unique_pass_code TEXT        NOT NULL UNIQUE
                               DEFAULT upper(encode(gen_random_bytes(6), 'hex')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────
-- Hot query paths: status filtering, auction lookups, bid ordering
CREATE INDEX IF NOT EXISTS idx_surrenders_game_id  ON seat_surrenders(game_id);
CREATE INDEX IF NOT EXISTS idx_surrenders_status   ON seat_surrenders(status);
CREATE INDEX IF NOT EXISTS idx_surrenders_sth_id   ON seat_surrenders(sth_id);
CREATE INDEX IF NOT EXISTS idx_auctions_surrender  ON auctions(surrender_id);
CREATE INDEX IF NOT EXISTS idx_auctions_status     ON auctions(status);
CREATE INDEX IF NOT EXISTS idx_bids_auction_id     ON bids(auction_id);
CREATE INDEX IF NOT EXISTS idx_bids_created_at     ON bids(auction_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assignments_game    ON sth_seat_assignments(game_id);
CREATE INDEX IF NOT EXISTS idx_assignments_sth     ON sth_seat_assignments(sth_id);

-- ── Realtime: enable for live bidding UI ─────────────────────
-- Run these after tables are created so the Vercel app can
-- subscribe to bid and auction changes without polling.
ALTER PUBLICATION supabase_realtime ADD TABLE auctions;
ALTER PUBLICATION supabase_realtime ADD TABLE bids;
ALTER PUBLICATION supabase_realtime ADD TABLE seat_surrenders;

-- ── nfl_seat_requests ─────────────────────────────────────────
--  A fan claiming an open seat from one of the 32 static NFL
--  stadium seat maps (docs/nfl_{slug}_seatmap.html). These seats
--  come from Ticketmaster section/row/seat geometry, not from a
--  pre-seeded `seats`/`venues`/STH game — so this table stands
--  alone rather than joining into the surrender/auction flow above.
CREATE TABLE IF NOT EXISTS nfl_seat_requests (
  id           BIGSERIAL    PRIMARY KEY,
  team_slug    TEXT         NOT NULL,
  section      TEXT         NOT NULL,
  row_label    TEXT         NOT NULL,
  seat_num     TEXT         NOT NULL,
  price_usd    NUMERIC(10,2) NOT NULL,
  fan_name     TEXT         NOT NULL,
  fan_phone    TEXT         NOT NULL,
  status       TEXT         NOT NULL DEFAULT 'requested'
                            CHECK (status IN ('requested','confirmed','cancelled')),
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nfl_requests_team ON nfl_seat_requests(team_slug);
