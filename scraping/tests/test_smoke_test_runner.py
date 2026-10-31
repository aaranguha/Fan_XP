"""
smoke_test_runner.py is the live-runner health check `smoke-test.yml` invokes
on the actual self-hosted machine. It never touches production data, but a
bug in its own pass/fail decision logic (exit codes, --parallel/--worker
argument handling) would make the smoke test lie about whether a runner can
actually reach Ticketmaster -- exactly the kind of silent false-positive the
founder relies on this script to rule out. None of this logic was covered
before this test file: it has zero existing test coverage.
"""
import sys

import pytest

import smoke_test_runner as str_mod


# ---------------------------------------------------------------------------
# run_worker(): the unit a single --worker invocation runs
# ---------------------------------------------------------------------------

def test_run_worker_exits_zero_when_rows_built(monkeypatch):
    monkeypatch.setattr(str_mod, "scrape_listings", lambda *a, **k: ({}, {}, {}, ["offer1"]))
    monkeypatch.setattr(str_mod, "build_rows_from_embedded_offers", lambda offers, scraped_at: [{"section": "101"}])

    with pytest.raises(SystemExit) as exc:
        str_mod.run_worker("http://example.com/event", 0)
    assert exc.value.code == 0


def test_run_worker_exits_one_when_rows_empty(monkeypatch):
    monkeypatch.setattr(str_mod, "scrape_listings", lambda *a, **k: ({}, {}, {}, []))
    monkeypatch.setattr(str_mod, "build_rows_from_embedded_offers", lambda offers, scraped_at: [])

    with pytest.raises(SystemExit) as exc:
        str_mod.run_worker("http://example.com/event", 2)
    assert exc.value.code == 1


def test_run_worker_exits_one_on_scrape_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("TM bot block")

    monkeypatch.setattr(str_mod, "scrape_listings", boom)

    with pytest.raises(SystemExit) as exc:
        str_mod.run_worker("http://example.com/event", 1)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# run_parallel(): the --parallel N load-test orchestration
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode, stdout_lines):
        self.returncode = returncode
        self._out = "\n".join(stdout_lines)

    def communicate(self):
        return self._out, None


