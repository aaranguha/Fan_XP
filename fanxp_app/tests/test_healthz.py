"""
Tests for GET /healthz (Render health check + deploy-guard commit probe).
200 + ok when Supabase answers; 503 when it raises. No network.
"""

from types import SimpleNamespace

import fanxp_api


class FakeSB:
    def __init__(self, boom=False):
        self.boom = boom

    def table(self, name):
        assert name == "games"
        return self

    def select(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("secret-host.supabase.co refused connection")
        return SimpleNamespace(data=[{"id": 1}])


def test_healthz_is_liveness_only_and_never_touches_supabase(monkeypatch):
    # Render probes /healthz constantly; a paused Supabase must not make it
    # restart a healthy API or block deploys.
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: FakeSB(boom=True))
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    resp = fanxp_api.app.test_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "commit": "abc123", "checks": {}}


def test_deep_healthz_ok_echoes_commit(monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: FakeSB())
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    resp = fanxp_api.app.test_client().get("/healthz?deep=1")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "commit": "abc123", "checks": {"supabase": "ok"}}


def test_deep_healthz_503_when_supabase_raises(monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: FakeSB(boom=True))
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    resp = fanxp_api.app.test_client().get("/healthz?deep=1")
    body = resp.get_json()
    assert resp.status_code == 503
    assert body["ok"] is False
    assert body["commit"] == ""
    assert body["checks"] == {"supabase": "unreachable"}
    assert "secret-host" not in resp.get_data(as_text=True)
