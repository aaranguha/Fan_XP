"""
fanxp_accounts.py — Fan XP accounts, the buyer/seller pathways, and the
live per-game seat data the NFL seat maps load.

Accounts are Supabase Auth users, created and signed in through this API
with the service-role client, so the browser never holds a Supabase key.
Name, phone and roles ("buyer", "seller") live in the user's
user_metadata. Signed-in calls send `Authorization: Bearer <access_token>`.

  GET    /api/auth/google?redirect_to=   start "Continue with Google"
  POST   /api/auth/register              create an account and sign in
  POST   /api/auth/login                 email + password -> session
  POST   /api/auth/refresh               refresh_token -> new session
  GET    /api/me                         profile
  PATCH  /api/me                         update name / phone / roles
  GET    /api/me/requests                buyer: my seat requests

  GET    /api/seller/seats               seller: seats I hold
  POST   /api/seller/seats               add seats (e.g. seats "5-8")
  DELETE /api/seller/seats/<id>
  GET    /api/seller/releases            seats I've put up for a game
  POST   /api/seller/releases            release seats for a game date at a price
  DELETE /api/seller/releases/<id>       withdraw (only while still available)
  GET    /api/seller/requests            fans' requests for my seats
  POST   /api/seller/requests/<id>/respond   YES / NO, same as the SMS link

  GET    /api/nfl/<slug>/games           games with seat data, newest first
  GET    /api/nfl/<slug>/seats?date=     that game's open seats: confirmed
                                         no-shows plus seller releases

Tables: see migrations/2026-10-14_accounts.sql.
"""

import os
import re
from datetime import datetime
from functools import wraps
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, redirect, request

from fanxp_common import get_supabase

bp = Blueprint("accounts", __name__)

EASTERN = ZoneInfo("America/New_York")
ROLES = {"buyer", "seller"}

# Where "Continue with Google" may send people back to. Supabase also checks
# its own allow-list (Authentication > URL Configuration > Redirect URLs).
OAUTH_RETURN_HOSTS = {"fan-xp.vercel.app", "localhost", "127.0.0.1"}

# The 32 slugs the seat maps use (docs/nfl_{slug}_seatmap.html). Mirrors
# scraping/nfl_teams.py, which this service can't import (Render deploys
# fanxp_app/ on its own).
NFL_SLUGS = {
    "49ers", "bears", "bengals", "bills", "broncos", "browns", "buccaneers",
    "cardinals", "chargers", "chiefs", "colts", "commanders", "cowboys",
    "dolphins", "eagles", "falcons", "giants", "jaguars", "jets", "lions",
    "packers", "panthers", "patriots", "raiders", "rams", "ravens", "saints",
    "seahawks", "steelers", "texans", "titans", "vikings",
}

# Filled in by register(): request-flow helpers that live in fanxp_api.py.
_api = {}


# ── Small helpers ────────────────────────────────────────────────────────────

def today_et() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def norm(s) -> str:
    return str(s or "").strip().upper()


def seat_key(section, row, seat) -> tuple:
    return (norm(section), norm(row), norm(seat))


def normalize_phone(raw: str) -> str | None:
    """US numbers to E.164 (+1XXXXXXXXXX); anything already +-prefixed is
    kept as digits. Returns None if it isn't a plausible number."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def expand_seats(spec: str) -> list[str]:
    """'5-8' -> ['5','6','7','8'], '5, 7' -> ['5','7'], '12' -> ['12'].
    Non-numeric seat labels (e.g. 'A') pass through as-is."""
    out = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi or hi - lo >= 40:
                raise ValueError(f"seat range '{part}' doesn't look right")
            out.extend(str(n) for n in range(lo, hi + 1))
        else:
            out.append(part)
    return out


def all_rows(query, page_size: int = 1000) -> list[dict]:
    """PostgREST caps a response at 1000 rows; a big game has ~3000
    no-shows, so page through with .range()."""
    rows, start = [], 0
    while True:
        batch = query.range(start, start + page_size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def profile_of(user) -> dict:
    meta = getattr(user, "user_metadata", None) or {}
    return {
        "id":        user.id,
        "email":     user.email,
        "full_name": meta.get("full_name") or meta.get("name") or "",
        "phone":     meta.get("phone", ""),
        "roles":     [r for r in meta.get("roles", ["buyer"]) if r in ROLES],
        # A Google sign-up arrives with only Google's name/email/photo; they
        # still need to pick buying/selling (and a phone, to sell).
        "needs_setup": "roles" not in meta,
    }


def session_payload(session, user) -> dict:
    return {
        "access_token":  session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at":    session.expires_at,
        "user":          profile_of(user),
    }


def _auth_client():
    """A throwaway client for sign-in/refresh: those calls store the user's
    session on the client they're made with, which must never be the
    service-role client used for table access."""
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL", "").strip(),
                         os.getenv("SUPABASE_SERVICE_KEY", "").strip())


# ── Auth ─────────────────────────────────────────────────────────────────────

def current_user():
    """The signed-in user for this request, or None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        resp = get_supabase().auth.get_user(header[7:].strip())
    except Exception:
        return None
    return getattr(resp, "user", None)


