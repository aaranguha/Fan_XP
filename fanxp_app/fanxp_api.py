"""
fanxp_api.py — Fan XP Phase 3

Fan-facing web API backing docs/fanxp_app.html.

  GET  /api/empty-seats              — list currently-empty seats for the demo game
  POST /api/seats/<seat_id>/request  — a fan asks to take a seat; texts the STH
  GET  /api/requests/<surrender_id>  — poll status of a request
  POST /webhooks/twilio/sms          — STH's YES/NO reply (Twilio inbound webhook)

Run locally:
    python fanxp_api.py
"""

import os
import re

from flask import Flask, jsonify, request, Response
from flask_cors import CORS

from fanxp_common import CREDIT_OFFER, get_supabase, get_twilio, send_surrender_sms

app = Flask(__name__)
CORS(app)


# ── Helpers ──────────────────────────────────────────────────────────────────

def demo_game_id(sb):
    """The most recent game_id that has seeded STH seat assignments."""
    result = (
        sb.table("sth_seat_assignments")
          .select("game_id")
          .order("game_id", desc=True)
          .limit(1)
          .execute()
    )
    return result.data[0]["game_id"] if result.data else None


def find_price(sb, game_id, seat):
    result = (
        sb.table("no_shows")
          .select("price_usd")
          .eq("game_id", game_id)
          .eq("section", str(seat["section"]))
          .eq("row", str(seat["row"]))
          .eq("seat", str(seat["seat"]))
          .limit(1)
          .execute()
    )
    return result.data[0]["price_usd"] if result.data else None


def latest_surrender(sb, seat_id, game_id):
    result = (
        sb.table("seat_surrenders")
          .select("*")
          .eq("seat_id", seat_id)
          .eq("game_id", game_id)
          .order("id", desc=True)
          .limit(1)
          .execute()
    )
    return result.data[0] if result.data else None


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/api/empty-seats")
def empty_seats():
    sb = get_supabase()
    game_id = demo_game_id(sb)
    if game_id is None:
        return jsonify({"game": None, "seats": []})

    game = (
        sb.table("games")
          .select("id, opponent, game_date, arena")
          .eq("id", game_id)
          .single()
          .execute()
    ).data

    assignments = (
        sb.table("sth_seat_assignments")
          .select("seat_id, seats(id, section, row, seat)")
          .eq("game_id", game_id)
          .execute()
    ).data or []

    seats = []
    for a in assignments:
        seat = a["seats"]
        surrender = latest_surrender(sb, seat["id"], game_id)
        seats.append({
            "seat_id": seat["id"],
            "section": seat["section"],
            "row":     seat["row"],
            "seat":    seat["seat"],
            "price":   find_price(sb, game_id, seat),
            "status":  surrender["status"] if surrender else "available",
            "surrender_id": surrender["id"] if surrender else None,
        })

    return jsonify({"game": game, "seats": seats})


@app.route("/api/seats/<int:seat_id>/request", methods=["POST"])
def request_seat(seat_id):
    body = request.get_json(force=True) or {}
    fan_name  = (body.get("fan_name") or "").strip()
    fan_phone = (body.get("fan_phone") or "").strip()
    if not fan_name or not fan_phone:
        return jsonify({"error": "fan_name and fan_phone are required"}), 400

    sb = get_supabase()
    game_id = demo_game_id(sb)
    if game_id is None:
        return jsonify({"error": "no demo game seeded"}), 404

    assignment = (
        sb.table("sth_seat_assignments")
          .select("sth_id, seats(id, section, row, seat)")
          .eq("seat_id", seat_id)
          .eq("game_id", game_id)
          .limit(1)
          .execute()
    ).data
    if not assignment:
        return jsonify({"error": f"seat {seat_id} is not part of the demo game"}), 404
    assignment = assignment[0]
    seat = assignment["seats"]
    sth_id = assignment["sth_id"]

    sth = (
        sb.table("season_ticket_holders")
          .select("id, name, phone")
          .eq("id", sth_id)
          .single()
          .execute()
    ).data

    game = (
        sb.table("games")
          .select("opponent, game_date, arena")
          .eq("id", game_id)
          .single()
          .execute()
    ).data

    # ── Upsert fan by phone ────────────────────────────────────
    existing_fan = (
        sb.table("fans").select("id").eq("phone", fan_phone).limit(1).execute()
    ).data
    if existing_fan:
        fan_id = existing_fan[0]["id"]
    else:
        fan_id = (
            sb.table("fans").insert({"name": fan_name, "phone": fan_phone}).execute()
        ).data[0]["id"]

    # ── Create seat_surrenders row (detected) ──────────────────
    surrender = (
        sb.table("seat_surrenders")
          .insert({
              "game_id": game_id,
              "seat_id": seat_id,
              "sth_id":  sth_id,
              "fan_id":  fan_id,
              "status":  "detected",
          })
          .execute()
    ).data[0]
    surrender_id = surrender["id"]

    # ── Text the STH, update status ────────────────────────────
    twilio = get_twilio()
    message = send_surrender_sms(twilio, sth, seat, game, CREDIT_OFFER)

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

    return jsonify({"surrender_id": surrender_id, "status": "sth_pinged"})


