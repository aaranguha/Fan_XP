"""
Tests for fanxp_accounts.py's account creation/sign-in flow and profile
validation: register/login/refresh/me/update_me, plus the pure helpers
`_validated_profile()` and `profile_of()` that back them.

These routes were added with the 2026-10-14 accounts feature (see
CLAUDE.md §6) and had no direct coverage beyond the "sellers need a phone"
check in test_accounts.py. Everything here runs against an in-memory fake
Supabase Auth; no network or credentials.
"""

from types import SimpleNamespace

import pytest

import fanxp_accounts
import fanxp_api


# ── Fake Supabase Auth (service-role + the throwaway sign-in client) ───────

class FakeAuth:
    """Backs both get_supabase().auth (service-role, used for create_user /
    update_user_by_id / get_user) and fanxp_accounts._auth_client().auth
    (used for sign_in_with_password / refresh_session) — the real code
    splits these across two clients so a sign-in never touches the service
    client's session, but both need to see the same users here."""

    def __init__(self):
        self.admin = self
        self.by_email = {}
        self.by_id = {}
        self.passwords = {}

    def create_user(self, payload):
        email = payload["email"]
        if email in self.by_email:
            raise RuntimeError("User already registered")
        uid = f"uid-{len(self.by_email) + 1}"
        user = SimpleNamespace(id=uid, email=email, user_metadata=payload["user_metadata"])
        self.by_email[email] = user
        self.by_id[uid] = user
        self.passwords[email] = payload["password"]
        return SimpleNamespace(user=user)

    def update_user_by_id(self, uid, fields):
        user = self.by_id[uid]
        user.user_metadata = fields["user_metadata"]
        return SimpleNamespace(user=user)

    def get_user(self, token):
        user = self.by_id.get(token.replace("tok-", ""))
        if not user:
            raise RuntimeError("invalid JWT")
        return SimpleNamespace(user=user)

    def get_user_by_id(self, uid):
        return SimpleNamespace(user=self.by_id[uid])

    def sign_in_with_password(self, creds):
        email, password = creds["email"], creds["password"]
        user = self.by_email.get(email)
        if not user or self.passwords.get(email) != password:
            raise RuntimeError("Invalid login credentials")
        return SimpleNamespace(
            session=SimpleNamespace(access_token=f"tok-{user.id}",
                                    refresh_token=f"refresh-{user.id}", expires_at=1234567890),
            user=user,
        )

    def refresh_session(self, token):
        uid = token.replace("refresh-", "")
        user = self.by_id.get(uid)
        if not user:
            raise RuntimeError("invalid refresh token")
        return SimpleNamespace(
            session=SimpleNamespace(access_token=f"tok-{uid}", refresh_token=token,
                                    expires_at=1234567890),
            user=user,
        )


class FakeSupabase:
    def __init__(self, auth):
        self.auth = auth


@pytest.fixture
def auth(monkeypatch):
    fake_auth = FakeAuth()
    monkeypatch.setattr(fanxp_accounts, "get_supabase", lambda: FakeSupabase(fake_auth))
    monkeypatch.setattr(fanxp_accounts, "_auth_client", lambda: FakeSupabase(fake_auth))
    return fake_auth


