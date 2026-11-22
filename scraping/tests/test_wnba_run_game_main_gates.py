"""
Tests for wnba_run_game.py's main() orchestration — specifically, whether it
protects against the two snapshot-quality failure modes CLAUDE.md §4.2/§4.3
document as real, confirmed incidents on the NFL pipeline:

  - Marketplace collapse: halftime listings collapsing to a small fraction of
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
ever writing to Supabase. wnba_run_game.py has NO equivalent: its main()
only checks `if pre_rows and ht_rows:` (both non-empty) before calling
compare()/insert_no_shows() — no ratio check and no impossible-count check.

WNBA scraping is currently paused (CLAUDE.md §2), so this hasn't fired on
real data yet. But it is structurally the same shape as the Diamondbacks
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

import wnba_run_game


def _seat_rows(n, section="101"):
    """n distinct seat-level rows in the same section, seats 1..n."""
    return [{"section": section, "row": "1", "seat": str(i)} for i in range(1, n + 1)]


def _event(tipoff: datetime, name="New York Liberty vs. Las Vegas Aces"):
    return {
        "name": name,
        "url": "https://www.ticketmaster.com/event/ABC123",
        "dates": {
            "start": {
                "dateTime": tipoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "localDate": tipoff.strftime("%Y-%m-%d"),
            }
        },
        "_embedded": {"venues": [{"name": "Barclays Center", "city": {"name": "Brooklyn"}}]},
    }


def _patch_common(monkeypatch, tmp_path, tipoff, rows_by_snapshot):
    """Wires up every main() dependency with a controllable fake, redirects
    gdir into tmp_path, and returns the list of insert_no_shows() calls made
    (each entry is the row count of that call)."""
    monkeypatch.setattr(sys, "argv", ["wnba_run_game.py", "liberty"])

    event = _event(tipoff)
    monkeypatch.setattr(
        wnba_run_game, "get_wnba_team",
        lambda slug: {"slug": "liberty", "espn_abbr": "NY", "tm_keyword": "New York Liberty"},
    )
    monkeypatch.setattr(wnba_run_game, "find_next_home_game", lambda *a, **kw: event)

    gdir = tmp_path / "game"
    monkeypatch.setattr(wnba_run_game, "wnba_game_dir", lambda *a, **kw: str(gdir))

    monkeypatch.setattr(wnba_run_game, "sleep_until", lambda *a, **kw: None)
    monkeypatch.setattr(wnba_run_game, "wait_for_halftime", lambda *a, **kw: None)
    monkeypatch.setattr(time, "sleep", lambda *a, **kw: None)
    monkeypatch.setattr(wnba_run_game, "print_report", lambda *a, **kw: None)

    monkeypatch.setattr(wnba_run_game.supabase_client, "upsert_game", lambda meta, league="wnba": "game-123")
    monkeypatch.setattr(wnba_run_game.supabase_client, "insert_listings", lambda *a, **kw: None)
    insert_no_shows_calls = []
    monkeypatch.setattr(
        wnba_run_game.supabase_client, "insert_no_shows",
        lambda game_id, rows, *a, **kw: insert_no_shows_calls.append(len(rows)),
    )

    def fake_run_snapshot(event, url, snapshot, out_csv):
        rows = rows_by_snapshot.get(snapshot, [])
        wnba_run_game.save_csv(rows, out_csv)
        return rows

    monkeypatch.setattr(wnba_run_game, "run_snapshot", fake_run_snapshot)

    return insert_no_shows_calls


FUTURE_TIPOFF = datetime.now(timezone.utc) + timedelta(hours=3)


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_run_game.py's main() rejects a halftime snapshot "
        "that collapsed below MIN_HALFTIME_RATIO (15%) of pre-game "
        "listings, since that reflects TM's marketplace closing out, not "
        "real no-shows (CLAUDE.md §4.2). wnba_run_game.py's main() only "
        "checks that both snapshots are non-empty before calling "
        "insert_no_shows() -- no ratio check -- so a 10%-of-pre-game "
        "halftime snapshot is recorded as a ~90% no-show rate. Not fixed "
        "here per QA-agent policy of not patching bugs it finds; see the "
        "companion GitHub issue. WNBA scraping is currently paused "
        "(CLAUDE.md §2), so this hasn't fired live yet."
    ),
    strict=True,
)
def test_marketplace_collapse_is_recorded_as_no_shows_bug(monkeypatch, tmp_path):
    insert_no_shows_calls = _patch_common(
        monkeypatch, tmp_path, FUTURE_TIPOFF,
        rows_by_snapshot={"pre_game": _seat_rows(100), "halftime": _seat_rows(10)},
    )

    wnba_run_game.main()

    assert insert_no_shows_calls == []


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_run_game.py's main() rejects a no_shows count that "
        "exceeds the smaller of the two snapshots -- mathematically "
        "impossible for a real intersection (CLAUDE.md §4.3, confirmed live "
        "2026-09-20: no_shows=1908 against a pre-game count of 173). "
        "wnba_run_game.py's main() has no equivalent check: compare() "
        "counts every duplicated pre-game row that matches a halftime key, "
        "so duplicated pre-game rows (e.g. from a re-run that reloads its "
        "own already-scraped CSV) produce a no_shows count bigger than "
        "either snapshot, and it is still written to Supabase "
        "unconditionally. Not fixed here per QA-agent policy of not "
        "patching bugs it finds; see the companion GitHub issue. WNBA "
        "scraping is currently paused (CLAUDE.md §2), so this hasn't fired "
        "live yet."
    ),
    strict=True,
)
def test_impossible_no_shows_is_recorded_instead_of_rejected_bug(monkeypatch, tmp_path):
    insert_no_shows_calls = _patch_common(
        monkeypatch, tmp_path, FUTURE_TIPOFF,
        rows_by_snapshot={"pre_game": _seat_rows(2) * 3, "halftime": _seat_rows(2)},
    )

    wnba_run_game.main()

    assert insert_no_shows_calls == []
