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
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, Response
from flask_cors import CORS

from fanxp_common import (
    CREDIT_OFFER,
    NFL_OFFER_WINDOW_MINUTES,
    SERVICE_FEE_RATE,
    get_stripe,
    get_supabase,
    get_twilio,
    send_nfl_seller_sms,
    send_or_log_sms,
    send_surrender_sms,
)

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


def get_or_create_nfl_seller(sb, slug, section):
    existing = (
        sb.table("nfl_sellers")
          .select("*")
          .eq("team_slug", slug)
          .eq("section", section)
          .limit(1)
          .execute()
    ).data
    if existing:
        return existing[0]

    sth_phone = os.getenv("STH_PHONE", "").strip()
    if not sth_phone:
        raise RuntimeError("STH_PHONE not set in .env")

    return (
        sb.table("nfl_sellers")
          .insert({
              "team_slug": slug,
              "section":   section,
              "name":      f"Section {section} Season Ticket Holder",
              "phone":     sth_phone,
          })
          .execute()
    ).data[0]


def expire_if_stale(sb, req):
    """
    Requests never got a background sweep, so expiry is checked lazily
    wherever a request is read or acted on: if it's still 'requested'
    (checkout abandoned) or 'seller_pinged' past NFL_OFFER_WINDOW_MINUTES
    after creation, flip it to 'expired', release any payment hold, and
    return the updated row. Otherwise returns `req` unchanged.
    """
    if req["status"] not in ("requested", "seller_pinged"):
        return req
    created_at = datetime.fromisoformat(req["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - created_at <= timedelta(minutes=NFL_OFFER_WINDOW_MINUTES):
        return req

    if req.get("stripe_payment_intent_id"):
        try:
            get_stripe().PaymentIntent.cancel(req["stripe_payment_intent_id"])
        except Exception:
            pass  # already captured/cancelled/expired on Stripe's side — fine

    return (
        sb.table("nfl_seat_requests")
          .update({"status": "expired", "updated_at": "now()"})
          .eq("id", req["id"])
          .execute()
    ).data[0]


def ping_nfl_seller(sb, req):
    """Texts the seller and flips the request to 'seller_pinged'. Called
    from the Stripe webhook once the card hold is authorized."""
    seller = (
        sb.table("nfl_sellers").select("*").eq("id", req["seller_id"]).single().execute()
    ).data
    twilio = get_twilio()
    message = send_nfl_seller_sms(twilio, seller, req, os.getenv("API_PUBLIC_URL", request.url_root), CREDIT_OFFER)
    sb.table("nfl_seat_requests").update({
        "status":      "seller_pinged",
        "message_sid": message.sid,
        "updated_at":  "now()",
    }).eq("id", req["id"]).execute()


@app.route("/api/nfl/<slug>/request", methods=["POST"])
def request_nfl_seat(slug):
    body = request.get_json(force=True) or {}
    section       = str(body.get("section") or "").strip()
    row_label     = str(body.get("row") or "").strip()
    seat_num      = str(body.get("seat") or "").strip()
    price         = body.get("price")
    fan_name      = (body.get("fan_name") or "").strip()
    fan_phone     = (body.get("fan_phone") or "").strip()
    return_to_url = (body.get("return_to_url") or "").strip()

    if not section or not row_label or not seat_num or price is None:
        return jsonify({"error": "section, row, seat, and price are required"}), 400
    if not fan_name or not fan_phone:
        return jsonify({"error": "fan_name and fan_phone are required"}), 400
    if not return_to_url:
        return jsonify({"error": "return_to_url is required"}), 400

    price = float(price)
    fee = round(price * SERVICE_FEE_RATE, 2)
    total_cents = round((price + fee) * 100)

    sb = get_supabase()
    seller = get_or_create_nfl_seller(sb, slug, section)

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
              "seller_id": seller["id"],
          })
          .execute()
    ).data[0]

    stripe = get_stripe()
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_intent_data={"capture_method": "manual"},
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": total_cents,
                "product_data": {
                    "name": f"{slug.upper()} — Sec {section}, Row {row_label}, Seat {seat_num}",
                    "description": "Card is authorized now and only charged if the seat owner confirms.",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{return_to_url}?request_id={row['id']}",
        cancel_url=f"{return_to_url}?cancelled_request_id={row['id']}",
        metadata={"request_id": str(row["id"])},
    )

    sb.table("nfl_seat_requests").update({
        "stripe_session_id": session.id,
        "updated_at":        "now()",
    }).eq("id", row["id"]).execute()

    return jsonify({"request_id": row["id"], "checkout_url": session.url})


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    stripe = get_stripe()
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    secret  = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        return jsonify({"error": f"invalid webhook: {e}"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        request_id = int(session["metadata"]["request_id"])
        payment_intent_id = session.get("payment_intent")

        sb = get_supabase()
        req = (
            sb.table("nfl_seat_requests").select("*").eq("id", request_id).single().execute()
        ).data
        if req and req["status"] == "requested":
            sb.table("nfl_seat_requests").update({
                "stripe_payment_intent_id": payment_intent_id,
                "updated_at":               "now()",
            }).eq("id", request_id).execute()
            req["stripe_payment_intent_id"] = payment_intent_id
            ping_nfl_seller(sb, req)

    return jsonify({"received": True})


@app.route("/api/nfl/requests/<int:request_id>")
def nfl_request_status(request_id):
    sb = get_supabase()
    row = (
        sb.table("nfl_seat_requests").select("*").eq("id", request_id).single().execute()
    ).data
    if not row:
        return jsonify({"error": "not found"}), 404
    row = expire_if_stale(sb, row)

    resp = {
        "request_id": request_id,
        "status":     row["status"],
        "section":    row["section"],
        "row":        row["row_label"],
        "seat":       row["seat_num"],
        "price":      float(row["price_usd"]),
    }
    if row["status"] == "confirmed" and row.get("pass_code"):
        resp["pass_code"] = row["pass_code"]
    return jsonify(resp)


def resolve_nfl_seat_request(sb, req, seller, decision):
    """
    Applies a seller's YES/NO decision to a pending nfl_seat_requests row:
    on YES, confirms it with a gate-pass code, credits the seller, and
    notifies the fan; on NO, marks it declined. Used by both the real
    Twilio webhook and the /respond endpoint (which also backs the
    TWILIO_DRY_RUN "simulate the seller" path used when SMS sending is
    blocked, e.g. by A2P 10DLC on a trial account).
    """
    if decision == "YES":
        if req.get("stripe_payment_intent_id"):
            get_stripe().PaymentIntent.capture(req["stripe_payment_intent_id"])

        pass_code = secrets.token_hex(6).upper()

        sb.table("nfl_seat_requests").update({
            "status":     "confirmed",
            "pass_code":  pass_code,
            "updated_at": "now()",
        }).eq("id", req["id"]).execute()

        sb.table("nfl_sellers").update({
            "credit_balance": float(seller["credit_balance"]) + CREDIT_OFFER,
        }).eq("id", seller["id"]).execute()

        send_or_log_sms(
            get_twilio(),
            req["fan_phone"],
            f"You're in! Your seat is confirmed for the 2nd half.\n\n"
            f"Sec {req['section']} · Row {req['row_label']} · Seat {req['seat_num']}\n"
            f"Gate pass code: {pass_code}\n\n"
            f"Show this at the gate to enter.",
        )
        return "confirmed"

    elif decision == "NO":
        if req.get("stripe_payment_intent_id"):
            get_stripe().PaymentIntent.cancel(req["stripe_payment_intent_id"])

        sb.table("nfl_seat_requests").update({
            "status":     "declined",
            "updated_at": "now()",
        }).eq("id", req["id"]).execute()
        return "declined"

    return None


def handle_nfl_sms_reply(sb, from_phone, body_text):
    """
    Returns True if `from_phone` matches a known NFL seller and the reply
    was handled (regardless of YES/NO/gibberish) — False means the caller
    should fall through to the season-ticket-holder flow below, since both
    flows can share the same test phone number (STH_PHONE) during a demo.
    """
    seller_rows = (
        sb.table("nfl_sellers").select("id, name, credit_balance").eq("phone", from_phone).execute()
    ).data
    if not seller_rows:
        return False
    seller_ids = [s["id"] for s in seller_rows]

    # A specific request id in the reply ("YES 12") disambiguates when one
    # seller phone has multiple pending requests; otherwise fall back to
    # the most recently pinged one for this phone.
    id_match = re.search(r"\b(\d+)\b", body_text)
    query = (
        sb.table("nfl_seat_requests")
          .select("*")
          .in_("seller_id", seller_ids)
          .eq("status", "seller_pinged")
    )
    if id_match:
        query = query.eq("id", int(id_match.group(1)))
    pending = query.order("id", desc=True).limit(1).execute().data
    if not pending:
        return True  # it's our number, just nothing pending — swallow it
    req = pending[0]
    seller = next(s for s in seller_rows if s["id"] == req["seller_id"])

    if re.match(r"^YES\b", body_text):
        resolve_nfl_seat_request(sb, req, seller, "YES")
    elif re.match(r"^NO\b", body_text):
        resolve_nfl_seat_request(sb, req, seller, "NO")

    return True


def apply_nfl_response(request_id, decision):
    """
    Shared by the JSON /respond endpoint and the tap-to-confirm SMS link.
    Returns (new_status_or_None, error_message_or_None).
    """
    if decision not in ("YES", "NO"):
        return None, "decision must be YES or NO"

    sb = get_supabase()
    req = (
        sb.table("nfl_seat_requests").select("*").eq("id", request_id).single().execute()
    ).data
    if not req:
        return None, "not found"
    req = expire_if_stale(sb, req)
    if req["status"] != "seller_pinged":
        return None, f"request is '{req['status']}', not awaiting a response"

    seller = (
        sb.table("nfl_sellers").select("*").eq("id", req["seller_id"]).single().execute()
    ).data

    return resolve_nfl_seat_request(sb, req, seller, decision), None


@app.route("/api/nfl/requests/<int:request_id>/respond", methods=["POST"])
def respond_nfl_request(request_id):
    """
    Lets a seller confirm/decline a request without texting back — used to
    simulate the seller's reply when real SMS delivery is unavailable
    (TWILIO_DRY_RUN), and as the JSON form of the tap-to-confirm SMS link
    below.
    """
    body = request.get_json(force=True) or {}
    decision = (body.get("decision") or "").strip().upper()
    new_status, error = apply_nfl_response(request_id, decision)
    if error:
        status_code = 404 if error == "not found" else 409 if "awaiting" in error else 400
        return jsonify({"error": error}), status_code
    return jsonify({"request_id": request_id, "status": new_status})


NFL_RESPOND_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fan XP</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;background:#f4f6fb;color:#14181f;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;}}
  .card{{max-width:380px;background:#fff;border-radius:16px;padding:32px 28px;text-align:center;
        box-shadow:0 12px 32px rgba(0,0,0,.08);}}
  .icon{{font-size:2.4rem;margin-bottom:12px;}}
  h1{{font-size:1.1rem;margin:0 0 8px;}}
  p{{color:#5b6472;font-size:.9rem;line-height:1.5;margin:0;}}
  .code{{display:inline-block;margin-top:14px;font-weight:800;font-size:1.05rem;
        letter-spacing:.04em;background:#f4f6fb;border-radius:8px;padding:8px 14px;}}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h1>{heading}</h1>
  <p>{body}</p>
</div></body></html>"""


@app.route("/nfl/respond/<int:request_id>")
def nfl_respond_link(request_id):
    decision = (request.args.get("decision") or "").strip().upper()
    new_status, error = apply_nfl_response(request_id, decision)

    if error == "not found":
        return NFL_RESPOND_PAGE.format(icon="\U0001F937", heading="Request not found",
                                        body="This link doesn't match a Fan XP request."), 404
    if error and error.startswith("request is 'expired'"):
        return NFL_RESPOND_PAGE.format(icon="⏰", heading="This offer expired",
                                        body="Too much time passed before this was answered, so the fan's request already timed out.")
    if error:
        return NFL_RESPOND_PAGE.format(icon="✅", heading="Already handled",
                                        body="This request was already responded to.")

    if new_status == "confirmed":
        return NFL_RESPOND_PAGE.format(icon="\U0001F389", heading="Seat released — thanks!",
                                        body="The fan has been sent their gate pass. Your stadium credit has been added.")
    return NFL_RESPOND_PAGE.format(icon="\U0001F44D", heading="Got it, seat kept",
                                    body="We let the fan know it's not available.")


@app.route("/webhooks/twilio/sms", methods=["POST"])
def twilio_sms_webhook():
    from_phone = (request.form.get("From") or "").strip()
    body_text  = (request.form.get("Body") or "").strip().upper()

    sb = get_supabase()

    if handle_nfl_sms_reply(sb, from_phone, body_text):
        return Response("<Response></Response>", mimetype="text/xml")

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
