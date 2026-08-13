"""
fanxp_common.py

Shared Supabase/Twilio helpers used by fanxp_ping_sth.py (CLI) and
fanxp_api.py (fan-facing web API).
"""

import os
from dotenv import load_dotenv

load_dotenv()

CREDIT_OFFER = 25  # stadium credit offered per surrendered seat


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
