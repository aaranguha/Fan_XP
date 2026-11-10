"""
Tests for nfl_run_game.py's main() — the full game-day orchestration driver
that had NO coverage at all before this file (0% on the function per
coverage, lines 280-466), despite being where several real, documented
production incidents actually live (CLAUDE.md §4.2):

  - The same-day duplicate-run guard (`.scrape_complete` marker): added after
    nfl.yml fired twice on 2026-09-13 and a second Giants subprocess tried to
    re-scrape a game that was already past halftime.
  - The "too late to take a real baseline" guard (MIN_PREGAME_LEAD_MIN):
    added after the runner started >2h late on 2026-09-14 (Chiefs) and
    2026-09-17 (Bills) and took a "pre-game" baseline minutes after kick-off,
    fabricating a near-100% no-show rate.
  - The missing-CSV-is-not-an-error fix: a pre-game or halftime scrape that
    legitimately finds 0 listings never writes its CSV (fetch_listings.
    save_csv() is a no-op on empty rows) — main() used to load_csv()
    unconditionally and crash with an unhandled FileNotFoundError (confirmed
    live 2026-09-20/21/22, runs 35531541265/35549121946/35675895765).
  - The three evaluate_snapshot_quality() verdicts actually being dispatched
    to the right Telegram message and the right Supabase write (or skip).
  - The "another instance finished while this one was sleeping" re-checks
    after sleep_until()/wait_for_halftime() — an orphaned duplicate process
    must bail out instead of redoing work another instance already did.

evaluate_snapshot_quality() itself, save_game_meta(), run_snapshot(), and
notify_scrape_status() each already have their own dedicated test files —
this file is only about main()'s own orchestration: what it calls, in what
order, and what it skips, under each real-world scenario.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import nfl_run_game


def _seat_rows(n, section="101"):
    """n distinct seat-level rows in the same section, seats 1..n."""
    return [{"section": section, "row": "1", "seat": str(i)} for i in range(1, n + 1)]


def _event(kickoff: datetime, name="Kansas City Chiefs vs. Denver Broncos"):
    return {
        "name": name,
        "url": "https://www.ticketmaster.com/event/ABC123",
        "dates": {
            "start": {
                "dateTime": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "localDate": kickoff.strftime("%Y-%m-%d"),
            }
        },
        "_embedded": {"venues": [{"name": "Arrowhead Stadium", "city": {"name": "Kansas City"}}]},
    }


def _patch_common(monkeypatch, tmp_path, kickoff, team_slug="chiefs", espn_tricode="KC"):
    """Wires up every main() dependency with a controllable fake, and returns
    a dict of call logs the test can assert against. gdir is redirected into
    tmp_path so nothing touches the real data/nfl/ tree."""
    monkeypatch.setattr(sys, "argv", ["nfl_run_game.py", team_slug])

    event = _event(kickoff)
    monkeypatch.setattr(
        nfl_run_game, "get_nfl_team",
        lambda slug: {"slug": team_slug, "espn_tricode": espn_tricode, "tm_keyword": "Kansas City Chiefs"},
    )
    monkeypatch.setattr(nfl_run_game, "find_next_home_game", lambda *a, **kw: event)

    gdir = tmp_path / "game"
    monkeypatch.setattr(nfl_run_game, "nfl_game_dir", lambda *a, **kw: str(gdir))

    monkeypatch.setattr(nfl_run_game, "sleep_until", lambda *a, **kw: None)
    monkeypatch.setattr(nfl_run_game, "wait_for_halftime", lambda *a, **kw: None)
    monkeypatch.setattr(nfl_run_game.time, "sleep", lambda *a, **kw: None)
    monkeypatch.setattr(nfl_run_game, "print_report", lambda *a, **kw: None)

    notified = []
    monkeypatch.setattr(nfl_run_game, "notify_scrape_status", lambda msg: notified.append(msg))

    upsert_calls = []
    insert_listings_calls = []
    insert_no_shows_calls = []
    monkeypatch.setattr(nfl_run_game.supabase_client, "upsert_game", lambda meta, league="nfl": upsert_calls.append(meta) or "game-123")
    monkeypatch.setattr(
        nfl_run_game.supabase_client, "insert_listings",
        lambda game_id, rows, snapshot, *a, **kw: insert_listings_calls.append((snapshot, len(rows))),
    )
    monkeypatch.setattr(
        nfl_run_game.supabase_client, "insert_no_shows",
        lambda game_id, rows, *a, **kw: insert_no_shows_calls.append(len(rows)),
    )

    return {
        "gdir": gdir,
        "event": event,
        "notified": notified,
        "upsert_calls": upsert_calls,
        "insert_listings_calls": insert_listings_calls,
        "insert_no_shows_calls": insert_no_shows_calls,
    }


def _patch_run_snapshot(monkeypatch, rows_by_snapshot=None, raise_on=None):
    """Fake run_snapshot() that writes real CSVs via the real save_csv() (so
    main()'s later load_csv()/os.path.isfile() dance round-trips for real,
    including the "zero rows -> no file written" behavior), and tracks which
    snapshots were actually attempted."""
    rows_by_snapshot = rows_by_snapshot or {}
    calls = []

    def fake(event, url, snapshot, out_csv, team_slug):
        calls.append(snapshot)
        if raise_on == snapshot:
            raise RuntimeError(f"boom during {snapshot}")
        rows = rows_by_snapshot.get(snapshot, [])
        nfl_run_game.save_csv(rows, out_csv)
        return rows

    monkeypatch.setattr(nfl_run_game, "run_snapshot", fake)
    return calls


FAR_FUTURE_KICKOFF = datetime.now(timezone.utc) + timedelta(hours=3)


# ── same-day duplicate-run guard ─────────────────────────────────────────────

def test_done_marker_present_skips_everything(monkeypatch, tmp_path):
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    os.makedirs(ctx["gdir"], exist_ok=True)
    (ctx["gdir"] / ".scrape_complete").touch()
    run_snapshot_calls = _patch_run_snapshot(monkeypatch)

    nfl_run_game.main()

    assert run_snapshot_calls == []
    assert ctx["upsert_calls"] == []  # never even gets to save_game_meta/upsert_game
    assert ctx["notified"] == []


# ── "too late for a real baseline" guard ─────────────────────────────────────

def test_too_late_to_scrape_sends_alert_and_skips_scraping(monkeypatch, tmp_path):
    # Kickoff only 15 min away: pre-game is due 60 min before kick-off (already
    # past), and the runner is starting with only 15 min of lead -- less than
    # MIN_PREGAME_LEAD_MIN (20) -- so there's no time left for a real baseline.
    kickoff = datetime.now(timezone.utc) + timedelta(minutes=15)
    ctx = _patch_common(monkeypatch, tmp_path, kickoff)
    run_snapshot_calls = _patch_run_snapshot(monkeypatch)

    nfl_run_game.main()

    assert run_snapshot_calls == []
    assert ctx["insert_listings_calls"] == []
    assert len(ctx["notified"]) == 1
    assert "NOT scraped" in ctx["notified"][0]
    assert "too late for a real baseline" in ctx["notified"][0]
    assert os.path.isfile(ctx["gdir"] / ".scrape_complete")  # so a later trigger won't retry it even later


def test_too_late_to_scrape_does_not_double_alert_on_a_repeat_attempt(monkeypatch, tmp_path):
    # If an earlier run already attempted this game (left game_meta.json
    # behind) and got cut off for the same reason, a later re-discovery must
    # not send a second "NOT scraped" alert for the same game.
    kickoff = datetime.now(timezone.utc) + timedelta(minutes=15)
    ctx = _patch_common(monkeypatch, tmp_path, kickoff)
    os.makedirs(ctx["gdir"], exist_ok=True)
    (ctx["gdir"] / "game_meta.json").write_text(json.dumps({"home_team": "chiefs", "opponent": "Denver Broncos"}))
    _patch_run_snapshot(monkeypatch)

    nfl_run_game.main()

    assert ctx["notified"] == []
    assert os.path.isfile(ctx["gdir"] / ".scrape_complete")


# ── "another instance finished while sleeping" re-checks ────────────────────

def test_done_marker_appearing_during_pregame_sleep_skips_the_scrape(monkeypatch, tmp_path):
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    run_snapshot_calls = _patch_run_snapshot(monkeypatch)

    def sleep_then_mark_done(*a, **kw):
        os.makedirs(ctx["gdir"], exist_ok=True)
        (ctx["gdir"] / ".scrape_complete").touch()

    monkeypatch.setattr(nfl_run_game, "sleep_until", sleep_then_mark_done)

    nfl_run_game.main()

    assert run_snapshot_calls == []
    assert ctx["notified"] == []  # this is a quiet skip, not a failure to alert on


def test_done_marker_appearing_during_halftime_wait_skips_only_the_halftime_scrape(monkeypatch, tmp_path):
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    run_snapshot_calls = _patch_run_snapshot(monkeypatch, rows_by_snapshot={"pre_game": _seat_rows(5)})

    def wait_then_mark_done(*a, **kw):
        (ctx["gdir"] / ".scrape_complete").touch()

    monkeypatch.setattr(nfl_run_game, "wait_for_halftime", wait_then_mark_done)

    nfl_run_game.main()

    assert run_snapshot_calls == ["pre_game"]  # pre-game already ran before the wait
    assert ctx["insert_listings_calls"] == [("pre_game", 5)]
    assert ctx["insert_no_shows_calls"] == []  # never reaches the compare/insert step


# ── scrape failures propagate and alert, but don't silently continue ────────

def test_pregame_scrape_failure_alerts_and_reraises(monkeypatch, tmp_path):
    import pytest

    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(monkeypatch, raise_on="pre_game")

    with pytest.raises(RuntimeError, match="boom during pre_game"):
        nfl_run_game.main()

    assert len(ctx["notified"]) == 1
    assert "Pre-game scrape failed" in ctx["notified"][0]
    assert ctx["insert_listings_calls"] == []


def test_halftime_scrape_failure_alerts_and_reraises(monkeypatch, tmp_path):
    import pytest

    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(monkeypatch, rows_by_snapshot={"pre_game": _seat_rows(5)}, raise_on="halftime")

    with pytest.raises(RuntimeError, match="boom during halftime"):
        nfl_run_game.main()

    assert len(ctx["notified"]) == 1
    assert "Halftime scrape failed" in ctx["notified"][0]
    assert ctx["insert_listings_calls"] == [("pre_game", 5)]  # pre-game write did happen
    assert ctx["insert_no_shows_calls"] == []


# ── post-scrape verdict dispatch ─────────────────────────────────────────────

def test_zero_pregame_listings_skips_insert_without_crashing(monkeypatch, tmp_path):
    # Regression guard for the confirmed FileNotFoundError incident: a
    # pre-game scrape that legitimately found 0 listings never writes
    # pre_game.csv at all, and main() must treat that as "no baseline", not
    # crash trying to load a file that was never written.
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(monkeypatch, rows_by_snapshot={"pre_game": [], "halftime": _seat_rows(20)})

    nfl_run_game.main()

    assert len(ctx["notified"]) == 1
    assert "Pre-game scrape found 0 listings" in ctx["notified"][-1]
    assert ctx["insert_no_shows_calls"] == []
    assert os.path.isfile(ctx["gdir"] / ".scrape_complete")


def test_marketplace_collapse_skips_no_show_insert(monkeypatch, tmp_path):
    # CLAUDE.md §4.2: halftime listings collapsing below MIN_HALFTIME_RATIO
    # of pre-game must NOT be recorded as no-shows -- it reflects TM's own
    # marketplace closing out listings, not real fan no-shows.
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(monkeypatch, rows_by_snapshot={"pre_game": _seat_rows(100), "halftime": _seat_rows(10)})

    nfl_run_game.main()

    assert len(ctx["notified"]) == 1
    msg = ctx["notified"][-1]
    assert "⚠️" in msg
    assert "NOT recorded" in msg
    assert ctx["insert_no_shows_calls"] == []
    assert not (ctx["gdir"] / "no_shows.csv").exists()
    assert os.path.isfile(ctx["gdir"] / ".scrape_complete")


def test_impossible_no_shows_skips_insert_instead_of_recording_corrupted_data(monkeypatch, tmp_path):
    # CLAUDE.md §4.3: no_shows exceeding the smaller snapshot is mathematically
    # impossible for a real intersection -- reproduced via duplicated pre-game
    # rows, the same corruption shape as the confirmed 2026-09-20 incident.
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(
        monkeypatch,
        rows_by_snapshot={"pre_game": _seat_rows(2) * 3, "halftime": _seat_rows(2)},
    )

    nfl_run_game.main()

    assert len(ctx["notified"]) == 1
    msg = ctx["notified"][-1]
    assert "⚠️" in msg
    assert "exceeds the smaller snapshot" in msg
    assert ctx["insert_no_shows_calls"] == []


def test_healthy_snapshot_pair_records_no_shows_and_sends_success_alert(monkeypatch, tmp_path):
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(
        monkeypatch,
        rows_by_snapshot={"pre_game": _seat_rows(10), "halftime": _seat_rows(6)},
    )

    nfl_run_game.main()

    assert ctx["insert_listings_calls"] == [("pre_game", 10), ("halftime", 6)]
    assert ctx["insert_no_shows_calls"] == [6]
    assert len(ctx["notified"]) == 1
    msg = ctx["notified"][-1]
    assert "✅" in msg
    assert "10" in msg and "6 at halftime" in msg and "6 no-shows recorded" in msg
    assert os.path.isfile(ctx["gdir"] / "no_shows.csv")
    assert os.path.isfile(ctx["gdir"] / ".scrape_complete")


def test_upsert_game_called_with_this_games_meta_before_either_scrape(monkeypatch, tmp_path):
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    _patch_run_snapshot(monkeypatch, rows_by_snapshot={"pre_game": _seat_rows(3), "halftime": _seat_rows(3)})

    nfl_run_game.main()

    assert len(ctx["upsert_calls"]) == 1
    assert ctx["upsert_calls"][0]["home_team"] == "chiefs"
    assert ctx["upsert_calls"][0]["opponent"] == "Denver Broncos"


# ── CLI argument handling and the no-URL/has-id TM event fallback ───────────

def test_wrong_argument_count_prints_usage_and_exits(monkeypatch, capsys):
    import pytest

    monkeypatch.setattr(sys, "argv", ["nfl_run_game.py"])

    with pytest.raises(SystemExit) as exc_info:
        nfl_run_game.main()

    assert exc_info.value.code == 1
    assert "Usage: python nfl_run_game.py" in capsys.readouterr().out


def test_event_without_ticketmaster_url_falls_back_to_constructed_url_from_id(monkeypatch, tmp_path):
    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    # Override the fake event so it has no usable url, only an id.
    ctx["event"]["url"] = None
    ctx["event"]["id"] = "XYZ789"
    run_snapshot_calls = []

    def fake(event, url, snapshot, out_csv, team_slug):
        run_snapshot_calls.append(url)
        return []

    monkeypatch.setattr(nfl_run_game, "run_snapshot", fake)

    nfl_run_game.main()

    assert run_snapshot_calls[0] == "https://www.ticketmaster.com/event/XYZ789"


def test_event_without_url_or_id_raises(monkeypatch, tmp_path):
    import pytest

    ctx = _patch_common(monkeypatch, tmp_path, FAR_FUTURE_KICKOFF)
    ctx["event"]["url"] = None

    with pytest.raises(RuntimeError, match="Event has no URL or ID"):
        nfl_run_game.main()
