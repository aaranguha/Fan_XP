"""
Tests for run_game.py — NBA's original (pre-generate_nba_dashboard.py) game
runner. Currently paused (CLAUDE.md §2) but still live code with zero test
coverage before this file, and it carries real, non-trivial pure logic:

  - find_team_game(): matches a team into the NBA live-scoreboard games list
    by city substring, used by wait_for_halftime()'s live-clock polling to
    find the right game among potentially several happening at once.
  - parse_clock_minutes(): NBA live-clock string ("PT02M34.56S") -> float
    minutes, with a 99.0 fallback for anything unrecognized.
  - get_tipoff_utc(): pulls the tip-off datetime out of a TM event dict.
  - save_game_meta(): writes game_meta.json. This is its OWN, more complex
    opponent-parsing implementation (not shared with mlb_run_game.py/
    wnba_run_game.py/nfl_run_game.py) — it additionally strips a playoff
    round prefix before the first ":" (e.g. "East Conf Qtrs: ..."), strips
    trailing playoff junk like "Rd 1 Hm Gm 2" / "HM GM2" after the opponent
    name, falls back to a " at " away-team parse for playoff-format names,
    and as a last resort reverse-parses the opponent from the game folder's
    own slug name. Like mlb_run_game.py/wnba_run_game.py (see their test
    files' docstrings) its separator list (" vs. ", " v. ", " vs ", " v ")
    is a case-sensitive literal-substring check that never got NFL's fix
    for a capital-"V" separator (confirmed live on an actual TM event name,
    per nfl_run_game.py's parse_opponent_name() docstring) — unlike MLB/WNBA,
    there's no "fall back to the whole name" safety net here: a capital-"V"
    name with no "_at_" game folder to fall back on silently writes an EMPTY
    opponent field. That case is marked xfail below; see the companion
    GitHub issue. Separately, its trailing-junk-stripping regex has its own
    bug: the inline code comment claims it strips both "Rd 1 Hm Gm 2" and
    "HM GM2", but only the "Rd" alternative's pattern allows a digit stuck
    directly onto the end — the "Hm Gm"/"HM GM" alternatives require a
    boundary right after "Gm", which a directly-attached digit ("GM2")
    never satisfies, so that case silently fails to strip. Also marked
    xfail below, with its own companion issue. save_game_meta() also
    returns None (writes straight to disk) rather than returning the meta
    dict like the other three leagues' versions do, so these tests read
    game_meta.json back off disk.
"""

import json
import os

import pytest

from run_game import find_team_game, get_tipoff_utc, parse_clock_minutes, save_game_meta


def _event(name, local_date="2026-04-07", local_time="19:30:00", venue="Chase Center", city="San Francisco"):
    return {
        "name": name,
        "dates": {"start": {"localDate": local_date, "localTime": local_time}},
        "_embedded": {"venues": [{"name": venue, "city": {"name": city}}]},
    }


def _team(slug="warriors"):
    return {"slug": slug}


def _read_meta(gdir):
    with open(os.path.join(gdir, "game_meta.json"), encoding="utf-8") as f:
        return json.load(f)


# ── get_tipoff_utc() ─────────────────────────────────────────────────────────

def test_get_tipoff_utc_parses_zulu_datetime():
    event = {"dates": {"start": {"dateTime": "2026-04-07T23:00:00Z"}}}
    tipoff = get_tipoff_utc(event)
    assert tipoff.year == 2026 and tipoff.hour == 23


def test_get_tipoff_utc_raises_when_datetime_missing():
    with pytest.raises(RuntimeError):
        get_tipoff_utc({"dates": {"start": {}}})


def test_get_tipoff_utc_raises_on_empty_event():
    with pytest.raises(RuntimeError):
        get_tipoff_utc({})


# ── parse_clock_minutes() ────────────────────────────────────────────────────

def test_pt_format_minutes_and_seconds():
    assert parse_clock_minutes("PT02M34.56S") == pytest.approx(2 + 34.56 / 60)


def test_pt_format_zero_clock():
    assert parse_clock_minutes("PT00M00S") == 0.0


def test_empty_string_falls_back_to_99():
    assert parse_clock_minutes("") == 99.0


def test_none_falls_back_to_99():
    assert parse_clock_minutes(None) == 99.0


