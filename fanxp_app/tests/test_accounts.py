"""
Tests for fanxp_accounts.py (accounts, buyer/seller pathways, per-game seat
data) and its hooks into fanxp_api's claim flow. Runs against an in-memory
fake Supabase client; no network or credentials.

What matters most here:
  - A claim for a real game is priced server-side (seller release beats a
    no-show; an unlisted seat is refused), never from the browser.
  - A seat a seller registered routes the request to that seller.
  - Seller endpoints only ever touch the signed-in seller's own rows.
  - The seat-data endpoint pages past PostgREST's 1000-row cap.
"""

from types import SimpleNamespace

import pytest

import fanxp_accounts
import fanxp_api


# ── In-memory Supabase fake ──────────────────────────────────────────────────

class Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.mode = db, table, [], "select"
        self.payload, self._range, self._count = None, None, False

    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def select(self, cols="*", count=None):
        self._count = count == "exact"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.mode, self.payload, self.conflict = "upsert", payload, on_conflict.split(",")
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, k, v):
        self.f.append((k, lambda x, v=v: str(x) == str(v)))
        return self

    def neq(self, k, v):
        self.f.append((k, lambda x, v=v: x != v))
        return self

    def gte(self, k, v):
        self.f.append((k, lambda x, v=v: str(x) >= str(v)))
        return self

    def in_(self, k, vs):
        self.f.append((k, lambda x, vs=vs: x in vs))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def _join(self, r):
        if self.table == "seat_releases":
            seat = next((s for s in self.db.tables.get("seller_seats", []) if s["id"] == r["seller_seat_id"]), {})
            return {**r, "seller_seats": seat}
        return r

    def _get(self, r, k):
        if "." in k:
            a, b = k.split(".", 1)
            return (r.get(a) or {}).get(b)
        return r.get(k)

    def _match(self):
        rows = [self._join(r) for r in self._rows()]
        return [r for r in rows if all(fn(self._get(r, k)) for k, fn in self.f)]

    def execute(self):
        if self.mode in ("insert", "upsert"):
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for item in items:
                if self.mode == "upsert":
                    hit = next((r for r in self._rows() if all(r.get(c) == item.get(c) for c in self.conflict)), None)
                    if hit:
                        hit.update(item)
                        out.append(hit)
                        continue
                row = {"id": len(self._rows()) + 1, "created_at": "2026-10-14T00:00:00Z", **item}
                self._rows().append(row)
                out.append(row)
            return SimpleNamespace(data=out, count=None)
        matched = self._match()
        ids = {r["id"] for r in matched}
        if self.mode == "update":
            for r in self._rows():
                if r["id"] in ids:
                    r.update(self.payload)
            return SimpleNamespace(data=[r for r in self._rows() if r["id"] in ids], count=None)
        if self.mode == "delete":
            self.db.tables[self.table] = [r for r in self._rows() if r["id"] not in ids]
            return SimpleNamespace(data=matched, count=None)
        if self._range:
            matched = matched[self._range[0]: self._range[1] + 1]
        return SimpleNamespace(data=matched, count=len(matched) if self._count else None)


class FakeAuth:
    def __init__(self, users):
        self.users = users
        self.admin = self

    def get_user(self, token):
        user = self.users.get(token)
        if not user:
            raise RuntimeError("invalid JWT")
        return SimpleNamespace(user=user)

    def get_user_by_id(self, uid):
        return SimpleNamespace(user=next(u for u in self.users.values() if u.id == uid))


class FakeDB:
    def __init__(self, users=None):
        self.tables = {}
        self.auth = FakeAuth(users or {})

    def table(self, name):
        return Q(self, name)


def make_user(uid, roles, phone="+15555550100", name="Sam Seller"):
    return SimpleNamespace(id=uid, email=f"{uid}@x.com",
                           user_metadata={"full_name": name, "phone": phone, "roles": roles})


@pytest.fixture
def db(monkeypatch):
    users = {
        "tok-seller": make_user("u-seller", ["seller"]),
        "tok-buyer": make_user("u-buyer", ["buyer"], phone="+15555550199", name="Bea Buyer"),
    }
    fake = FakeDB(users)
    monkeypatch.setattr(fanxp_accounts, "get_supabase", lambda: fake)
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: fake)
    monkeypatch.setattr(fanxp_accounts, "today_et", lambda: "2026-10-14")
    return fake