def call(method, path, token=None, json=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = fanxp_api.app.test_client()
    resp = client.open(path, method=method, json=json, headers=headers)
    return resp.status_code, resp.get_json()


VALID_BODY = {"email": "a@b.com", "password": "longenough", "full_name": "Ann", "roles": ["buyer"]}


# ── _validated_profile(): pure, no I/O ──────────────────────────────────────

def test_validated_profile_full_mode_requires_every_field():
    out, err = fanxp_accounts._validated_profile({"full_name": "Ann", "roles": ["buyer"]})
    assert err is None and out == {"full_name": "Ann", "roles": ["buyer"], "phone": ""}


def test_validated_profile_full_mode_rejects_blank_name():
    out, err = fanxp_accounts._validated_profile({"roles": ["buyer"]})
    assert err and "name" in err


def test_validated_profile_rejects_unknown_role():
    out, err = fanxp_accounts._validated_profile({"full_name": "Ann", "roles": ["admin"]})
    assert err and "buying" in err


def test_validated_profile_rejects_empty_roles_list():
    out, err = fanxp_accounts._validated_profile({"full_name": "Ann", "roles": []})
    assert err is not None


def test_validated_profile_rejects_bad_phone_but_allows_blank():
    out, err = fanxp_accounts._validated_profile({"full_name": "Ann", "roles": ["buyer"], "phone": "123"})
    assert err and "phone" in err
    out, err = fanxp_accounts._validated_profile({"full_name": "Ann", "roles": ["buyer"], "phone": ""})
    assert err is None and out["phone"] == ""


def test_validated_profile_normalizes_good_phone():
    out, err = fanxp_accounts._validated_profile(
        {"full_name": "Ann", "roles": ["seller", "buyer"], "phone": "(415) 555-0100"})
    assert err is None
    assert out["phone"] == "+14155550100"
    assert out["roles"] == ["buyer", "seller"]  # sorted, deduped


def test_validated_profile_truncates_long_name():
    out, _ = fanxp_accounts._validated_profile({"full_name": "x" * 200, "roles": ["buyer"]})
    assert len(out["full_name"]) == 80


def test_validated_profile_partial_mode_only_checks_fields_present():
    # Partial updates (PATCH /api/me) must not force full_name/roles/phone
    # to all be re-supplied just to change one of them.
    out, err = fanxp_accounts._validated_profile({"phone": "415-555-0100"}, partial=True)
    assert err is None
    assert out == {"phone": "+14155550100"}


def test_validated_profile_partial_mode_still_rejects_bad_value_for_touched_field():
    out, err = fanxp_accounts._validated_profile({"full_name": "   "}, partial=True)
    assert err and "name" in err


# ── profile_of(): pure, no I/O ──────────────────────────────────────────────

def test_profile_of_defaults_missing_metadata_to_buyer_with_no_phone():
    user = SimpleNamespace(id="u1", email="a@b.com", user_metadata=None)
    assert fanxp_accounts.profile_of(user) == {
        "id": "u1", "email": "a@b.com", "full_name": "", "phone": "", "roles": ["buyer"],
    }


def test_profile_of_drops_unknown_roles():
    user = SimpleNamespace(id="u1", email="a@b.com",
                           user_metadata={"roles": ["buyer", "admin", "seller"]})
    assert fanxp_accounts.profile_of(user)["roles"] == ["buyer", "seller"]


# ── /api/auth/register ───────────────────────────────────────────────────────

def test_register_rejects_invalid_email(auth):
    code, body = call("POST", "/api/auth/register", json={**VALID_BODY, "email": "not-an-email"})
    assert code == 400 and "email" in body["error"]


def test_register_rejects_short_password(auth):
    code, body = call("POST", "/api/auth/register", json={**VALID_BODY, "password": "short"})
    assert code == 400 and "Password" in body["error"]


def test_register_rejects_bad_profile_fields(auth):
    code, body = call("POST", "/api/auth/register", json={**VALID_BODY, "roles": ["admin"]})
    assert code == 400


def test_register_rejects_duplicate_email(auth):
    code, _ = call("POST", "/api/auth/register", json=VALID_BODY)
    assert code == 201
    code, body = call("POST", "/api/auth/register", json=VALID_BODY)
    assert code == 409 and "already exists" in body["error"]


def test_register_then_login_round_trip(auth):
    code, body = call("POST", "/api/auth/register", json=VALID_BODY)
    assert code == 201
    assert body["user"]["email"] == "a@b.com" and body["user"]["roles"] == ["buyer"]
    token = body["access_token"]

    code, me_body = call("GET", "/api/me", token=token)
    assert code == 200 and me_body["full_name"] == "Ann"

    code, login_body = call("POST", "/api/auth/login",
                            json={"email": "a@b.com", "password": "longenough"})
    assert code == 200 and login_body["user"]["email"] == "a@b.com"


def test_login_rejects_wrong_password(auth):
    call("POST", "/api/auth/register", json=VALID_BODY)
    code, body = call("POST", "/api/auth/login", json={"email": "a@b.com", "password": "wrongwrong"})
    assert code == 401 and "error" in body


def test_login_rejects_unknown_email(auth):
    code, body = call("POST", "/api/auth/login", json={"email": "nobody@x.com", "password": "whatever"})
    assert code == 401


# ── /api/auth/refresh ────────────────────────────────────────────────────────

def test_refresh_requires_token(auth):
    code, body = call("POST", "/api/auth/refresh", json={})
    assert code == 400 and "refresh_token" in body["error"]


def test_refresh_rejects_bad_token(auth):
    code, _ = call("POST", "/api/auth/refresh", json={"refresh_token": "garbage"})
    assert code == 401


def test_refresh_returns_new_session_for_valid_token(auth):
    _, body = call("POST", "/api/auth/register", json=VALID_BODY)
    code, refreshed = call("POST", "/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert code == 200
    assert refreshed["user"]["email"] == "a@b.com"


# ── PATCH /api/me ─────────────────────────────────────────────────────────────

def test_update_me_merges_partial_change_and_keeps_other_fields(auth):
    _, reg = call("POST", "/api/auth/register", json=VALID_BODY)
    token = reg["access_token"]

    code, body = call("PATCH", "/api/me", token=token, json={"phone": "415-555-0199"})
    assert code == 200
    assert body["phone"] == "+14155550199"
    assert body["full_name"] == "Ann"  # untouched field preserved
    assert body["roles"] == ["buyer"]


def test_update_me_to_seller_without_phone_is_rejected(auth):
    _, reg = call("POST", "/api/auth/register", json=VALID_BODY)  # buyer, no phone
    token = reg["access_token"]
    code, body = call("PATCH", "/api/me", token=token, json={"roles": ["seller"]})
    assert code == 400 and "phone" in body["error"]


def test_update_me_requires_sign_in(auth):
    code, _ = call("PATCH", "/api/me", json={"full_name": "X"})
    assert code == 401
