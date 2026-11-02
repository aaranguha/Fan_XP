"""
Tests for the write-side of fanxp_api.py's generic/demo flow (CLAUDE.md
section 6, point 1): POST /api/seats/<id>/request (request_seat()) and
POST /webhooks/twilio/sms (twilio_sms_webhook()).

test_demo_seat_flow.py already covers this flow's read-side (demo_game_id(),
find_price(), latest_surrender(), GET /api/empty-seats, GET /api/requests/<id>)
and explicitly left the write-side for later. This is that later: both routes
had zero test coverage before this file, despite being live code shipped in
fanxp_api.py (deployed on Render) and reachable in production.

twilio_sms_webhook() is the more interesting of the two: it still contains a
full single-bid "auction" (insert into `auctions`/`bids`, close with a
winning_fan_id) for the legacy demo flow. CLAUDE.md section 2's standing
decision is "no auction/bidding" for the real product -- that auction system
was built, reverted, and must not be reintroduced -- but this old demo-only
code path was apparently never cleaned up and is still live. These tests
document its current behavior as a regression net (so nobody accidentally
makes it worse while touching this file), not an endorsement of reintroducing
auction logic anywhere else.

The other real behavior worth protecting: handle_nfl_sms_reply() is tried
first and, per its own docstring, "both flows can share the same test phone
number (STH_PHONE) during a demo" -- so when it reports the reply as handled,
the legacy season-ticket-holder/auction logic below it must never run, even
if a season_ticket_holders row with a matching phone and a pending surrender
also exists.

Driven via app.test_client() against a minimal fake Supabase client (same
shape as test_demo_seat_flow.py's), with send_surrender_sms/get_twilio
stubbed at the same orchestration boundary used throughout this test package
(see test_ping_nfl_seller.py) -- no real network access or credentials
needed.
"""

from types import SimpleNamespace

import pytest

import fanxp_api


# ── Fake Supabase client (select/insert/update/eq/order/limit/single) ───────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._limit = None
        self._order = None
        self._desc = False
        self._mode = "select"
        self._payload = None
        self._single = False

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def order(self, field, desc=False):
        self._order = field
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def _matching(self):
        rows = self.store.get(self.table_name, [])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self._order:
            out = sorted(out, key=lambda r: r[self._order], reverse=self._desc)
        return out

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            existing = self.store.get(self.table_name, [])
            if "id" not in row:
                ids = [r.get("id", 0) for r in existing]
                row["id"] = (max(ids) + 1) if ids else 1
            if self.table_name == "gate_passes" and "unique_pass_code" not in row:
                # Real schema generates this as a DB default; the app code
                # never sets it on insert, only reads it back afterwards.
                row["unique_pass_code"] = f"FANXP-{row['id']:04d}"
            self.store.setdefault(self.table_name, []).append(row)
            return FakeResult([row])

        if self._mode == "update":
            matching = self._matching()
            for r in matching:
                r.update(self._payload)
            return FakeResult(matching)

        matching = self._matching()
        limited = matching[: self._limit] if self._limit is not None else matching
        if self._single:
            return FakeResult(limited[0] if limited else None)
        return FakeResult(limited)


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


@pytest.fixture
def store():
    return {}


@pytest.fixture
def fake_supabase(store, monkeypatch):
    sb = FakeSupabase(store)
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    return sb


@pytest.fixture
def client():
    return fanxp_api.app.test_client()


# ── POST /api/seats/<id>/request (request_seat()) ───────────────────────────

def seed_demo_game(store, sth_id=10, sth_phone="+15551234567"):
    store["sth_seat_assignments"] = [
        {"seat_id": 1, "game_id": 7, "sth_id": sth_id,
         "seats": {"id": 1, "section": "112", "row": "10", "seat": "5"}},
    ]
    store["season_ticket_holders"] = [
        {"id": sth_id, "name": "Pat Jones", "phone": sth_phone},
    ]
    store["games"] = [
        {"id": 7, "opponent": "Rams", "game_date": "2026-09-13", "arena": "Levi's Stadium"},
    ]


@pytest.fixture
def fake_sms(monkeypatch):
    sent = []

    class FakeMessage:
        def __init__(self, sid):
            self.sid = sid

    def fake_send(twilio, sth, seat, game, credit_offer=fanxp_api.CREDIT_OFFER):
        sent.append({"twilio": twilio, "sth": sth, "seat": seat, "game": game,
                      "credit_offer": credit_offer})
        return FakeMessage(f"SM{len(sent)}")

    monkeypatch.setattr(fanxp_api, "send_surrender_sms", fake_send)
    monkeypatch.setattr(fanxp_api, "get_twilio", lambda: "fake-twilio-client")
    return sent


def test_missing_fan_name_returns_400(client, fake_supabase):
    resp = client.post("/api/seats/1/request", json={"fan_phone": "+15559990000"})
    assert resp.status_code == 400