def test_run_parallel_exits_zero_when_all_workers_succeed(monkeypatch):
    procs = iter([
        _FakeProc(0, ["[worker 0] ok: 5 rows in 3s"]),
        _FakeProc(0, ["[worker 1] ok: 7 rows in 4s"]),
    ])
    monkeypatch.setattr(str_mod.subprocess, "Popen", lambda *a, **k: next(procs))
    monkeypatch.setattr(str_mod.shutil, "rmtree", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exc:
        str_mod.run_parallel("http://example.com/event", 2)
    assert exc.value.code == 0


def test_run_parallel_exits_one_when_any_worker_fails(monkeypatch):
    procs = iter([
        _FakeProc(0, ["[worker 0] ok: 5 rows in 3s"]),
        _FakeProc(1, ["[worker 1] FAILED after 10s: timeout"]),
    ])
    monkeypatch.setattr(str_mod.subprocess, "Popen", lambda *a, **k: next(procs))
    monkeypatch.setattr(str_mod.shutil, "rmtree", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exc:
        str_mod.run_parallel("http://example.com/event", 2)
    assert exc.value.code == 1


def test_run_parallel_cleans_up_every_worker_chrome_profile(monkeypatch):
    procs = iter([_FakeProc(0, ["[worker 0] ok"]), _FakeProc(0, ["[worker 1] ok"])])
    monkeypatch.setattr(str_mod.subprocess, "Popen", lambda *a, **k: next(procs))

    cleaned = []
    monkeypatch.setattr(str_mod.shutil, "rmtree", lambda path, ignore_errors=False: cleaned.append(path))

    with pytest.raises(SystemExit):
        str_mod.run_parallel("http://example.com/event", 2)

    assert len(cleaned) == 2
    assert all("smoke_test_" in p for p in cleaned)


# ---------------------------------------------------------------------------
# check_espn_scoreboard(): never fatal regardless of ESPN's response
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_check_espn_scoreboard_reachable(monkeypatch, capsys):
    monkeypatch.setattr(str_mod.requests, "get", lambda *a, **k: _FakeResp(200, {"events": [1, 2, 3]}))
    str_mod.check_espn_scoreboard()
    out = capsys.readouterr().out
    assert "3 events returned" in out


def test_check_espn_scoreboard_blocked_is_not_fatal(monkeypatch, capsys):
    monkeypatch.setattr(str_mod.requests, "get", lambda *a, **k: _FakeResp(403, {}))
    str_mod.check_espn_scoreboard()  # must not raise
    out = capsys.readouterr().out
    assert "blocked (HTTP 403)" in out


def test_check_espn_scoreboard_network_error_is_not_fatal(monkeypatch, capsys):
    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(str_mod.requests, "get", boom)
    str_mod.check_espn_scoreboard()  # must not raise
    out = capsys.readouterr().out
    assert "ESPN check failed" in out


# ---------------------------------------------------------------------------
# main(): argument parsing / dispatch branches
# ---------------------------------------------------------------------------

def test_main_no_args_exits_one(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py"])
    with pytest.raises(SystemExit) as exc:
        str_mod.main()
    assert exc.value.code == 1
    assert "Usage:" in capsys.readouterr().out


def test_main_worker_flag_dispatches_to_run_worker(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py", "http://example.com/event", "--worker", "3"])
    calls = []

    def fake_run_worker(url, i):
        calls.append((url, i))
        sys.exit(0)

    monkeypatch.setattr(str_mod, "run_worker", fake_run_worker)
    with pytest.raises(SystemExit) as exc:
        str_mod.main()
    assert exc.value.code == 0
    assert calls == [("http://example.com/event", 3)]


def test_main_parallel_flag_dispatches_to_run_parallel(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py", "http://example.com/event", "--parallel", "5"])
    calls = []

    def fake_run_parallel(url, n):
        calls.append((url, n))
        sys.exit(0)

    monkeypatch.setattr(str_mod, "run_parallel", fake_run_parallel)
    with pytest.raises(SystemExit) as exc:
        str_mod.main()
    assert exc.value.code == 0
    assert calls == [("http://example.com/event", 5)]


def test_main_parallel_one_runs_single_scrape_path_not_run_parallel(monkeypatch):
    # --parallel 1 is the same as not passing --parallel at all: must NOT
    # take the subprocess-spawning run_parallel() path.
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py", "http://example.com/event", "--parallel", "1"])

    def fail_if_called(*a, **k):
        raise AssertionError("run_parallel should not be called for --parallel 1")

    monkeypatch.setattr(str_mod, "run_parallel", fail_if_called)
    monkeypatch.setattr(str_mod, "check_espn_scoreboard", lambda: None)
    monkeypatch.setattr(str_mod, "scrape_listings", lambda *a, **k: ({"s": 1}, {}, {"p": 1}, [{"price_usd": 10}]))
    monkeypatch.setattr(str_mod, "build_rows_from_embedded_offers", lambda offers, scraped_at: [{"price_usd": 10}])

    str_mod.main()  # should fall through to the single-scrape path and return normally


def test_main_single_scrape_fails_when_no_rows_built(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py", "http://example.com/event"])
    monkeypatch.setattr(str_mod, "check_espn_scoreboard", lambda: None)
    monkeypatch.setattr(str_mod, "scrape_listings", lambda *a, **k: ({}, {}, {}, []))
    monkeypatch.setattr(str_mod, "build_rows_from_embedded_offers", lambda offers, scraped_at: [])

    with pytest.raises(SystemExit) as exc:
        str_mod.main()
    assert exc.value.code == 1
    assert "no rows built at all" in capsys.readouterr().out


def test_main_single_scrape_exits_one_on_scrape_exception(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py", "http://example.com/event"])
    monkeypatch.setattr(str_mod, "check_espn_scoreboard", lambda: None)

    def boom(*a, **k):
        raise RuntimeError("TM bot block")

    monkeypatch.setattr(str_mod, "scrape_listings", boom)

    with pytest.raises(SystemExit) as exc:
        str_mod.main()
    assert exc.value.code == 1
    assert "SCRAPE FAILED" in capsys.readouterr().out


def test_main_single_scrape_warns_but_does_not_exit_on_partial_pricing(monkeypatch, capsys):
    # Some rows missing a price is a WARNING, not a failure -- main() must
    # return normally (no SystemExit) so the workflow step reports success.
    monkeypatch.setattr(sys, "argv", ["smoke_test_runner.py", "http://example.com/event"])
    monkeypatch.setattr(str_mod, "check_espn_scoreboard", lambda: None)
    monkeypatch.setattr(str_mod, "scrape_listings", lambda *a, **k: ({}, {}, {}, ["o1", "o2"]))
    monkeypatch.setattr(
        str_mod, "build_rows_from_embedded_offers",
        lambda offers, scraped_at: [{"price_usd": 10}, {"price_usd": None}],
    )

    str_mod.main()  # must not raise SystemExit
    out = capsys.readouterr().out
    assert "WARNING: 1 rows missing a price" in out
    assert "PASS" in out
