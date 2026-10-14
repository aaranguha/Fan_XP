"""
Tests for fetch_listings.browser_slot(): the machine-wide cap on concurrent
Chrome windows that fixes the 1 PM-slate halftime timeouts (7-8 teams each
on 9/27, 10/4 and 10/11). flock locks are per open file description, so
threads in one process contend exactly like the real per-team processes.
"""

import threading
import time

import pytest

import fetch_listings


@pytest.fixture
def slots(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_listings, "SLOT_DIR", str(tmp_path))
    monkeypatch.setattr(fetch_listings, "BROWSER_SLOTS", 2)
    monkeypatch.setattr(fetch_listings.random, "uniform", lambda a, b: 0.01)
    return tmp_path


def _run_contenders(n, hold_s):
    active, peak, done = [0], [0], []
    lock = threading.Lock()

    def worker(i):
        with fetch_listings.browser_slot(f"team{i}"):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(hold_s)
            with lock:
                active[0] -= 1
        done.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return peak[0], done


def test_never_more_than_browser_slots_at_once(slots):
    peak, done = _run_contenders(n=7, hold_s=0.1)

    assert peak == 2
    assert sorted(done) == list(range(7))


def test_slot_is_released_when_scrape_raises(slots):
    with pytest.raises(RuntimeError):
        with fetch_listings.browser_slot("a"):
            raise RuntimeError("scrape failed")

    # Both slots must be free again: two holders can enter together.
    peak, done = _run_contenders(n=2, hold_s=0.1)
    assert peak == 2 and len(done) == 2


def test_proceeds_without_a_slot_after_max_wait(slots, monkeypatch):
    monkeypatch.setattr(fetch_listings, "BROWSER_SLOT_MAX_WAIT_S", 0.05)
    entered = []
    with fetch_listings.browser_slot("a"), fetch_listings.browser_slot("b"):
        with fetch_listings.browser_slot("c"):
            entered.append("c")

    assert entered == ["c"]
