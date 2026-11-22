"""
Tests for mlb_run_game.py's main() orchestration — specifically, whether it
protects against the two snapshot-quality failure modes CLAUDE.md §4.2/§4.3
document as real, confirmed incidents on the NFL pipeline:

  - Marketplace collapse: mid-game listings collapsing to a small fraction of
    pre-game listings reflects Ticketmaster's own resale marketplace closing
    out as the event proceeds, not real fan no-shows (confirmed live: 181
    pre-game listings -> 4 at the 71-minute mark for one NFL game, the
    incident that produced nfl_run_game.py's MIN_HALFTIME_RATIO gate).
  - Impossible no-shows: a no-show count exceeding the smaller of the two
    snapshots is mathematically impossible for a real intersection (a
    no-show has to be present in BOTH snapshots) — confirmed live 2026-09-20:
    no_shows=1908 against a pre-game count of only 173, from mismatched
    snapshots.

nfl_run_game.py guards against both via a single reusable function,
evaluate_snapshot_quality() (see nfl_run_game.py), which main() calls before
ever writing to Supabase. mlb_run_game.py has NO equivalent: its main() calls
compare()/insert_no_shows() unconditionally on whatever two CSVs it loaded.

MLB scraping is currently paused (CLAUDE.md §2), so this hasn't fired on real
data yet. But it is structurally the same shape as the Diamondbacks
105,192-row corrupted-no-shows incident (CLAUDE.md §4.3) — a different cause
(no gate at all, vs. a duplicate insert call), same result: a wildly
implausible no-show count written straight to Supabase, this time with
nothing to catch it before the write.

Both tests below are marked xfail: they assert the CORRECT behavior (no
Supabase write), which the current code does not implement. This is NOT
fixed here per QA-agent policy of not patching bugs it finds — see the
companion GitHub issue.
"""

import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

import mlb_run_game


def _seat_rows(n, section="101"):
    """n distinct seat-level rows in the same section, seats 1..n."""
    return [{"section": section, "row": "1", "seat": str(i)} for i in range(1, n + 1)]


def _event(first_pitch: datetime, name="New York Yankees vs. Boston Red Sox"):
    return {
        "name": name,
        "url": "https://www.ticketmaster.com/event/ABC123",
        "dates": {
            "start": {
                "dateTime": first_pitch.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "localDate": first_pitch.strftime("%Y-%m-%d"),
            }
        },
        "_embedded": {"venues": [{"name": "Yankee Stadium", "city": {"name": "New York"}}]},
    }


def _patch_common(monkeypatch, tmp_path, first_pitch, rows_by_snapshot):
    """Wires up every main() dependency with a controllable fake, redirects
    gdir into tmp_path, and returns the list of insert_no_shows() calls made
    (each entry is the row count of that call)."""
    monkeypatch.setattr(sys, "argv", ["mlb_run_game.py", "yankees", "--now"])

    event = _event(first_pitch)
    monkeypatch.setattr(
        mlb_run_game, "get_mlb_team",
        lambda slug: {"slug": "yankees", "mlb_team_id": 147, "tm_keyword": "New York Yankees"},
    )
    monkeypatch.setattr(mlb_run_game, "find_next_home_game", lambda *a, **kw: event)

    gdir = tmp_path / "game"
    monkeypatch.setattr(mlb_run_game, "mlb_game_dir", lambda *a, **kw: str(gdir))

    monkeypatch.setattr(mlb_run_game, "sleep_until", lambda *a, **kw: None)
    monkeypatch.setattr(mlb_run_game, "wait_for_mid_game", lambda *a, **kw: None)
    monkeypatch.setattr(time, "sleep", lambda *a, **kw: None)
    monkeypatch.setattr(mlb_run_game, "print_report", lambda *a, **kw: None)

    monkeypatch.setattr(mlb_run_game.supabase_client, "upsert_game", lambda meta, league="mlb": "game-123")
    monkeypatch.setattr(mlb_run_game.supabase_client, "insert_listings", lambda *a, **kw: None)
    insert_no_shows_calls = []
    monkeypatch.setattr(
        mlb_run_game.supabase_client, "insert_no_shows",
        lambda game_id, rows, *a, **kw: insert_no_shows_calls.append(len(rows)),
    )

    def fake_run_snapshot_tm(event, url, snapshot, out_csv):
        rows = rows_by_snapshot.get(snapshot, [])
        mlb_run_game.save_csv(rows, out_csv)
        return rows

    monkeypatch.setattr(mlb_run_game, "run_snapshot_tm", fake_run_snapshot_tm)

    return insert_no_shows_calls


FUTURE_FIRST_PITCH = datetime.now(timezone.utc) + timedelta(hours=3)


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_run_game.py's main() rejects a halftime/mid-game "
        "snapshot that collapsed below MIN_HALFTIME_RATIO (15%) of pre-game "
        "listings, since that reflects TM's marketplace closing out, not "
        "real no-shows (CLAUDE.md §4.2). mlb_run_game.py's main() has no "
        "equivalent gate and calls insert_no_shows() unconditionally, so a "
        "10%-of-pre-game mid-game snapshot is recorded as a ~90% no-show "
        "rate. Not fixed here per QA-agent policy of not patching bugs it "
        "finds; see the companion GitHub issue. MLB scraping is currently "
        "paused (CLAUDE.md §2), so this hasn't fired live yet."
    ),
    strict=True,
)
def test_marketplace_collapse_is_recorded_as_no_shows_bug(monkeypatch, tmp_path):
    insert_no_shows_calls = _patch_common(
        monkeypatch, tmp_path, FUTURE_FIRST_PITCH,
        rows_by_snapshot={"pre_game": _seat_rows(100), "mid_game": _seat_rows(10)},
    )

    mlb_run_game.main()

    assert insert_no_shows_calls == []


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_run_game.py's main() rejects a no_shows count that "
        "exceeds the smaller of the two snapshots -- mathematically "
        "impossible for a real intersection (CLAUDE.md §4.3, confirmed live "
        "2026-09-20: no_shows=1908 against a pre-game count of 173). "
        "mlb_run_game.py's main() has no equivalent check: compare() counts "
        "every duplicated pre-game row that matches a mid-game key, so "
        "duplicated pre-game rows (e.g. from a re-run that reloads its own "
        "already-scraped CSV) produce a no_shows count bigger than either "
        "snapshot, and it is still written to Supabase unconditionally. Not "
        "fixed here per QA-agent policy of not patching bugs it finds; see "
        "the companion GitHub issue. MLB scraping is currently paused "
        "(CLAUDE.md §2), so this hasn't fired live yet."
    ),
    strict=True,
)
def test_impossible_no_shows_is_recorded_instead_of_rejected_bug(monkeypatch, tmp_path):
    insert_no_shows_calls = _patch_common(
        monkeypatch, tmp_path, FUTURE_FIRST_PITCH,
        rows_by_snapshot={"pre_game": _seat_rows(2) * 3, "mid_game": _seat_rows(2)},
    )

    mlb_run_game.main()

    assert insert_no_shows_calls == []