def call(method, path, token=None, json=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = fanxp_api.app.test_client()
    resp = client.open(path, method=method, json=json, headers=headers)
    return resp.status_code, resp.get_json()


# ── Pure helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("(415) 555-0100", "+14155550100"),
    ("1-415-555-0100", "+14155550100"),
    ("+44 20 7946 0958", "+442079460958"),
    ("555-0100", None),
])
def test_normalize_phone(raw, want):
    assert fanxp_accounts.normalize_phone(raw) == want


def test_expand_seats_ranges_lists_and_labels():
    assert fanxp_accounts.expand_seats("5-8") == ["5", "6", "7", "8"]
    assert fanxp_accounts.expand_seats("5, 7,A") == ["5", "7", "A"]
    with pytest.raises(ValueError):
        fanxp_accounts.expand_seats("9-5")


def test_all_rows_pages_past_1000(db):
    db.tables["no_shows"] = [{"id": i, "game_id": 1} for i in range(2500)]
    rows = fanxp_accounts.all_rows(db.table("no_shows").select("*").eq("game_id", 1))
    assert len(rows) == 2500


# ── Auth gating ──────────────────────────────────────────────────────────────

def test_seller_routes_need_sign_in_and_seller_role(db):
    assert call("GET", "/api/seller/seats")[0] == 401
    assert call("GET", "/api/seller/seats", token="bogus")[0] == 401
    assert call("GET", "/api/seller/seats", token="tok-buyer")[0] == 403
    assert call("GET", "/api/seller/seats", token="tok-seller")[0] == 200


def test_register_requires_phone_for_sellers(db):
    code, body = call("POST", "/api/auth/register", json={
        "email": "a@b.com", "password": "longenough", "full_name": "A", "roles": ["seller"]})
    assert code == 400 and "phone" in body["error"]


# ── Seller seats and releases ────────────────────────────────────────────────

def test_add_seats_expands_range_and_blocks_double_registration(db):
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                      json={"team_slug": "giants", "section": "115", "row": "b", "seats": "5-6"})
    assert code == 201
    assert [(s["row"], s["seat"]) for s in body] == [("B", "5"), ("B", "6")]

    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                      json={"team_slug": "giants", "section": "115", "row": "B", "seats": "6"})
    assert code == 409


def test_cannot_release_someone_elses_seat(db):
    db.tables["seller_seats"] = [{"id": 1, "user_id": "someone-else", "team_slug": "giants",
                                  "section": "115", "row_label": "B", "seat_num": "5"}]
    code, _ = call("POST", "/api/seller/releases", token="tok-seller",
                   json={"seat_ids": [1], "game_date": "2026-10-18", "price": 80})
    assert code == 403


def test_release_rejects_past_dates(db):
    code, _ = call("POST", "/api/seller/releases", token="tok-seller",
                   json={"seat_ids": [1], "game_date": "2026-10-01", "price": 80})
    assert code == 400


# ── Per-game seat data ───────────────────────────────────────────────────────

def seed_game(db):
    db.tables["games"] = [{"id": 7, "league": "nfl", "home_team": "giants",
                           "game_date": "2026-10-18", "opponent": "New Orleans Saints"}]
    db.tables["no_shows"] = [
        {"id": 1, "game_id": 7, "section": "115", "row": "B", "seat": "5", "price_usd": 120.4},
        {"id": 2, "game_id": 7, "section": "115", "row": "B", "seat": "6", "price_usd": 118.0},
    ]
    db.tables["seller_seats"] = [{"id": 1, "user_id": "u-seller", "team_slug": "giants",
                                  "section": "115", "row_label": "B", "seat_num": "6"}]
    db.tables["seat_releases"] = [{"id": 1, "seller_seat_id": 1, "game_date": "2026-10-18",
                                   "price_usd": 90, "status": "available"}]


def test_seats_endpoint_merges_no_shows_and_releases(db):
    seed_game(db)
    code, body = call("GET", "/api/nfl/giants/seats?date=2026-10-18")
    assert code == 200
    seats = {(s[0], s[1], s[2]): (s[3], s[4]) for s in body["seats"]}
    assert seats[("115", "B", "5")] == (120, "no_show")
    assert seats[("115", "B", "6")] == (90, "release")


def test_games_endpoint_lists_game_with_counts(db):
    seed_game(db)
    code, body = call("GET", "/api/nfl/giants/games")
    assert code == 200
    assert body[0]["date"] == "2026-10-18"
    assert body[0]["no_shows"] == 2 and body[0]["releases"] == 1