def test_colon_format_is_not_handled_falls_back_to_99():
    # Unlike wnba_run_game.py's parse_clock_minutes(), this one only
    # recognizes the NBA live-scoreboard "PTxxMyy.zzS" format, not ESPN's
    # "2:34" clock string — so a colon-format clock also falls back to 99.
    assert parse_clock_minutes("2:34") == 99.0


# ── find_team_game() ─────────────────────────────────────────────────────────

def _game(home_city, away_city, period=2, clock="PT02M00S"):
    return {
        "homeTeam": {"teamCity": home_city},
        "awayTeam": {"teamCity": away_city},
        "period": period,
        "gameClock": clock,
    }


def test_find_team_game_matches_home_team():
    games = [_game("Boston", "Miami"), _game("Golden State", "Lakers")]
    found = find_team_game(games, "Golden State")
    assert found is not None
    assert found["homeTeam"]["teamCity"] == "Golden State"


def test_find_team_game_matches_away_team():
    games = [_game("Boston", "Miami"), _game("Golden State", "Lakers")]
    found = find_team_game(games, "Miami")
    assert found is not None
    assert found["awayTeam"]["teamCity"] == "Miami"


def test_find_team_game_returns_none_when_team_not_playing():
    games = [_game("Boston", "Miami")]
    assert find_team_game(games, "Golden State") is None


def test_find_team_game_returns_none_for_empty_games_list():
    assert find_team_game([], "Boston") is None


def test_find_team_game_returns_first_match_when_multiple_in_list():
    # Only realistic if the scoreboard list has stale/duplicate entries, but
    # the function has no dedup logic of its own — document first-match-wins.
    games = [_game("Boston", "Miami"), _game("Boston", "Orlando")]
    found = find_team_game(games, "Boston")
    assert found["awayTeam"]["teamCity"] == "Miami"


# ── save_game_meta() — regular-season separators ─────────────────────────────

def test_period_dot_vs_separator(tmp_path):
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


