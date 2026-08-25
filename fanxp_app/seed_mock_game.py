"""
seed_mock_game.py — Fan XP Phase 1

Sets up the mock demo environment by:
  1. Finding the most recent game in our DB that has real no-show data
  2. Inserting a venue record from that game's arena/city metadata
  3. Pulling the 3 highest-priced no-show seats as the demo empty seats
  4. Creating a Season Ticket Holder record tied to your real phone number
  5. Assigning all 3 seats to that STH for that game

Run once before starting Phase 2 (Twilio SMS ping).

Usage:
    python seed_mock_game.py

Requires:
    SUPABASE_URL and SUPABASE_SERVICE_KEY in .env or environment
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
STH_PHONE    = "+15109469095"   # Your real number — Twilio will text this
STH_NAME     = "Aaran Guha"
CREDIT_OFFER = 25.00            # Stadium credit amount offered for surrender
NUM_SEATS    = 3                # How many empty seats to seed


def get_client():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
        print("       Add them to your .env file and try again.")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(url, key)
    except ImportError:
        print("ERROR: supabase-py not installed. Run: pip install supabase")
        sys.exit(1)


def find_best_game(sb):
    """
    Find the most recent game_id that has no_shows with non-null price_usd.
    Falls back to any no_show game if no priced rows exist.
    """
    print("Scanning no_shows for best demo game...")

    # Try: most recent game with priced no-shows (gives us real $ numbers)
    result = (
        sb.table("no_shows")
          .select("game_id")
          .not_.is_("price_usd", "null")
          .gt("price_usd", 0)
          .order("game_id", desc=True)
          .limit(1)
          .execute()
    )
    if result.data:
        return result.data[0]["game_id"]

    # Fallback: any game with no_shows
    result = (
        sb.table("no_shows")
          .select("game_id")
          .order("game_id", desc=True)
          .limit(1)
          .execute()
    )
    if result.data:
        print("  (no priced rows found — using any no_show game)")
        return result.data[0]["game_id"]

    return None


def pick_seats(sb, game_id, n=3):
    """
    Pull the n highest-priced no-show seats for a game.
    These become our mock 'detected' empty seats.
    """
    result = (
        sb.table("no_shows")
          .select("section, row, seat, price_usd")
          .eq("game_id", game_id)
          .not_.is_("price_usd", "null")
          .gt("price_usd", 0)
          .order("price_usd", desc=True)
          .limit(n)
          .execute()
    )
    seats = result.data or []

    if len(seats) < n:
        # Fallback: any seats for this game regardless of price
        fallback = (
            sb.table("no_shows")
              .select("section, row, seat, price_usd")
              .eq("game_id", game_id)
              .limit(n)
              .execute()
        ).data or []
        # Merge: keep priced ones, fill remainder from fallback
        existing_keys = {(s["section"], s["row"], s["seat"]) for s in seats}
        for row in fallback:
            key = (row["section"], row["row"], row["seat"])
            if key not in existing_keys:
                seats.append(row)
                existing_keys.add(key)
            if len(seats) >= n:
                break

    return seats[:n]


def main():
    sb = get_client()

    print("=" * 54)
    print("  Fan XP — Seeding Mock Demo Environment")
    print("=" * 54)

    # ── Step 1: Find best game ────────────────────────────────
    game_id = find_best_game(sb)
    if not game_id:
        print("\nERROR: No games with no_show data found in database.")
        print("       Run mlb_runner.py or daily_runner.py first.")
        sys.exit(1)

    # ── Step 2: Load game metadata ────────────────────────────
    game_result = (
        sb.table("games")
          .select("id, home_team, opponent, game_date, arena, city, league")
          .eq("id", game_id)
          .single()
          .execute()
    )
    game = game_result.data
    if not game:
        print(f"\nERROR: Could not load game id={game_id} from games table.")
        sys.exit(1)

    print(f"\n  Game selected:")
    print(f"    {game.get('opponent')} at {game.get('home_team')}")
    print(f"    {game.get('game_date')}  ·  {game.get('league','').upper()}")
    print(f"    {game.get('arena')}  ·  {game.get('city')}")

    # ── Step 3: Pick 3 real empty seats ──────────────────────
    seat_rows = pick_seats(sb, game_id, NUM_SEATS)
    if not seat_rows:
        print(f"\nERROR: No seat records found in no_shows for game_id={game_id}.")
        sys.exit(1)

    print(f"\n  Seats pulled from no_shows ({len(seat_rows)} seats):")
    for s in seat_rows:
        price = s.get("price_usd")
        price_str = f"${price:.2f}" if price else "no price"
        print(f"    Section {s['section']} · Row {s['row']} · Seat {s['seat']}  ({price_str})")

    # ── Step 4: Insert venue ──────────────────────────────────
    venue_name = game.get("arena") or f"{game.get('home_team', 'Demo').title()} Arena"
    venue_city = game.get("city") or "Demo City"

    print(f"\n  Inserting venue: {venue_name}, {venue_city}...")
    venue_res = (
        sb.table("venues")
          .insert({"name": venue_name, "city": venue_city})
          .execute()
    )
    venue_id = venue_res.data[0]["id"]
    print(f"    ✓ venue_id = {venue_id}")

    # ── Step 5: Insert Season Ticket Holder ───────────────────
    print(f"\n  Inserting STH: {STH_NAME}  ·  {STH_PHONE}...")
    sth_res = (
        sb.table("season_ticket_holders")
          .insert({
              "venue_id":       venue_id,
              "name":           STH_NAME,
              "phone":          STH_PHONE,
              "credit_balance": 0.00,
          })
          .execute()
    )
    sth_id = sth_res.data[0]["id"]
    print(f"    ✓ sth_id = {sth_id}")

    # ── Step 6: Insert seats + assign to STH ─────────────────
    print(f"\n  Inserting seats and assigning to STH...")
    inserted_seats = []
    for s in seat_rows:
        seat_res = (
            sb.table("seats")
              .insert({
                  "venue_id": venue_id,
                  "section":  str(s["section"]),
                  "row":      str(s["row"]),
                  "seat":     str(s["seat"]),
              })
              .execute()
        )
        seat_id = seat_res.data[0]["id"]

        (
            sb.table("sth_seat_assignments")
              .insert({
                  "sth_id":  sth_id,
                  "seat_id": seat_id,
                  "game_id": game_id,
              })
              .execute()
        )

        inserted_seats.append({
            "seat_id": seat_id,
            "section": s["section"],
            "row":     s["row"],
            "seat":    s["seat"],
            "price":   s.get("price_usd"),
        })
        print(f"    ✓ seat_id={seat_id}  →  Sec {s['section']} · Row {s['row']} · Seat {s['seat']}")

    # ── Done: print summary ───────────────────────────────────
    print(f"""
{'=' * 54}
  Mock Environment Ready — Fan XP Phase 1 Complete
{'=' * 54}

  Game:    {game.get('opponent')} · {game.get('game_date')}
  League:  {game.get('league', '').upper()}
  Venue:   {venue_name}, {venue_city}  (id={venue_id})
  STH:     {STH_NAME}  ·  {STH_PHONE}  (id={sth_id})

  Seats seeded:""")
    for s in inserted_seats:
        price_str = f"${s['price']:.2f}" if s["price"] else "no price"
        print(f"    seat_id={s['seat_id']}  Sec {s['section']} · Row {s['row']} · Seat {s['seat']}  ({price_str})")

    print(f"""
  IDs to keep for Phase 2:
    game_id  = {game_id}
    venue_id = {venue_id}
    sth_id   = {sth_id}

  Next → Phase 2: python fanxp_ping_sth.py --sth_id {sth_id} --game_id {game_id}
{'=' * 54}
""")


if __name__ == "__main__":
    main()
