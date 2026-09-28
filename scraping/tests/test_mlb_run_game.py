"""
Tests for mlb_run_game.py's pure timing/parsing helpers.

Covers:
  - save_game_meta()'s opponent-name parsing. This is the SAME class of bug
    nfl_run_game.py's parse_opponent_name() docstring documents as a real,
    confirmed-live incident ("Los Angeles Chargers V Arizona Cardinals"
    slipping through a case-sensitive literal-substring check) and fixed
    with a case-insensitive regex (CLAUDE.md §4.3). mlb_run_game.py's
    save_game_meta() (and the duplicate inline copy in main(), not
    independently testable since main() drives live network/sleep calls)
    still does the OLD literal-substring, case-sensitive check
    (`for sep in (" vs. ", " v. ", " vs ", " v "):`) — the fix was never
    ported over from NFL to MLB. The capital-"V" and promo-suffix cases
    below are marked xfail: they capture the correct behavior and document
    the still-open bug without failing the suite (see the companion
    GitHub issue).
  - save_game_meta() also initializes `opponent = ""` before that separator
    loop (unlike main()'s separate inline copy of the same logic, which
    starts from `opponent = name`), so a name with no recognized separator
    ends up with an EMPTY opponent field here rather than falling back to
    the whole event name -- a real behavioral difference between the two
    inline copies, documented as current behavior below (not a bug fix).
  - get_first_pitch_utc(): pulls the first-pitch datetime out of a TM event.
"""

import pytest

from mlb_run_game import get_first_pitch_utc, save_game_meta


def _event(name, local_date="2026-07-04", local_time="19:10:00", venue="Yankee Stadium", city="Bronx"):
    return {
        "name": name,
        "dates": {"start": {"localDate": local_date, "localTime": local_time}},
        "_embedded": {"venues": [{"name": venue, "city": {"name": city}}]},
    }


def _team():
    return {"slug": "yankees"}


# ── save_game_meta() opponent parsing ────────────────────────────────────────

def test_period_dot_vs_separator(tmp_path):
    meta = save_game_meta(_event("New York Yankees vs. Boston Red Sox"), _team(), str(tmp_path))
    assert meta["opponent"] == "Boston Red Sox"


def test_bare_vs_separator(tmp_path):
    meta = save_game_meta(_event("New York Yankees vs Boston Red Sox"), _team(), str(tmp_path))
    assert meta["opponent"] == "Boston Red Sox"


def test_bare_lowercase_v_separator(tmp_path):
    meta = save_game_meta(_event("New York Yankees v Boston Red Sox"), _team(), str(tmp_path))
    assert meta["opponent"] == "Boston Red Sox"


def test_no_recognized_separator_leaves_opponent_empty(tmp_path):
    # Unlike main()'s separate inline copy of this same parsing logic (which
    # falls back to `opponent = name`, the whole event name), save_game_meta()
    # itself initializes `opponent = ""` before the separator loop -- so a
    # name with no recognized separator ends up with an EMPTY opponent field
    # here, not the whole name. Documenting actual current behavior.
    meta = save_game_meta(_event("Some Weird Event Title"), _team(), str(tmp_path))
    assert meta["opponent"] == ""


@pytest.mark.xfail(
    reason=(
        "Real, live-confirmed bug class (see nfl_run_game.py's parse_opponent_name "
        "docstring and CLAUDE.md §4.3): TM event names sometimes use a bare capital "
        "'V' separator ('Los Angeles Chargers V Arizona Cardinals', confirmed live). "
        "nfl_run_game.py was fixed to match case-insensitively via regex, but "
        "mlb_run_game.py's save_game_meta() still does a case-sensitive literal-"
        "substring check (`for sep in (' vs. ', ' v. ', ' vs ', ' v '):`), so a "
        "capital-V matchup name falls through and 'opponent' becomes the entire "
        "unparsed event name. Not fixed here per QA-agent policy of not patching "
        "bugs it finds; see the companion GitHub issue."
    ),
    strict=True,
)
def test_bare_uppercase_v_separator_not_recognized_bug(tmp_path):
    meta = save_game_meta(_event("New York Yankees V Boston Red Sox"), _team(), str(tmp_path))
    assert meta["opponent"] == "Boston Red Sox"


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_run_game.py's parse_opponent_name() strips promo suffixes "
        "after ' - ' or ': ' (confirmed live: 'San Francisco 49ers vs. Arizona "
        "Cardinals - George Kittle Bobblehead' -- CLAUDE.md §4.3), since no team "
        "name contains either. mlb_run_game.py's save_game_meta() has no "
        "equivalent trimming, so a promo suffix ends up baked into the stored "
        "opponent name (and therefore into mlb_game_dir()'s folder-name slug). "
        "Not fixed here per QA-agent policy; see the companion GitHub issue."
    ),
    strict=True,
)
def test_promo_suffix_is_not_stripped_bug(tmp_path):
    meta = save_game_meta(
        _event("New York Yankees vs. Boston Red Sox - Bobblehead Night"),
        _team(),
        str(tmp_path),
    )
    assert meta["opponent"] == "Boston Red Sox"


def test_save_game_meta_persists_and_reuses_existing_file(tmp_path):
    event = _event("New York Yankees vs. Boston Red Sox")
    first = save_game_meta(event, _team(), str(tmp_path))

    # Second call with a different event must return the FIRST call's saved
    # meta unchanged (save_game_meta() short-circuits if game_meta.json already
    # exists), not silently overwrite it with the new event's data.
    second = save_game_meta(_event("New York Yankees vs. Toronto Blue Jays"), _team(), str(tmp_path))
    assert second == first
    assert second["opponent"] == "Boston Red Sox"


def test_save_game_meta_fields(tmp_path):
    meta = save_game_meta(
        _event("New York Yankees vs. Boston Red Sox", local_date="2026-07-04", local_time="19:10:00"),
        _team(),
        str(tmp_path),
    )
    assert meta["home_team"] == "yankees"
    assert meta["game_date"] == "2026-07-04"
    assert meta["day_of_week"] == "Saturday"
    assert meta["tipoff_local"] == "19:10"
    assert meta["arena"] == "Yankee Stadium"
    assert meta["city"] == "Bronx"
    assert meta["league"] == "mlb"


# ── get_first_pitch_utc() ─────────────────────────────────────────────────────

def test_get_first_pitch_utc_parses_zulu_datetime():
    from datetime import datetime, timezone

    event = {"dates": {"start": {"dateTime": "2026-07-04T23:10:00Z"}}}
    assert get_first_pitch_utc(event) == datetime(2026, 7, 4, 23, 10, 0, tzinfo=timezone.utc)


def test_get_first_pitch_utc_raises_when_datetime_missing():
    with pytest.raises(RuntimeError):
        get_first_pitch_utc({"dates": {"start": {}}})