def test_missing_fan_phone_returns_400(client, fake_supabase):
    resp = client.post("/api/seats/1/request", json={"fan_name": "Alex Fan"})
    assert resp.status_code == 400


def test_no_demo_game_seeded_returns_404(client, fake_supabase):
    # No sth_seat_assignments at all -> demo_game_id() returns None.
    resp = client.post("/api/seats/1/request",
                        json={"fan_name": "Alex Fan", "fan_phone": "+15559990000"})
    assert resp.status_code == 404


def test_seat_not_part_of_demo_game_returns_404(client, store, fake_supabase):
    seed_demo_game(store)
    # seat_id 999 was never assigned for the demo game.
    resp = client.post("/api/seats/999/request",
                        json={"fan_name": "Alex Fan", "fan_phone": "+15559990000"})
    assert resp.status_code == 404


def test_new_fan_is_created_when_phone_not_on_file(client, store, fake_supabase, fake_sms):
    seed_demo_game(store)

    resp = client.post("/api/seats/1/request",
                        json={"fan_name": "Alex Fan", "fan_phone": "+15559990000"})

    assert resp.status_code == 200
    fans = store["fans"]
    assert len(fans) == 1
    assert fans[0]["name"] == "Alex Fan"
    assert fans[0]["phone"] == "+15559990000"


def test_existing_fan_is_reused_by_phone_not_duplicated(client, store, fake_supabase, fake_sms):
    seed_demo_game(store)
    store["fans"] = [{"id": 42, "name": "Old Name", "phone": "+15559990000"}]

    resp = client.post("/api/seats/1/request",
                        json={"fan_name": "Alex Fan", "fan_phone": "+15559990000"})

    assert resp.status_code == 200
    # Still exactly one fan row for that phone -- no duplicate insert.
    assert len(store["fans"]) == 1
    surrender = store["seat_surrenders"][0]
    assert surrender["fan_id"] == 42


def test_successful_request_creates_detected_then_pings_sth(client, store, fake_supabase, fake_sms):
    seed_demo_game(store)

    resp = client.post("/api/seats/1/request",
                        json={"fan_name": "Alex Fan", "fan_phone": "+15559990000"})
    data = resp.get_json()

    assert resp.status_code == 200
    surrender = store["seat_surrenders"][0]
    # Final state is "sth_pinged" (started as "detected", then updated) and
    # carries the Twilio message sid back from send_surrender_sms().
    assert surrender["status"] == "sth_pinged"
    assert surrender["message_sid"] == "SM1"
    assert surrender["game_id"] == 7
    assert surrender["seat_id"] == 1
    assert surrender["sth_id"] == 10
    assert data == {"surrender_id": surrender["id"], "status": "sth_pinged"}

    # send_surrender_sms() was handed the right seat/game/sth.
    assert len(fake_sms) == 1
    assert fake_sms[0]["seat"]["section"] == "112"
    assert fake_sms[0]["game"]["opponent"] == "Rams"
    assert fake_sms[0]["sth"]["phone"] == "+15551234567"


# ── POST /webhooks/twilio/sms (twilio_sms_webhook()) ─────────────────────────

STH_PHONE = "+15551234567"


def seed_sth_with_pending_surrender(store, sth_id=10, fan_id=99, credit_balance=0.0):
    store["season_ticket_holders"] = [
        {"id": sth_id, "phone": STH_PHONE, "credit_balance": credit_balance},
    ]
    store["seat_surrenders"] = [
        {"id": 500, "sth_id": sth_id, "fan_id": fan_id, "status": "sth_pinged"},
    ]
    store["fans"] = [{"id": fan_id, "phone": "+15559990000"}]


@pytest.fixture
def fake_nfl_reply(monkeypatch):
    """Stubs handle_nfl_sms_reply() one level down so these tests cover only
    twilio_sms_webhook()'s own fallthrough logic. Defaults to "not our
    number" (False) so the legacy demo flow below it still runs; individual
    tests override this to exercise the short-circuit."""
    calls = []

    def fake_handle(sb, from_phone, body_text):
        calls.append((from_phone, body_text))
        return False

    monkeypatch.setattr(fanxp_api, "handle_nfl_sms_reply", fake_handle)
    return calls


