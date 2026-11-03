"""
Tests for fetch_listings._team_profile_path(): the per-team Chrome profile
directory each browser session launches against.

Each NFL game runs its own subprocess/browser, and on a busy Sunday several
of them call this at once for *different* teams sharing the same
CHROME_PROFILE_BASE. The function is supposed to seed a fresh team profile
from the shared logged-in TM session on first use, but never touch (or
re-copy over) a team profile that already exists -- shutil.copytree raises
FileExistsError if its destination already exists, and even if it didn't,
clobbering an existing profile mid-scrape would be the kind of two-teams-
stomp-on-one-directory bug this per-team split exists to avoid.
"""

import os

import fetch_listings


def test_creates_team_dir_when_no_shared_profile(tmp_path, monkeypatch):
    base = tmp_path / "profiles"
    monkeypatch.setattr(fetch_listings, "CHROME_PROFILE_BASE", str(base))

    result = fetch_listings._team_profile_path("eagles")

    assert result == str(base / "eagles")
    assert os.path.isdir(result)
    # nothing to copy from, so it's just an empty freshly-made directory
    assert os.listdir(result) == []


def test_seeds_team_dir_from_shared_profile(tmp_path, monkeypatch):
    base = tmp_path / "profiles"
    shared = base / "shared"
    shared.mkdir(parents=True)
    (shared / "Default").mkdir()
    (shared / "Default" / "Cookies").write_text("tm-session-cookie")
    monkeypatch.setattr(fetch_listings, "CHROME_PROFILE_BASE", str(base))

    result = fetch_listings._team_profile_path("eagles")

    assert result == str(base / "eagles")
    copied = base / "eagles" / "Default" / "Cookies"
    assert copied.read_text() == "tm-session-cookie"


def test_existing_team_dir_is_left_untouched(tmp_path, monkeypatch):
    base = tmp_path / "profiles"
    shared = base / "shared"
    shared.mkdir(parents=True)
    (shared / "Default").mkdir()
    (shared / "Default" / "Cookies").write_text("fresh-shared-cookie")

    team_dir = base / "eagles"
    team_dir.mkdir()
    (team_dir / "marker.txt").write_text("already-logged-in-for-this-team")
    monkeypatch.setattr(fetch_listings, "CHROME_PROFILE_BASE", str(base))

    result = fetch_listings._team_profile_path("eagles")

    assert result == str(team_dir)
    # the pre-existing profile survives untouched -- no copytree-over-existing-dir
    # crash, and no silent overwrite of a profile another process is using.
    assert (team_dir / "marker.txt").read_text() == "already-logged-in-for-this-team"
    assert not (team_dir / "Default").exists()


def test_each_team_gets_its_own_independent_copy(tmp_path, monkeypatch):
    base = tmp_path / "profiles"
    shared = base / "shared"
    shared.mkdir(parents=True)
    (shared / "Default").mkdir()
    (shared / "Default" / "Cookies").write_text("shared-cookie")
    monkeypatch.setattr(fetch_listings, "CHROME_PROFILE_BASE", str(base))

    eagles_path = fetch_listings._team_profile_path("eagles")
    browns_path = fetch_listings._team_profile_path("browns")

    assert eagles_path != browns_path

    # mutate one team's copy and confirm the other is unaffected -- they must
    # not share any state (e.g. accidentally be symlinked or the same dir).
    (base / "eagles" / "Default" / "Cookies").write_text("eagles-specific-cookie")
    assert (base / "browns" / "Default" / "Cookies").read_text() == "shared-cookie"