def require_user(role: str | None = None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"error": "Please sign in again."}), 401
            g.user = user
            g.profile = profile_of(user)
            if role and role not in g.profile["roles"]:
                return jsonify({"error": f"Turn on the {role} option in your account first."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return deco


def _validated_profile(body: dict, partial: bool = False) -> tuple[dict, str | None]:
    out = {}
    if "full_name" in body or not partial:
        name = (body.get("full_name") or "").strip()
        if not name:
            return {}, "Please enter your name."
        out["full_name"] = name[:80]
    if "roles" in body or not partial:
        roles = body.get("roles") or []
        if not isinstance(roles, list) or not roles or not set(roles) <= ROLES:
            return {}, "Choose buying, selling, or both."
        out["roles"] = sorted(set(roles))
    if "phone" in body or not partial:
        raw = (body.get("phone") or "").strip()
        phone = normalize_phone(raw) if raw else ""
        if raw and not phone:
            return {}, "That phone number doesn't look right."
        out["phone"] = phone
    return out, None


@bp.route("/api/auth/google")
def google_start():
    """Sends the browser to Supabase's Google sign-in. Supabase sends it back
    to `redirect_to` with the session in the URL fragment (#access_token=...),
    which account.html reads; from there it's the same session as an email
    sign-in, so /api/me, refresh, etc. all work unchanged."""
    back = (request.args.get("redirect_to") or "").strip()
    parsed = urlparse(back)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in OAUTH_RETURN_HOSTS \
            or not parsed.path.endswith("/account.html"):
        return jsonify({"error": "bad redirect_to"}), 400
    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    return redirect(f"{base}/auth/v1/authorize?provider=google&redirect_to={quote(back, safe='')}")


@bp.route("/api/auth/register", methods=["POST"])
def register():
    body = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"error": "Please enter a valid email."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password needs at least 8 characters."}), 400
    meta, error = _validated_profile(body)
    if error:
        return jsonify({"error": error}), 400
    if "seller" in meta["roles"] and not meta["phone"]:
        return jsonify({"error": "Sellers need a phone number so we can text you when a fan wants your seat."}), 400

    sb = get_supabase()
    try:
        sb.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": meta,
        })
    except Exception as e:
        msg = str(e).lower()
        if "already" in msg or "exists" in msg or "registered" in msg:
            return jsonify({"error": "An account with that email already exists. Try signing in."}), 409
        return jsonify({"error": "Couldn't create the account. Please try again."}), 502

    auth = _auth_client().auth.sign_in_with_password({"email": email, "password": password})
    return jsonify(session_payload(auth.session, auth.user)), 201


@bp.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    try:
        auth = _auth_client().auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        return jsonify({"error": "Wrong email or password."}), 401
    return jsonify(session_payload(auth.session, auth.user))


@bp.route("/api/auth/refresh", methods=["POST"])
def refresh():
    token = ((request.get_json(force=True) or {}).get("refresh_token") or "").strip()
    if not token:
        return jsonify({"error": "refresh_token is required"}), 400
    try:
        auth = _auth_client().auth.refresh_session(token)
    except Exception:
        return jsonify({"error": "Please sign in again."}), 401
    return jsonify(session_payload(auth.session, auth.user))


@bp.route("/api/me")
@require_user()
def me():
    out = dict(g.profile)
    if "seller" in out["roles"]:
        rows = (
            get_supabase().table("nfl_sellers").select("credit_balance")
              .eq("user_id", g.user.id).execute()
        ).data or []
        out["credit_balance"] = round(sum(float(r["credit_balance"] or 0) for r in rows), 2)
    return jsonify(out)


@bp.route("/api/me", methods=["PATCH"])
@require_user()
def update_me():
    changes, error = _validated_profile(request.get_json(force=True) or {}, partial=True)
    if error:
        return jsonify({"error": error}), 400
    merged = {**{k: g.profile[k] for k in ("full_name", "phone", "roles")}, **changes}
    if "seller" in merged["roles"] and not merged["phone"]:
        return jsonify({"error": "Sellers need a phone number so we can text you when a fan wants your seat."}), 400
    resp = get_supabase().auth.admin.update_user_by_id(g.user.id, {"user_metadata": merged})
    return jsonify(profile_of(resp.user))


