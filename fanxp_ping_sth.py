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

from fanxp_common import CREDIT_OFFER, get_supabase, get_twilio, send_surrender_sms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sth_id",  type=int, required=True)
    parser.add_argument("--game_id", type=int, required=True)
    args = parser.parse_args()

    try:
        sb     = get_supabase()
        twilio = get_twilio()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

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

        # ── Send Twilio SMS ───────────────────────────────────
        message = send_surrender_sms(twilio, sth, seat, game, CREDIT_OFFER)
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