@app.route("/api/requests/<int:surrender_id>")
def request_status(surrender_id):
    sb = get_supabase()
    surrender = (
        sb.table("seat_surrenders").select("*").eq("id", surrender_id).single().execute()
    ).data
    if not surrender:
        return jsonify({"error": "not found"}), 404

    resp = {"surrender_id": surrender_id, "status": surrender["status"]}

    if surrender["status"] == "sold":
        auction = (
            sb.table("auctions")
              .select("id")
              .eq("surrender_id", surrender_id)
              .order("id", desc=True)
              .limit(1)
              .execute()
        ).data
        if auction:
            gate_pass = (
                sb.table("gate_passes")
                  .select("unique_pass_code")
                  .eq("auction_id", auction[0]["id"])
                  .limit(1)
                  .execute()
            ).data
            if gate_pass:
                resp["pass_code"] = gate_pass[0]["unique_pass_code"]

    return jsonify(resp)


@app.route("/api/nfl/<slug>/request", methods=["POST"])
def request_nfl_seat(slug):
    body = request.get_json(force=True) or {}
    section   = str(body.get("section") or "").strip()
    row_label = str(body.get("row") or "").strip()
    seat_num  = str(body.get("seat") or "").strip()
    price     = body.get("price")
    fan_name  = (body.get("fan_name") or "").strip()
    fan_phone = (body.get("fan_phone") or "").strip()

    if not section or not row_label or not seat_num or price is None:
        return jsonify({"error": "section, row, seat, and price are required"}), 400
    if not fan_name or not fan_phone:
        return jsonify({"error": "fan_name and fan_phone are required"}), 400

    sb = get_supabase()
    row = (
        sb.table("nfl_seat_requests")
          .insert({
              "team_slug": slug,
              "section":   section,
              "row_label": row_label,
              "seat_num":  seat_num,
              "price_usd": price,
              "fan_name":  fan_name,
              "fan_phone": fan_phone,
          })
          .execute()
    ).data[0]

    return jsonify({"request_id": row["id"], "status": row["status"]})


@app.route("/webhooks/twilio/sms", methods=["POST"])
def twilio_sms_webhook():
    from_phone = (request.form.get("From") or "").strip()
    body_text  = (request.form.get("Body") or "").strip().upper()

    sb = get_supabase()
    sth = (
        sb.table("season_ticket_holders").select("id, credit_balance").eq("phone", from_phone).limit(1).execute()
    ).data
    if not sth:
        return Response("<Response></Response>", mimetype="text/xml")
    sth = sth[0]

    surrender = (
        sb.table("seat_surrenders")
          .select("*")
          .eq("sth_id", sth["id"])
          .eq("status", "sth_pinged")
          .order("id", desc=True)
          .limit(1)
          .execute()
    ).data
    if not surrender:
        return Response("<Response></Response>", mimetype="text/xml")
    surrender = surrender[0]
    surrender_id = surrender["id"]

    if re.match(r"^YES\b", body_text):
        (
            sb.table("seat_surrenders")
              .update({"status": "released", "updated_at": "now()"})
              .eq("id", surrender_id)
              .execute()
        )

        # ── Single-bid auction: the requesting fan wins instantly ──
        auction = (
            sb.table("auctions")
              .insert({"surrender_id": surrender_id, "status": "live", "start_time": "now()"})
              .execute()
        ).data[0]
        auction_id = auction["id"]

        fan_id = surrender["fan_id"]
        bid_amount = CREDIT_OFFER

        if fan_id:
            sb.table("bids").insert({
                "auction_id":     auction_id,
                "fan_id":         fan_id,
                "bid_amount_usd": bid_amount,
            }).execute()

        (
            sb.table("auctions")
              .update({
                  "status":          "closed",
                  "highest_bid_usd": bid_amount,
                  "winning_fan_id":  fan_id,
                  "end_time":        "now()",
              })
              .eq("id", auction_id)
              .execute()
        )

        (
            sb.table("seat_surrenders")
              .update({"status": "sold", "updated_at": "now()"})
              .eq("id", surrender_id)
              .execute()
        )

        if fan_id:
            gate_pass = (
                sb.table("gate_passes")
                  .insert({"auction_id": auction_id, "fan_id": fan_id})
                  .execute()
            ).data[0]

            fan = (
                sb.table("fans").select("phone").eq("id", fan_id).single().execute()
            ).data

            twilio = get_twilio()
            from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
            twilio.messages.create(
                body=(
                    f"You're in! Your seat is confirmed for the 2nd half.\n\n"
                    f"Gate pass code: {gate_pass['unique_pass_code']}\n\n"
                    f"Show this at the gate to enter."
                ),
                from_=from_number,
                to=fan["phone"],
            )

        sb.table("season_ticket_holders").update({
            "credit_balance": float(sth["credit_balance"]) + CREDIT_OFFER,
        }).eq("id", sth["id"]).execute()

    elif re.match(r"^NO\b", body_text):
        (
            sb.table("seat_surrenders")
              .update({"status": "expired", "updated_at": "now()"})
              .eq("id", surrender_id)
              .execute()
        )

    return Response("<Response></Response>", mimetype="text/xml")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