def _request_view(row: dict) -> dict:
    view = {
        "id":        row["id"],
        "team_slug": row["team_slug"],
        "section":   row["section"],
        "row":       row["row_label"],
        "seat":      row["seat_num"],
        "price":     float(row["price_usd"]),
        "status":    row["status"],
        "game_date": row.get("game_date"),
        "created_at": row["created_at"],
    }
    if row["status"] == "confirmed" and row.get("pass_code"):
        view["pass_code"] = row["pass_code"]
    return view


@bp.route("/api/me/requests")
@require_user()
def my_requests():
    sb = get_supabase()
    rows = (
        sb.table("nfl_seat_requests").select("*")
          .eq("buyer_user_id", g.user.id)
          .order("id", desc=True).limit(50).execute()
    ).data or []
    return jsonify([_request_view(_api["expire_if_stale"](sb, r)) for r in rows])


# ── Seller: seats ────────────────────────────────────────────────────────────

def _seat_view(r: dict) -> dict:
    return {"id": r["id"], "team_slug": r["team_slug"], "section": r["section"],
            "row": r["row_label"], "seat": r["seat_num"]}


@bp.route("/api/seller/seats")
@require_user("seller")
def list_seller_seats():
    rows = (
        get_supabase().table("seller_seats").select("*")
          .eq("user_id", g.user.id).order("team_slug").order("section")
          .order("row_label").execute()
    ).data or []
    return jsonify([_seat_view(r) for r in rows])


@bp.route("/api/seller/seats", methods=["POST"])
@require_user("seller")
def add_seller_seats():
    body = request.get_json(force=True) or {}
    slug = (body.get("team_slug") or "").strip().lower()
    section = norm(body.get("section"))
    row_label = norm(body.get("row"))
    if slug not in NFL_SLUGS:
        return jsonify({"error": "Pick your team."}), 400
    if not section or not row_label:
        return jsonify({"error": "Section and row are required."}), 400
    try:
        seats = [norm(s) for s in expand_seats(body.get("seats"))]
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not seats:
        return jsonify({"error": "Add at least one seat number."}), 400

    sb = get_supabase()
    taken = (
        sb.table("seller_seats").select("seat_num")
          .eq("team_slug", slug).eq("section", section).eq("row_label", row_label)
          .in_("seat_num", seats).execute()
    ).data or []
    if taken:
        nums = ", ".join(sorted({t["seat_num"] for t in taken}))
        return jsonify({"error": f"Seat {nums} in Sec {section} Row {row_label} is already registered to an account."}), 409

    rows = sb.table("seller_seats").insert([
        {"user_id": g.user.id, "team_slug": slug, "section": section,
         "row_label": row_label, "seat_num": s}
        for s in seats
    ]).execute().data or []
    return jsonify([_seat_view(r) for r in rows]), 201


@bp.route("/api/seller/seats/<int:seat_id>", methods=["DELETE"])
@require_user("seller")
def delete_seller_seat(seat_id):
    deleted = (
        get_supabase().table("seller_seats").delete()
          .eq("id", seat_id).eq("user_id", g.user.id).execute()
    ).data
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": seat_id})


# ── Seller: releases (seats put up for a specific game) ──────────────────────

def _release_view(r: dict) -> dict:
    seat = r.get("seller_seats") or {}
    return {"id": r["id"], "game_date": r["game_date"], "price": float(r["price_usd"]),
            "status": r["status"], "seat_id": r["seller_seat_id"],
            "team_slug": seat.get("team_slug"), "section": seat.get("section"),
            "row": seat.get("row_label"), "seat": seat.get("seat_num")}


RELEASE_SELECT = "*, seller_seats!inner(user_id, team_slug, section, row_label, seat_num)"


@bp.route("/api/seller/releases")
@require_user("seller")
def list_releases():
    rows = (
        get_supabase().table("seat_releases").select(RELEASE_SELECT)
          .eq("seller_seats.user_id", g.user.id)
          .gte("game_date", today_et())
          .order("game_date").execute()
    ).data or []
    return jsonify([_release_view(r) for r in rows])