# ── Claim flow hooks ─────────────────────────────────────────────────────────

def test_lookup_prefers_release_price_over_no_show(db):
    seed_game(db)
    hit = fanxp_api.lookup_open_seat(db, "giants", "2026-10-18", "115", "b", "6")
    assert hit == {"price": 90.0, "release_id": 1}
    hit = fanxp_api.lookup_open_seat(db, "giants", "2026-10-18", "115", "B", "5")
    assert hit == {"price": 120.4, "release_id": None}
    assert fanxp_api.lookup_open_seat(db, "giants", "2026-10-18", "115", "B", "99") is None


def test_claim_for_real_game_ignores_browser_price_and_routes_to_owner(db, monkeypatch):
    seed_game(db)
    session = SimpleNamespace(id="cs_1", url="https://checkout")
    fake_stripe = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(
        create=lambda **kw: (captured.update(kw), session)[1])))
    captured = {}
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    code, body = call("POST", "/api/nfl/giants/request", token="tok-buyer", json={
        "section": "115", "row": "B", "seat": "6", "price": 1, "game_date": "2026-10-18",
        "return_to_url": "https://fan-xp.vercel.app/nfl_giants_seatmap.html"})

    assert code == 200
    req = db.tables["nfl_seat_requests"][0]
    assert req["price_usd"] == 90.0 and req["release_id"] == 1
    assert req["buyer_user_id"] == "u-buyer" and req["fan_name"] == "Bea Buyer"
    seller = next(s for s in db.tables["nfl_sellers"] if s["id"] == req["seller_id"])
    assert seller["user_id"] == "u-seller"
    assert captured["line_items"][0]["price_data"]["unit_amount"] == round(90 * 1.12 * 100)


def test_claim_for_seat_not_open_is_refused(db):
    seed_game(db)
    code, _ = call("POST", "/api/nfl/giants/request", json={
        "section": "300", "row": "A", "seat": "1", "game_date": "2026-10-18",
        "fan_name": "X", "fan_phone": "+15555550111", "return_to_url": "https://x"})
    assert code == 409


def test_payout_is_release_price_or_flat_credit():
    assert fanxp_api.seller_payout({"release_id": 3, "price_usd": "90.00"}) == 90.0
    assert fanxp_api.seller_payout({"release_id": None, "price_usd": "120"}) == fanxp_api.CREDIT_OFFER


def test_checkout_failure_returns_reason_and_removes_the_request(db, monkeypatch):
    seed_game(db)
    monkeypatch.setenv("STH_PHONE", "+15555550123")  # seat 5 has no owner -> demo seller

    def boom(**kw):
        raise RuntimeError("Received unknown parameter: managed_payments")
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: SimpleNamespace(
        checkout=SimpleNamespace(Session=SimpleNamespace(create=boom))))

    code, body = call("POST", "/api/nfl/giants/request", token="tok-buyer", json={
        "section": "115", "row": "B", "seat": "5", "game_date": "2026-10-18",
        "return_to_url": "https://x"})

    assert code == 502 and "managed_payments" in body["error"]
    assert db.tables.get("nfl_seat_requests") == []


def test_google_start_redirects_only_back_to_our_account_page(db, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    client = fanxp_api.app.test_client()
    ok = client.get("/api/auth/google?redirect_to=https%3A%2F%2Ffan-xp.vercel.app%2Faccount.html%3Fnext%3Dbuy.html")
    assert ok.status_code == 302
    assert ok.headers["Location"].startswith("https://proj.supabase.co/auth/v1/authorize?provider=google&redirect_to=https%3A%2F%2Ffan-xp.vercel.app%2Faccount.html")
    assert client.get("/api/auth/google?redirect_to=https%3A%2F%2Fevil.example%2Faccount.html").status_code == 400
    assert client.get("/api/auth/google?redirect_to=https%3A%2F%2Ffan-xp.vercel.app%2Fother.html").status_code == 400


def test_google_user_without_roles_needs_setup():
    google_user = SimpleNamespace(id="g1", email="g@x.com", user_metadata={"name": "Gia Google", "avatar_url": "x"})
    prof = fanxp_accounts.profile_of(google_user)
    assert prof["needs_setup"] is True and prof["full_name"] == "Gia Google"
    assert fanxp_accounts.profile_of(make_user("u", ["buyer"]))["needs_setup"] is False
