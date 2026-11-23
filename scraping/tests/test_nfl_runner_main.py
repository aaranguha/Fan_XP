"""
Tests for nfl_runner.py's launch_team() and main() — the daily orchestration
driver that launches one nfl_run_game.py subprocess per home team, waits for
them, and (on any success) regenerates pages and commits/pushes. Before this
file neither had any coverage at all (0% on main(), per `--cov-report=
term-missing`), despite several of its branches existing specifically because
of real, documented production incidents (CLAUDE.md §5):

  - A partial failure (one team's Playwright timeout, or "Bot Detection")
    used to still leave the whole GitHub Actions run green — confirmed live
    2026-09-13 (Giants). main() now exits 1 whenever any team is in `failed`.
  - A silent git-push failure used to just print a message and move on,
    losing that run's commit entirely (every .scrape_complete marker
    included) while the run still reported success — confirmed live
    2026-09-20, which caused the next run to re-launch pregame scrapes for
    14 teams hours after kickoff. main() now treats a push that never lands
    (after 3 retries with `git pull --rebase` in between) as a hard failure
    ("git-push" appended to `failed`), not a no-op.
  - Regenerating every team's story page (not just tonight's) when more than
    one team ran, vs. only that one team's page on a single-team run.
  - Skipping the seat-map regen for any team with no captured venue geometry.

get_home_teams_with_retry() (already covered by test_nfl_runner.py) and
run_snapshot()/evaluate_snapshot_quality() (covered by nfl_run_game.py's own
tests) are stubbed out here; this file is only about what main() itself
calls, in what order, and what it decides based on each team's outcome.
"""

import os

import pytest

import nfl_runner


class FakeProc:
    def __init__(self, pid, returncode):
        self.pid = pid
        self.returncode = returncode

    def wait(self):
        pass


class FakeLogFile:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_schedule(monkeypatch, slugs):
    monkeypatch.setattr(nfl_runner, "get_home_teams_with_retry", lambda today: slugs)


def _patch_launch(monkeypatch, returncodes: dict):
    """returncodes: {slug: returncode}. Records (slug, today) calls."""
    calls = []

    def fake_launch_team(slug, today):
        calls.append((slug, today))
        return FakeProc(pid=len(calls), returncode=returncodes[slug]), FakeLogFile()

    monkeypatch.setattr(nfl_runner, "launch_team", fake_launch_team)
    return calls


def _patch_subprocess_run(monkeypatch, push_results=None):
    """Records every subprocess.run call and returns a fake CompletedProcess.
    push_results is a list of returncodes consumed in order for `git push`
    calls (default: every push succeeds)."""
    calls = []
    push_results = list(push_results) if push_results is not None else None

    class FakeCompleted:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        if cmd[:2] == ["git", "push"]:
            rc = push_results.pop(0) if push_results else 0
            return FakeCompleted(rc)
        return FakeCompleted(0)

    monkeypatch.setattr(nfl_runner.subprocess, "run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _no_real_sleep_or_env(monkeypatch):
    monkeypatch.setattr(nfl_runner.time, "sleep", lambda s: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("NFL_GAME_DATE", raising=False)


# ── main(): schedule lookup ─────────────────────────────────────────────────

def test_no_home_games_today_returns_without_launching_anything(monkeypatch):
    _patch_schedule(monkeypatch, [])
    launch_calls = _patch_launch(monkeypatch, {})
    run_calls = _patch_subprocess_run(monkeypatch)

    nfl_runner.main()  # must not raise/exit

    assert launch_calls == []
    assert run_calls == []


def test_schedule_fetch_failure_exits_1_without_launching_anything(monkeypatch):
    def boom(today):
        raise RuntimeError("schedule lookup exhausted its retries")

    monkeypatch.setattr(nfl_runner, "get_home_teams_with_retry", boom)
    launch_calls = _patch_launch(monkeypatch, {})

    with pytest.raises(SystemExit) as exc_info:
        nfl_runner.main()

    assert exc_info.value.code == 1
    assert launch_calls == []


# ── main(): per-team outcome classification ─────────────────────────────────

def test_all_teams_succeeding_does_not_exit_and_regenerates_all_team_pages(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs", "bills"])
    _patch_launch(monkeypatch, {"chiefs": 0, "bills": 0})
    run_calls = _patch_subprocess_run(monkeypatch)

    nfl_runner.main()  # must not raise

    story_slugs = [cmd[2] for cmd in run_calls if len(cmd) > 1 and "generate_nfl_story.py" in cmd[1]]
    # More than one team ran, so every NFL team's story page is refreshed,
    # not just tonight's two (a team with older data still gets a refresh).
    assert sorted(story_slugs) == sorted(nfl_runner.NFL_TEAMS.keys())


def test_single_team_run_only_regenerates_that_teams_own_page(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 0})
    run_calls = _patch_subprocess_run(monkeypatch)

    nfl_runner.main()

    story_slugs = [cmd[2] for cmd in run_calls if len(cmd) > 1 and "generate_nfl_story.py" in cmd[1]]
    assert story_slugs == ["chiefs"]


def test_bot_detection_exit_code_is_classified_as_failed_and_exits_1(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs", "bills"])
    _patch_launch(monkeypatch, {"chiefs": 0, "bills": 2})  # 2 == Bot Detection
    _patch_subprocess_run(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        nfl_runner.main()

    assert exc_info.value.code == 1


def test_a_failed_team_still_lets_successful_teams_push_their_data(monkeypatch, tmp_path):
    # A partial failure must not block the teams that *did* succeed from
    # being committed/pushed — confirmed as the intended behavior by the
    # "succeeded" list being pushed regardless of `failed` being non-empty.
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs", "bills"])
    _patch_launch(monkeypatch, {"chiefs": 0, "bills": 1})
    run_calls = _patch_subprocess_run(monkeypatch)

    with pytest.raises(SystemExit):
        nfl_runner.main()

    assert any(cmd[:2] == ["git", "commit"] for cmd in run_calls)
    assert any(cmd[:2] == ["git", "push"] for cmd in run_calls)


def test_nonzero_exit_without_log_file_prints_warning_but_does_not_crash(monkeypatch, tmp_path):
    # data/nfl/<slug>/game.log doesn't exist in this tmp cwd -- main() must
    # handle the OSError from trying to tail it instead of crashing.
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 1})
    _patch_subprocess_run(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        nfl_runner.main()

    assert exc_info.value.code == 1


def test_nonzero_exit_with_a_log_file_tails_it_without_crashing(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    log_dir = tmp_path / "data" / "nfl" / "chiefs"
    log_dir.mkdir(parents=True)
    (log_dir / "game.log").write_text("line one\nline two\nPlaywright timeout\n")

    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 1})
    _patch_subprocess_run(monkeypatch)

    with pytest.raises(SystemExit):
        nfl_runner.main()

    out = capsys.readouterr().out
    assert "Playwright timeout" in out


# ── main(): seat-map regeneration only for teams with captured geometry ────

def test_seatmap_regen_skipped_for_a_team_with_no_captured_geometry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 0})
    run_calls = _patch_subprocess_run(monkeypatch)
    # No data/chiefs/seatmap_geo.json created in tmp_path.

    nfl_runner.main()

    assert not any("extract_stadium_geo.py" in cmd for cmd in run_calls)
    assert not any("build_stadium_seatmap.py" in cmd for cmd in run_calls)