@bp.route("/api/seller/releases", methods=["POST"])
@require_user("seller")
def create_releases():
    body = request.get_json(force=True) or {}
    seat_ids = body.get("seat_ids") or []
    game_date = (body.get("game_date") or "").strip()
    try:
        price = round(float(body.get("price")), 2)
    except (TypeError, ValueError):
        return jsonify({"error": "Set a price."}), 400
    if price <= 0 or price > 5000:
        return jsonify({"error": "Price should be between $1 and $5,000."}), 400
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", game_date) or game_date < today_et():
        return jsonify({"error": "Pick an upcoming game date."}), 400
    if not isinstance(seat_ids, list) or not seat_ids:
        return jsonify({"error": "Pick at least one of your seats."}), 400

    sb = get_supabase()
    mine = (
        sb.table("seller_seats").select("id").eq("user_id", g.user.id)
          .in_("id", seat_ids).execute()
    ).data or []
    if len(mine) != len(set(seat_ids)):
        return jsonify({"error": "Some of those seats aren't on your account."}), 403

    sb.table("seat_releases").upsert(
        [{"seller_seat_id": s["id"], "game_date": game_date, "price_usd": price, "status": "available"}
         for s in mine],
        on_conflict="seller_seat_id,game_date",
    ).execute()
    rows = (
        sb.table("seat_releases").select(RELEASE_SELECT)
          .eq("seller_seats.user_id", g.user.id).eq("game_date", game_date)
          .in_("seller_seat_id", [s["id"] for s in mine]).execute()
    ).data or []
    return jsonify([_release_view(r) for r in rows]), 201


@bp.route("/api/seller/releases/<int:release_id>", methods=["DELETE"])
@require_user("seller")
def withdraw_release(release_id):
    sb = get_supabase()
    rows = (
        sb.table("seat_releases").select(RELEASE_SELECT)
          .eq("id", release_id).eq("seller_seats.user_id", g.user.id).execute()
    ).data
    if not rows:
        return jsonify({"error": "not found"}), 404
    if rows[0]["status"] != "available":
        return jsonify({"error": f"This seat is already {rows[0]['status']}."}), 409
    sb.table("seat_releases").update({"status": "withdrawn"}).eq("id", release_id).execute()
    return jsonify({"id": release_id, "status": "withdrawn"})


# ── Seller: incoming requests ────────────────────────────────────────────────

def _my_seller_ids(sb, user_id) -> list[int]:
    rows = sb.table("nfl_sellers").select("id").eq("user_id", user_id).execute().data or []
    return [r["id"] for r in rows]


@bp.route("/api/seller/requests")
@require_user("seller")
def seller_requests():
    sb = get_supabase()
    ids = _my_seller_ids(sb, g.user.id)
    if not ids:
        return jsonify([])
    rows = (
        sb.table("nfl_seat_requests").select("*").in_("seller_id", ids)
          .neq("status", "requested")  # still at checkout: not a real request yet
          .order("id", desc=True).limit(50).execute()
    ).data or []
    out = []
    for r in rows:
        view = _request_view(_api["expire_if_stale"](sb, r))
        view.pop("pass_code", None)
        view["fan_first_name"] = (r.get("fan_name") or "").split(" ")[0]
        out.append(view)
    return jsonify(out)


@bp.route("/api/seller/requests/<int:request_id>/respond", methods=["POST"])
@require_user("seller")
def seller_respond(request_id):
    sb = get_supabase()
    ids = _my_seller_ids(sb, g.user.id)
    owned = ids and (
        sb.table("nfl_seat_requests").select("id").eq("id", request_id)
          .in_("seller_id", ids).execute()
    ).data
    if not owned:
        return jsonify({"error": "not found"}), 404
    decision = ((request.get_json(force=True) or {}).get("decision") or "").strip().upper()
    new_status, error = _api["apply_nfl_response"](request_id, decision)
    if error:
        return jsonify({"error": error}), 409 if "awaiting" in error else 400
    if new_status == "capture_failed":
        return jsonify({"error": "Payment capture failed, please try again."}), 502
    return jsonify({"request_id": request_id, "status": new_status})


# ── Seller lookup used by the claim flow ─────────────────────────────────────

def find_seat_owner(sb, slug, section, row, seat) -> dict | None:
    """The seller_seats row for this physical seat, if a seller registered it."""
    rows = (
        sb.table("seller_seats").select("*")
          .eq("team_slug", slug).eq("section", norm(section))
          .eq("row_label", norm(row)).eq("seat_num", norm(seat)).limit(1).execute()
    ).data
    return rows[0] if rows else None