@pytest.fixture
def fake_twilio_client(monkeypatch):
    sent = []

    class FakeMessages:
        @staticmethod
        def create(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(sid="SM-GATE-PASS")

    class FakeTwilioClient:
        messages = FakeMessages()

    monkeypatch.setattr(fanxp_api, "get_twilio", lambda: FakeTwilioClient())
    return sent


def post_sms(client, body, from_phone=STH_PHONE):
    return client.post("/webhooks/twilio/sms",
                        data={"From": from_phone, "Body": body})


def test_nfl_seller_reply_short_circuits_before_legacy_demo_logic(
    client, store, fake_supabase, fake_twilio_client, monkeypatch,
):
    # Simulate the documented shared-phone-number demo scenario: the NFL
    # flow claims this reply as handled even though a season_ticket_holders
    # row with the same phone and a pending surrender also exists.
    monkeypatch.setattr(fanxp_api, "handle_nfl_sms_reply", lambda sb, p, b: True)
    seed_sth_with_pending_surrender(store)

    resp = post_sms(client, "YES")

    assert resp.status_code == 200
    assert b"<Response></Response>" in resp.data
    # None of the legacy auction/credit side effects happened.
    assert store["seat_surrenders"][0]["status"] == "sth_pinged"
    assert "auctions" not in store
    assert "bids" not in store
    assert "gate_passes" not in store
    assert store["season_ticket_holders"][0]["credit_balance"] == 0.0
    assert fake_twilio_client == []


def test_unknown_phone_is_a_noop(client, store, fake_supabase, fake_nfl_reply, fake_twilio_client):
    resp = post_sms(client, "YES", from_phone="+19995550000")

    assert resp.status_code == 200
    assert b"<Response></Response>" in resp.data
    assert store == {}
    assert fake_twilio_client == []


def test_known_sth_with_no_pending_surrender_is_a_noop(
    client, store, fake_supabase, fake_nfl_reply, fake_twilio_client,
):
    store["season_ticket_holders"] = [{"id": 10, "phone": STH_PHONE, "credit_balance": 0.0}]
    # No seat_surrenders row at all for this STH.

    resp = post_sms(client, "YES")

    assert resp.status_code == 200
    assert store["season_ticket_holders"][0]["credit_balance"] == 0.0
    assert fake_twilio_client == []


def test_yes_reply_runs_the_full_legacy_auction_flow(
    client, store, fake_supabase, fake_nfl_reply, fake_twilio_client,
):
    seed_sth_with_pending_surrender(store, sth_id=10, fan_id=99, credit_balance=5.0)

    resp = post_sms(client, "YES")

    assert resp.status_code == 200
    surrender = store["seat_surrenders"][0]
    assert surrender["status"] == "sold"

    assert len(store["auctions"]) == 1
    auction = store["auctions"][0]
    assert auction["surrender_id"] == 500
    assert auction["status"] == "closed"
    assert auction["highest_bid_usd"] == fanxp_api.CREDIT_OFFER
    assert auction["winning_fan_id"] == 99

    assert len(store["bids"]) == 1
    assert store["bids"][0]["fan_id"] == 99
    assert store["bids"][0]["bid_amount_usd"] == fanxp_api.CREDIT_OFFER

    assert len(store["gate_passes"]) == 1
    gate_pass = store["gate_passes"][0]
    assert gate_pass["auction_id"] == auction["id"]
    assert gate_pass["fan_id"] == 99

    # SMS sent to the fan (not the STH) with the gate pass code embedded.
    assert len(fake_twilio_client) == 1
    sms = fake_twilio_client[0]
    assert sms["to"] == "+15559990000"
    assert gate_pass["unique_pass_code"] in sms["body"]

    # STH's credit balance increased by exactly CREDIT_OFFER.
    assert store["season_ticket_holders"][0]["credit_balance"] == 5.0 + fanxp_api.CREDIT_OFFER


def test_yes_reply_without_fan_id_skips_bid_and_gate_pass_but_still_credits_sth(
    client, store, fake_supabase, fake_nfl_reply, fake_twilio_client,
):
    seed_sth_with_pending_surrender(store, sth_id=10, fan_id=None, credit_balance=0.0)

    resp = post_sms(client, "YES")

    assert resp.status_code == 200
    assert store["seat_surrenders"][0]["status"] == "sold"
    assert "bids" not in store
    assert "gate_passes" not in store
    assert fake_twilio_client == []
    # The credit-balance update sits outside the `if fan_id:` guard, so it
    # still runs even with nobody to hand the seat to.
    assert store["season_ticket_holders"][0]["credit_balance"] == fanxp_api.CREDIT_OFFER


def test_no_reply_marks_expired_without_crediting_sth(
    client, store, fake_supabase, fake_nfl_reply, fake_twilio_client,
):
    seed_sth_with_pending_surrender(store, sth_id=10, fan_id=99, credit_balance=0.0)

    resp = post_sms(client, "NO")

    assert resp.status_code == 200
    assert store["seat_surrenders"][0]["status"] == "expired"
    assert "auctions" not in store
    assert store["season_ticket_holders"][0]["credit_balance"] == 0.0
    assert fake_twilio_client == []


def test_gibberish_reply_leaves_pending_surrender_untouched(
    client, store, fake_supabase, fake_nfl_reply, fake_twilio_client,
):
    seed_sth_with_pending_surrender(store, sth_id=10, fan_id=99, credit_balance=0.0)

    resp = post_sms(client, "maybe later")

    assert resp.status_code == 200
    assert store["seat_surrenders"][0]["status"] == "sth_pinged"
    assert "auctions" not in store
    assert fake_twilio_client == []