def test_seatmap_regen_runs_for_a_team_with_captured_geometry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    geo_dir = tmp_path / "data" / "chiefs"
    geo_dir.mkdir(parents=True)
    (geo_dir / "seatmap_geo.json").write_text("{}")

    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 0})
    run_calls = _patch_subprocess_run(monkeypatch)

    nfl_runner.main()

    assert any(len(cmd) > 1 and "extract_stadium_geo.py" in cmd[1] and cmd[2] == "chiefs" for cmd in run_calls)
    assert any(len(cmd) > 1 and "build_stadium_seatmap.py" in cmd[1] and cmd[2] == "chiefs" for cmd in run_calls)


# ── main(): git push must be a hard failure, not a silent no-op ────────────

def test_git_push_failing_all_three_attempts_is_a_hard_failure(monkeypatch, tmp_path):
    # Regression test for the 2026-09-20 incident: a dropped push used to
    # just print a message and move on, silently losing the run's commit
    # while main() still reported success (exit 0). It must now exit 1 after
    # 3 push attempts, pulling/rebasing after each failed one.
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 0})
    run_calls = _patch_subprocess_run(monkeypatch, push_results=[1, 1, 1])

    with pytest.raises(SystemExit) as exc_info:
        nfl_runner.main()

    assert exc_info.value.code == 1
    push_attempts = [cmd for cmd in run_calls if cmd[:2] == ["git", "push"]]
    rebase_pulls = [cmd for cmd in run_calls if cmd[:3] == ["git", "pull", "--rebase"]]
    assert len(push_attempts) == 3
    assert len(rebase_pulls) == 3  # the loop pulls/rebases after every failed attempt, including the last


def test_git_push_succeeding_on_a_retry_is_not_a_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_schedule(monkeypatch, ["chiefs"])
    _patch_launch(monkeypatch, {"chiefs": 0})
    run_calls = _patch_subprocess_run(monkeypatch, push_results=[1, 0])

    nfl_runner.main()  # must not raise

    push_attempts = [cmd for cmd in run_calls if cmd[:2] == ["git", "push"]]
    assert len(push_attempts) == 2


# ── launch_team(): the subprocess/log-file setup real teams actually run on ─

def test_launch_team_creates_log_dir_and_writes_a_run_header(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, stdout=None, stderr=None):
            popen_calls.append({"cmd": cmd, "stdout": stdout, "stderr": stderr})
            self.pid = 4242

    monkeypatch.setattr(nfl_runner.subprocess, "Popen", FakePopen)

    proc, log_file = nfl_runner.launch_team("chiefs", "2026-11-23")
    log_file.close()

    assert os.path.isdir("data/nfl/chiefs")
    log_text = (tmp_path / "data" / "nfl" / "chiefs" / "game.log").read_text()
    assert "Run started" in log_text

    assert len(popen_calls) == 1
    call = popen_calls[0]
    assert call["cmd"] == [nfl_runner.PYTHON, "nfl_run_game.py", "chiefs", "2026-11-23"]
    assert proc.pid == 4242


def test_launch_team_appends_to_an_existing_log_instead_of_truncating_it(monkeypatch, tmp_path):
    # A team can run more than once in a day (e.g. a manual re-run after a
    # failure) -- the prior attempt's log must stay, not be wiped.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(nfl_runner.subprocess, "Popen", lambda *a, **kw: FakeProc(1, 0))

    log_dir = tmp_path / "data" / "nfl" / "chiefs"
    log_dir.mkdir(parents=True)
    (log_dir / "game.log").write_text("earlier attempt's output\n")

    _, log_file = nfl_runner.launch_team("chiefs", "2026-11-23")
    log_file.close()

    log_text = (log_dir / "game.log").read_text()
    assert "earlier attempt's output" in log_text
    assert "Run started" in log_text