def get_or_create_account_seller(sb, user_id, slug) -> dict:
    """The nfl_sellers row a request points at for a real seller account,
    carrying their current name/phone so the existing SMS loop reaches them."""
    user = sb.auth.admin.get_user_by_id(user_id).user
    prof = profile_of(user)
    fields = {"name": prof["full_name"] or "Fan XP seller", "phone": prof["phone"]}
    existing = (
        sb.table("nfl_sellers").select("*").eq("user_id", user_id).eq("team_slug", slug)
          .limit(1).execute()
    ).data
    if existing:
        if any(existing[0].get(k) != v for k, v in fields.items()):
            sb.table("nfl_sellers").update(fields).eq("id", existing[0]["id"]).execute()
            existing[0].update(fields)
        return existing[0]
    return (
        sb.table("nfl_sellers").insert({**fields, "user_id": user_id, "team_slug": slug,
                                        "section": "(account)"}).execute()
    ).data[0]


# ── Public per-game seat data for the seat maps ──────────────────────────────

def _available_releases(query_fn) -> list[dict]:
    """Seller releases, or [] if the accounts migration hasn't been applied
    yet, so the public seat-map endpoints keep serving no-shows either way."""
    try:
        return query_fn().execute().data or []
    except Exception:
        return []


def _games_for(sb, slug) -> dict:
    rows = (
        sb.table("games").select("id, game_date, opponent")
          .eq("league", "nfl").eq("home_team", slug).execute()
    ).data or []
    return {str(r["game_date"]): r for r in rows}


def open_seats_for(sb, slug, game_date) -> dict:
    """{(section,row,seat): {price, source, release_id?}} for one game:
    confirmed no-shows from that game's halftime scrape, overridden by any
    seat a seller has released for it at their own price."""
    seats = {}
    game = _games_for(sb, slug).get(game_date)
    if game:
        for r in all_rows(
            sb.table("no_shows").select("section, row, seat, price_usd")
              .eq("game_id", game["id"]).order("id")
        ):
            if r.get("seat") in (None, ""):
                continue
            price = r.get("price_usd")
            seats[seat_key(r["section"], r["row"], r["seat"])] = {
                "price": round(float(price)) if price not in (None, "") else None,
                "source": "no_show",
            }
    for r in _available_releases(lambda: (
        sb.table("seat_releases").select(RELEASE_SELECT)
          .eq("seller_seats.team_slug", slug).eq("game_date", game_date)
          .eq("status", "available")
    )):
        s = r["seller_seats"]
        seats[seat_key(s["section"], s["row_label"], s["seat_num"])] = {
            "price": round(float(r["price_usd"])), "source": "release", "release_id": r["id"],
        }
    return seats


@bp.route("/api/nfl/<slug>/games")
def nfl_games(slug):
    if slug not in NFL_SLUGS:
        return jsonify({"error": "unknown team"}), 404
    sb = get_supabase()
    today = today_et()
    games = _games_for(sb, slug)
    out = {}
    for date, gm in games.items():
        # 9/13's Steelers/Raiders runs scraped paid add-on pages (club pass,
        # hotel package) instead of the game; never offer those as a game.
        if any(m in (gm.get("opponent") or "").lower() for m in ("not a game ticket", "hotel package", "club pass")):
            continue
        n = (
            sb.table("no_shows").select("id", count="exact")
              .eq("game_id", gm["id"]).limit(1).execute()
        ).count or 0
        if n or date >= today:
            out[date] = {"date": date, "opponent": gm.get("opponent") or "",
                         "no_shows": n, "releases": 0}
    for r in _available_releases(lambda: (
        sb.table("seat_releases").select("game_date, seller_seats!inner(team_slug)")
          .eq("seller_seats.team_slug", slug).eq("status", "available")
          .gte("game_date", today)
    )):
        d = str(r["game_date"])
        out.setdefault(d, {"date": d, "opponent": "", "no_shows": 0, "releases": 0})
        out[d]["releases"] += 1
    for d in out.values():
        d["live"] = d["date"] == today
    resp = jsonify(sorted(out.values(), key=lambda d: d["date"], reverse=True))
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/api/nfl/<slug>/seats")
def nfl_seats(slug):
    date = (request.args.get("date") or "").strip()
    if slug not in NFL_SLUGS or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return jsonify({"error": "team and date=YYYY-MM-DD required"}), 400
    seats = open_seats_for(get_supabase(), slug, date)
    resp = jsonify({
        "date": date,
        # compact: [section, row, seat, price, source]
        "seats": [[k[0], k[1], k[2], v["price"], v["source"]] for k, v in seats.items()],
    })
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


def register(app, expire_if_stale, apply_nfl_response):
    _api["expire_if_stale"] = expire_if_stale
    _api["apply_nfl_response"] = apply_nfl_response
    app.register_blueprint(bp)
