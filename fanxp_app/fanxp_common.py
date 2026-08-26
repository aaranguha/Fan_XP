"""
fanxp_common.py

Shared Supabase/Twilio helpers used by fanxp_ping_sth.py (CLI) and
fanxp_api.py (fan-facing web API).
"""

import os
from dotenv import load_dotenv

load_dotenv()

CREDIT_OFFER = 25  # stadium credit offered per surrendered seat
NFL_OFFER_WINDOW_MINUTES = 30  # matches the "expires in 30 minutes" text in the seller SMS

TWILIO_DRY_RUN = os.getenv("TWILIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


class _DryRunMessage:
    """Stand-in for a Twilio Message when TWILIO_DRY_RUN is on."""
    def __init__(self):
        import secrets
        self.sid = f"DRYRUN-{secrets.token_hex(8)}"


def send_or_log_sms(twilio, to_phone, body):
    """
    Sends via Twilio normally, or — when TWILIO_DRY_RUN is set — just logs
    the message and returns a fake Message-like object with a `.sid`. Used
    while real delivery is blocked (e.g. A2P 10DLC unregistered on a trial
    account) so the rest of the request/response loop can still be tested.
    """
    if TWILIO_DRY_RUN:
        print(f"[TWILIO_DRY_RUN] to={to_phone}\n{body}\n")
        return _DryRunMessage()

    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    return twilio.messages.create(body=body, from_=from_number, to=to_phone)


def get_supabase():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env")
    return create_client(url, key)


def get_twilio():
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not sid or not token:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set in .env")
    return Client(sid, token)


def send_surrender_sms(twilio, sth, seat, game, credit_offer=CREDIT_OFFER):
    """
    Texts an STH asking them to surrender one seat.
    `sth` needs name/phone, `seat` needs section/row/seat, `game` needs arena.
    Returns the Twilio Message object.
    """
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    sec, row, num = seat["section"], seat["row"], seat["seat"]

    sms_body = (
        f"Hi {sth['name'].split()[0]}! \U0001F44B This is Fan XP at {game.get('arena', 'the stadium')}.\n\n"
        f"We noticed Sec {sec} · Row {row} · Seat {num} may be empty for tonight's game.\n\n"
        f"Reply YES to surrender it for ${credit_offer} in stadium credit.\n"
        f"Reply NO to keep it.\n\n"
        f"Offer expires in 30 minutes."
    )

    return twilio.messages.create(
        body=sms_body,
        from_=from_number,
        to=sth["phone"],
    )


def send_nfl_seller_sms(twilio, seller, req, api_base_url, credit_offer=CREDIT_OFFER):
    """
    Texts a simulated NFL seat seller asking them to confirm the release,
    with tap-to-confirm links (no reply needed). `seller` needs name/phone,
    `req` needs id/team_slug/section/row_label/seat_num. `api_base_url` is
    the public URL the API is reachable at (e.g. from request.url_root),
    used to build the /nfl/respond links. Returns the Twilio Message object.
    """
    base = api_base_url.rstrip("/")
    yes_link = f"{base}/nfl/respond/{req['id']}?decision=YES"
    no_link  = f"{base}/nfl/respond/{req['id']}?decision=NO"

    sms_body = (
        f"Hi {seller['name'].split()[0]}! \U0001F44B This is Fan XP.\n\n"
        f"A fan wants your empty seat for the 2nd half: "
        f"Sec {req['section']} · Row {req['row_label']} · Seat {req['seat_num']}.\n\n"
        f"Tap to release it for ${credit_offer} in stadium credit:\n{yes_link}\n\n"
        f"Tap to keep it:\n{no_link}\n\n"
        f"Offer expires in 30 minutes."
    )

    return send_or_log_sms(twilio, seller["phone"], sms_body)
