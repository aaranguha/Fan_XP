"""
supabase_client.py

Thin wrapper around supabase-py for writing game data to Supabase.

All writes are optional — if SUPABASE_URL / SUPABASE_SERVICE_KEY are not set,
calls are silently skipped so the local CSV pipeline keeps working unchanged.

The mobile app reads data using the anon key (read-only via RLS).
The scraper writes using the service role key (bypasses RLS).
"""

import os

from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
    except Exception as e:
        print(f"  [supabase] Failed to init client: {e}")
    return _client


def upsert_game(meta: dict, league: str = "nba") -> int | None:
    """
    Upsert a game record from a game_meta.json dict.
    Returns the Supabase game id, or None if Supabase is not configured.
    """
    client = _get_client()
    if not client:
        return None
    try:
        row = {
            "league":               league,
            "home_team":            meta.get("home_team", ""),
            "opponent":             meta.get("opponent", ""),
            "game_date":            meta.get("game_date", ""),
            "day_of_week":          meta.get("day_of_week", ""),
            "tipoff_local":         meta.get("tipoff_local", ""),
            "arena":                meta.get("arena", ""),
            "city":                 meta.get("city", ""),
            "home_draw_score":      meta.get("home_draw_score"),
            "opponent_draw_score":  meta.get("opponent_draw_score"),
            "game_appeal_score":    meta.get("game_appeal_score"),
            "opponent_wins":        meta.get("opponent_wins"),
            "opponent_losses":      meta.get("opponent_losses"),
            "opponent_win_pct":     meta.get("opponent_win_pct"),
        }
        result = client.table("games").upsert(
            row, on_conflict="home_team,game_date,league"
        ).execute()
        if result.data:
            gid = result.data[0]["id"]
            print(f"  [supabase] Game upserted → id={gid}")
            return gid
    except Exception as e:
        print(f"  [supabase] upsert_game failed: {e}")
    return None


def insert_listings(
    game_id: int,
    rows: list[dict],
    snapshot: str,
    home_team: str,
    game_date: str,
    league: str = "nba",
) -> None:
    """
    Bulk-insert seat listing rows for one snapshot ('pre_game' or 'halftime'/'mid_game').
    Inserts in batches of 500 to stay within Supabase limits.
    """
    client = _get_client()
    if not client or not rows or game_id is None:
        return
    try:
        records = []
        for r in rows:
            price = r.get("price_usd")
            try:
                price_float = float(price) if price not in (None, "", "None") else None
            except (ValueError, TypeError):
                price_float = None
            records.append({
                "game_id":        game_id,
                "league":         league,
                "home_team":      home_team,
                "game_date":      game_date,
                "snapshot":       snapshot,
                "section":        r.get("section", ""),
                "row":            r.get("row", ""),
                "seat":           r.get("seat", ""),
                "price_usd":      price_float,
                "selection_type": r.get("selection_type", ""),
                "scraped_at":     r.get("scraped_at", ""),
            })

        batch = 500
        for i in range(0, len(records), batch):
            client.table("listings").insert(records[i:i + batch]).execute()

        print(f"  [supabase] Inserted {len(records)} {snapshot} listings → game_id={game_id}")
    except Exception as e:
        print(f"  [supabase] insert_listings failed: {e}")


def insert_no_shows(
    game_id: int,
    rows: list[dict],
    home_team: str,
    game_date: str,
    league: str = "nba",
) -> None:
    """Insert confirmed no-show seat records."""
    client = _get_client()
    if not client or not rows or game_id is None:
        return
    try:
        records = []
        for r in rows:
            price = r.get("price_usd")
            try:
                price_float = float(price) if price not in (None, "", "None") else None
            except (ValueError, TypeError):
                price_float = None
            records.append({
                "game_id":        game_id,
                "league":         league,
                "home_team":      home_team,
                "game_date":      game_date,
                "section":        r.get("section", ""),
                "row":            r.get("row", ""),
                "seat":           r.get("seat", ""),
                "price_usd":      price_float,
                "selection_type": r.get("selection_type", ""),
                "scraped_at":     r.get("scraped_at", ""),
            })

        batch = 500
        for i in range(0, len(records), batch):
            client.table("no_shows").insert(records[i:i + batch]).execute()

        print(f"  [supabase] Inserted {len(records)} no-shows → game_id={game_id}")
    except Exception as e:
        print(f"  [supabase] insert_no_shows failed: {e}")