def test_bare_vs_separator(tmp_path):
    save_game_meta(_event("Golden State Warriors vs Los Angeles Lakers"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


def test_period_dot_v_separator(tmp_path):
    save_game_meta(_event("Golden State Warriors v. Los Angeles Lakers"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


def test_bare_lowercase_v_separator(tmp_path):
    save_game_meta(_event("Golden State Warriors v Los Angeles Lakers"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


@pytest.mark.xfail(reason=(
    "run_game.py's save_game_meta() separator list (' vs. ', ' v. ', ' vs ', "
    "' v ') is a case-sensitive literal-substring check, same bug class as "
    "mlb_run_game.py/wnba_run_game.py (see their test files) and the one "
    "nfl_run_game.py's parse_opponent_name() docstring documents as a real "
    "confirmed-live TM event name ('Los Angeles Chargers V Arizona Cardinals'). "
    "Unlike MLB/WNBA there's no whole-name fallback for an unrecognized "
    "separator either, so this silently writes an EMPTY opponent field "
    "rather than even a wrong-but-nonempty one. See companion GitHub issue."
))
def test_bare_capital_v_separator_not_recognized_bug(tmp_path):
    save_game_meta(_event("Golden State Warriors V Los Angeles Lakers"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


# ── save_game_meta() — playoff formats ───────────────────────────────────────

def test_playoff_prefix_before_colon_is_stripped(tmp_path):
    save_game_meta(_event("West Conf Semis: Golden State Warriors vs. Los Angeles Lakers"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


def test_playoff_trailing_rd_hm_gm_junk_is_stripped(tmp_path):
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers Rd 1 Hm Gm 2"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


@pytest.mark.xfail(reason=(
    "run_game.py's own inline comment above this regex claims it strips "
    "trailing playoff junk like 'Rd 1 Hm Gm 2' OR 'HM GM2', but the regex "
    "(r'\\s+(?:Rd\\s*\\d|Hm\\s+Gm|HM\\s+GM)\\b') only gives the 'Rd' "
    "alternative a trailing \\d — the 'Hm\\s+Gm'/'HM\\s+GM' alternatives end "
    "right after 'Gm' with a bare \\b, which can't match when a digit is "
    "stuck directly onto 'GM' with no space ('GM2'): 'M' and '2' are both "
    "word characters, so there's no boundary there and the whole alternation "
    "fails to match at that position. 'HM GM2' is therefore NOT stripped "
    "today, contrary to the comment's own example. See companion GitHub issue."
))
def test_playoff_trailing_hm_gm_compact_junk_is_stripped(tmp_path):
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers HM GM2"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


def test_playoff_away_at_home_format_uses_away_team_as_opponent(tmp_path):
    # No "vs."/"v." separator at all in the playoff "Away at Home" TM format
    # — opponent is the away team, parsed from before " at ".
    save_game_meta(_event("Los Angeles Lakers at Golden State Warriors Rd 1 Hm Gm 2"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == "Los Angeles Lakers"


# ── save_game_meta() — folder-slug fallback ──────────────────────────────────

def test_folder_slug_fallback_when_name_has_no_parseable_opponent(tmp_path):
    # Event name has no recognized separator or " at " at all, so the
    # opponent is reverse-parsed from the game folder's own slug name
    # (e.g. "data/clippers/2026-04-07_dallas_mavericks_at_clippers").
    gdir = tmp_path / "2026-04-07_dallas_mavericks_at_clippers"
    gdir.mkdir()
    save_game_meta(_event("Some Unparseable Special Event Title"), _team("clippers"), str(gdir))
    assert _read_meta(gdir)["opponent"] == "Dallas Mavericks"


def test_no_separator_and_no_at_folder_leaves_opponent_empty(tmp_path):
    save_game_meta(_event("Some Unparseable Special Event Title"), _team(), str(tmp_path))
    assert _read_meta(tmp_path)["opponent"] == ""


# ── save_game_meta() — day of week / malformed date ──────────────────────────

def test_day_of_week_is_computed_from_game_date(tmp_path):
    from datetime import datetime
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers", local_date="2026-04-07"), _team(), str(tmp_path))
    expected = datetime.strptime("2026-04-07", "%Y-%m-%d").strftime("%A")
    assert _read_meta(tmp_path)["day_of_week"] == expected


def test_malformed_game_date_does_not_raise_and_leaves_day_of_week_empty(tmp_path):
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers", local_date="not-a-date"), _team(), str(tmp_path))
    meta = _read_meta(tmp_path)
    assert meta["day_of_week"] == ""
    assert meta["game_date"] == "not-a-date"


# ── save_game_meta() — write-once idempotency ────────────────────────────────

def test_save_game_meta_does_not_overwrite_existing_file(tmp_path):
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers"), _team(), str(tmp_path))
    first = _read_meta(tmp_path)

    save_game_meta(_event("Golden State Warriors vs. Boston Celtics"), _team(), str(tmp_path))
    second = _read_meta(tmp_path)

    assert second == first
    assert second["opponent"] == "Los Angeles Lakers"


# ── save_game_meta() — draw-score computation ────────────────────────────────

def test_draw_scores_use_known_opponent_slug(tmp_path):
    # Opponent recognized via slug_from_fullname() -> team_draw_score() uses
    # the Lakers' real TEAM_DRAW entry (star=10, market=10), not the
    # unknown-team default (5, 5). get_opponent_record() fails closed to {}
    # here since nba_api isn't installed in this environment, so win_pct
    # defaults to 0.5 either way.
    save_game_meta(_event("Golden State Warriors vs. Los Angeles Lakers"), _team("warriors"), str(tmp_path))
    meta = _read_meta(tmp_path)
    # team_draw_score("lakers", 0.5) = round(10*0.5 + 10*0.3 + 5*0.2, 2) = 9.0
    assert meta["opponent_draw_score"] == 9.0
    # team_draw_score("warriors", 0.5) = round(10*0.5 + 9*0.3 + 5*0.2, 2) = 8.7
    assert meta["home_draw_score"] == 8.7
    assert meta["game_appeal_score"] == round((8.7 + 9.0) / 2, 2)


def test_draw_score_falls_back_to_default_formula_for_unrecognized_opponent(tmp_path):
    save_game_meta(_event("Golden State Warriors vs. Some Unrecognized Team"), _team("warriors"), str(tmp_path))
    meta = _read_meta(tmp_path)
    # slug_from_fullname() finds no match -> explicit fallback formula:
    # round(win_pct * 10 * 0.2 + 5 * 0.8, 2), win_pct defaults 0.5 -> 5.0
    assert meta["opponent_draw_score"] == 5.0
