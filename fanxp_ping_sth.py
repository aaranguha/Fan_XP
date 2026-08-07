"""
fanxp_ping_sth.py — Fan XP Phase 2

For each seat assigned to the STH for this game:
  1. Creates a seat_surrenders row (status = 'detected')
  2. Sends a Twilio SMS to the STH asking them to surrender
  3. Updates status to 'sth_pinged' and saves the Twilio message_sid

Usage:
    python fanxp_ping_sth.py --sth_id 1 --game_id 386

The STH should reply YES to trigger Phase 3 (open the auction).
"""

import argparse
import os
import sys
from dotenv import load_dotenv

load_dotenv()

CREDIT_OFFER = 25  # stadium credit offered per seat


def get_supabase():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env")
        sys.exit(1)
    return create_client(url, key)


def get_twilio():
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not sid or not token:
        print("ERROR: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set in .env")
        sys.exit(1)
    return Client(sid, token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sth_id",  type=int, required=True)
    parser.add_argument("--game_id", type=int, required=True)
    args = parser.parse_args()

    sb     = get_supabase()
    twilio = get_twilio()
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()

    # ── Load STH ─────────────────────────────────────────────
    sth = (
        sb.table("season_ticket_holders")
          .select("id, name, phone, venue_id")
          .eq("id", args.sth_id)
          .single()
          .execute()
    ).data
    if not sth:
        print(f"ERROR: No STH found with id={args.sth_id}")
        sys.exit(1)

    # ── Load seat assignments for this game ───────────────────
    assignments = (
        sb.table("sth_seat_assignments")
          .select("id, seat_id, seats(section, row, seat)")
          .eq("sth_id", args.sth_id)
          .eq("game_id", args.game_id)
          .execute()
    ).data

    if not assignments:
        print(f"ERROR: No seat assignments for sth_id={args.sth_id}, game_id={args.game_id}")
        sys.exit(1)

    # ── Load game info for the message ───────────────────────
    game = (
        sb.table("games")
          .select("opponent, game_date, arena")
          .eq("id", args.game_id)
          .single()
          .execute()
    ).data

    print("=" * 54)
    print(f"  Fan XP — Pinging STH")
    print("=" * 54)
    print(f"  STH:   {sth['name']}  ·  {sth['phone']}")
    print(f"  Game:  {game.get('opponent')} · {game.get('game_date')}")
    print(f"  Seats: {len(assignments)}")

    surrender_ids = []

    for a in assignments:
        seat = a["seats"]
        seat_id = a["seat_id"]
        sec, row, num = seat["section"], seat["row"], seat["seat"]

        print(f"\n  Processing Sec {sec} · Row {row} · Seat {num}...")

        # ── Create seat_surrenders row (detected) ─────────────
        surrender = (
            sb.table("seat_surrenders")
              .insert({
                  "game_id": args.game_id,
                  "seat_id": seat_id,
                  "sth_id":  args.sth_id,
                  "status":  "detected",
              })
              .execute()
        ).data[0]
        surrender_id = surrender["id"]
        print(f"    ✓ surrender_id={surrender_id}  status=detected")

        # ── Build SMS message ─────────────────────────────────
        sms_body = (
            f"Hi {sth['name'].split()[0]}! 👋 This is Fan XP at {game.get('arena', 'the stadium')}.\n\n"
            f"We noticed Sec {sec} · Row {row} · Seat {num} may be empty for tonight's game.\n\n"
            f"Reply YES to surrender it for ${CREDIT_OFFER} in stadium credit.\n"
            f"Reply NO to keep it.\n\n"
            f"Offer expires in 30 minutes."
        )

        # ── Send Twilio SMS ───────────────────────────────────
        message = twilio.messages.create(
            body=sms_body,
            from_=from_number,
            to=sth["phone"],
        )
        print(f"    ✓ SMS sent  message_sid={message.sid}")

        # ── Update status → sth_pinged ────────────────────────
        (
            sb.table("seat_surrenders")
              .update({
                  "status":      "sth_pinged",
                  "message_sid": message.sid,
                  "updated_at":  "now()",
              })
              .eq("id", surrender_id)
              .execute()
        )
        print(f"    ✓ status → sth_pinged")
        surrender_ids.append(surrender_id)

    print(f"""
{'=' * 54}
  Done — {len(assignments)} SMS(es) sent to {sth['phone']}
{'=' * 54}

  surrender_ids: {surrender_ids}

  Waiting for STH to reply YES...
  Next → Phase 3: python fanxp_open_auction.py --surrender_id {surrender_ids[0]}
{'=' * 54}
""")


if __name__ == "__main__":
    main()
