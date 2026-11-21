"""
Direct unit tests for fanxp_accounts.py's request-auth guard:
current_user() and the require_user() decorator factory.

These back every signed-in route in the real accounts/buyer/seller product
(CLAUDE.md §6) -- GET/PATCH /api/me, every /api/seller/* route, the respond
routes -- but until now only had indirect coverage through one route's
status codes (test_accounts.py::test_seller_routes_need_sign_in_and_seller_role).
Here they're driven directly against a tiny throwaway Flask app, so the
guard's own branches are what's under test, not a particular route's
business logic. No network, no real Supabase.
"""

from types import SimpleNamespace

import pytest
from flask import Flask, g, jsonify

import fanxp_accounts


class FakeAuth:
    def __init__(self, users):
        self.users = users  # token -> user

    def get_user(self, token):
        user = self.users.get(token)
        if user is None:
            raise RuntimeError("invalid JWT")
        return SimpleNamespace(user=user)


class FakeSupabase:
    def __init__(self, users):
        self.auth = FakeAuth(users)


def make_user(uid, roles=("buyer",), phone=""):
    return SimpleNamespace(
        id=uid,
        email=f"{uid}@x.com",
        user_metadata={"full_name": uid, "roles": list(roles), "phone": phone},
    )


@pytest.fixture
def app(monkeypatch):
    flask_app = Flask(__name__)

    buyer = make_user("u-buyer", roles=["buyer"])
    seller = make_user("u-seller", roles=["seller"], phone="+15551234567")
    both = make_user("u-both", roles=["buyer", "seller"])

    fake = FakeSupabase({"tok-buyer": buyer, "tok-seller": seller, "tok-both": both})
    monkeypatch.setattr(fanxp_accounts, "get_supabase", lambda: fake)

    @flask_app.get("/open")
    @fanxp_accounts.require_user()
    def open_route():
        return jsonify({"uid": g.user.id, "roles": g.profile["roles"]})

    @flask_app.get("/sellers-only")
    @fanxp_accounts.require_user("seller")
    def sellers_only():
        return jsonify({"uid": g.user.id})

    return flask_app


def auth(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── current_user() ───────────────────────────────────────────────────────

def test_current_user_returns_none_without_header(app):
    with app.test_request_context("/open"):
        assert fanxp_accounts.current_user() is None


def test_current_user_returns_none_for_non_bearer_scheme(app):
    with app.test_request_context("/open", headers={"Authorization": "Basic tok-buyer"}):
        assert fanxp_accounts.current_user() is None


def test_current_user_scheme_check_is_case_sensitive(app):
    # The "Bearer " prefix check is a literal startswith(), so a
    # differently-cased scheme is rejected outright and never even reaches
    # auth.get_user().
    with app.test_request_context("/open", headers={"Authorization": "bearer tok-buyer"}):
        assert fanxp_accounts.current_user() is None


def test_current_user_strips_whitespace_around_the_token(app):
    with app.test_request_context("/open", headers={"Authorization": "Bearer  tok-buyer  "}):
        user = fanxp_accounts.current_user()
        assert user is not None and user.id == "u-buyer"


def test_current_user_returns_none_when_auth_lookup_raises(app):
    # An expired/malformed/unknown token: get_user() raises rather than
    # returning a null user. This must be swallowed into None, not bubble
    # up as an unhandled 500 on every protected route.
    with app.test_request_context("/open", headers={"Authorization": "Bearer garbage"}):
        assert fanxp_accounts.current_user() is None


def test_current_user_returns_the_real_user_for_a_valid_token(app):
    with app.test_request_context("/open", headers={"Authorization": "Bearer tok-seller"}):
        user = fanxp_accounts.current_user()
        assert user is not None and user.id == "u-seller"


# ── require_user() ────────────────────────────────────────────────────────

def test_require_user_rejects_missing_token(app):
    resp = app.test_client().get("/open")
    assert resp.status_code == 401


def test_require_user_rejects_bad_token(app):
    resp = app.test_client().get("/open", headers=auth("garbage"))
    assert resp.status_code == 401


def test_require_user_with_no_role_lets_any_signed_in_user_through(app):
    resp = app.test_client().get("/open", headers=auth("tok-buyer"))
    assert resp.status_code == 200
    assert resp.get_json() == {"uid": "u-buyer", "roles": ["buyer"]}


def test_require_user_sets_g_user_and_g_profile_for_the_wrapped_view(app):
    # The decorator's whole job is making g.user/g.profile available to the
    # route it wraps -- assert the wrapped view actually saw the right ones,
    # not just that it got a 200.
    resp = app.test_client().get("/open", headers=auth("tok-both"))
    assert resp.get_json() == {"uid": "u-both", "roles": ["buyer", "seller"]}


def test_require_user_with_role_rejects_user_missing_that_role(app):
    resp = app.test_client().get("/sellers-only", headers=auth("tok-buyer"))
    assert resp.status_code == 403
    assert "seller" in resp.get_json()["error"]


def test_require_user_with_role_allows_user_with_that_role(app):
    resp = app.test_client().get("/sellers-only", headers=auth("tok-seller"))
    assert resp.status_code == 200
    assert resp.get_json() == {"uid": "u-seller"}


def test_require_user_role_check_happens_after_the_sign_in_check(app):
    # A role-gated route with no token at all must report 401 ("sign in"),
    # not 403 ("turn on the role") -- the two checks must stay in this
    # order even though both end the request before the view runs.
    resp = app.test_client().get("/sellers-only")
    assert resp.status_code == 401
