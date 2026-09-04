from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace

from fastapi.testclient import TestClient
from requests.exceptions import ReadTimeout

import app
import pytest


@pytest.fixture(autouse=True)
def today_config(monkeypatch):
    monkeypatch.setattr(app, "TODAY_WORKERS", 4)
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", 12)
    monkeypatch.setattr(app, "TODAY_PAGES_PER_CHUNK", 4)
    monkeypatch.setattr(app, "TODAY_UPSTREAM_TIMEOUT", 5)
    monkeypatch.setattr(app, "TODAY_TENNIS_TIMEOUT", 4)


@pytest.fixture
def sport():
    return "football"


def match(match_id, date, competition="ATP 250", markets=None):
    return SimpleNamespace(
        id=match_id,
        name=f"Mecz {match_id}",
        date=date,
        competition=competition,
        competition_id=1,
        is_live=False,
        teams=[],
        markets=markets or [],
    )


def test_today_paginates_without_fetching_event(monkeypatch):
    now = datetime.now(app.WARSAW)
    today = now.replace(hour=12).isoformat()
    tomorrow = (now + timedelta(days=1)).replace(hour=12).isoformat()
    pages = {
        0: {"matches": [match(i, today) for i in range(40)], "total": 42},
        40: {"matches": [match(40, today), match(41, tomorrow)], "total": 42},
        80: {"matches": [], "total": 42},
        120: {"matches": [], "total": 42},
    }
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kwargs: calls.append(offset) or pages[offset])
    monkeypatch.setattr(app, "_fetch_event", lambda *_, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected detail call")))

    data = app.today_events(scope="all", sport="football", competition=None)

    assert data["returned"] == 41
    assert sorted(calls) == [0, 40, 80, 120]
    assert data["pages_scanned"] == 4
    assert data["errors"] == []
    assert all("markets" not in event for event in data["events"])


def test_today_competition_filter(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    page = {"matches": [match(1, today, "Ekstraklasa"), match(2, today, "La Liga")], "total": 2}
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_, **kwargs: page)

    data = app.today_events(scope="all", sport="football", competition="ekstra")

    assert data["returned"] == 1
    assert data["events"][0]["competition"] == "Ekstraklasa"


def test_today_uses_fixed_offsets_even_for_short_pages(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    pages = {
        0: {"matches": [match(i, today) for i in range(20)], "total": 0},
        40: {"matches": [match(20, today)], "total": 0},
        80: {"matches": [], "total": 0},
        120: {"matches": [], "total": 0},
    }
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kwargs: calls.append(offset) or pages[offset])

    matches, scan = app._fetch_today("football", scope="all")

    assert len(matches) == 21
    assert sorted(calls) == [0, 40, 80, 120]
    assert scan["pages_scanned"] == 4
    assert scan["errors"] == []


def test_today_stops_after_page_with_future_events_when_total_is_zero(monkeypatch, sport):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 4, 12, tzinfo=app.WARSAW).astimezone(tz)

    monkeypatch.setattr(app, "datetime", FrozenDatetime)
    monkeypatch.setattr(app, "TODAY_PAGES_PER_CHUNK", 8)
    # UTC timestamps cross midnight in Warsaw two hours earlier than in UTC.
    today = "2026-09-04T21:59:00Z"
    tomorrow = "2026-09-04T22:00:00Z"
    pages = {
        0: {"matches": [match(1, today)], "total": 0},
        40: {"matches": [match(2, today), match(3, tomorrow), match(4, today)], "total": 0},
        80: {"matches": [match(5, tomorrow)], "total": 0},
        120: {"matches": [match(6, tomorrow)], "total": 0},
    }
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kwargs: calls.append(offset) or pages[offset])

    data = app.today_events(scope="all", sport=sport, competition=None)

    assert sorted(calls) == [0, 40, 80, 120]
    assert data["pages_scanned"] == 4
    assert data["batches_scanned"] == 1
    assert data["errors"] == []
    assert data["partial"] is False
    assert data["upstream_total"] == 0
    assert data["returned"] == 3
    assert data["done"] is True
    assert data["next_chunk"] is None
    assert [event["id"] for event in data["events"]] == [1, 2, 4]


def test_today_stops_at_page_limit_when_total_is_zero(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    calls = []
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", 3)
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kwargs: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 0,
    })

    data = app.today_events(scope="all", sport="football", competition=None)

    assert sorted(calls) == [0, 40, 80]
    assert data["pages_scanned"] == 3
    assert data["batches_scanned"] == 1
    assert data["partial"] is True
    assert data["returned"] == 3


def test_today_counts_empty_page(monkeypatch):
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_, **kwargs: {"matches": [], "total": 0})

    data = app.today_events(scope="all", sport="football", competition=None)

    assert data["pages_scanned"] == 4
    assert data["batches_scanned"] == 1
    assert data["events"] == []


@pytest.mark.parametrize("sport,workers", [("football", 4), ("football", 2)])
def test_today_fetches_parallel_batches_in_offset_order(monkeypatch, sport, workers):
    today = datetime.now(app.WARSAW).isoformat()
    max_pages = workers + 2
    monkeypatch.setattr(app, "TODAY_WORKERS", workers)
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", max_pages)
    monkeypatch.setattr(app, "TODAY_PAGES_PER_CHUNK", max_pages)
    barriers = [Barrier(workers), Barrier(2)]
    last_finished = [Event(), Event()]
    lock = Lock()
    completed = []

    def fetch(requested_sport, offset, **kwargs):
        assert requested_sport == sport
        page_index = offset // 40
        batch = page_index // workers
        with lock:
            if batch:
                assert set(range(0, workers * 40, 40)).issubset(completed)
        # A sequential implementation cannot pass this barrier.
        barriers[batch].wait(timeout=5)
        first = batch * workers * 40
        last = (min((batch + 1) * workers, max_pages) - 1) * 40
        if offset == first:
            assert last_finished[batch].wait(timeout=5)
        with lock:
            completed.append(offset)
        if offset == last:
            last_finished[batch].set()
        return {"matches": [match(offset, today), match(None, today)], "total": 0}

    monkeypatch.setattr(app, "_fetch_matches_page", fetch)

    data = app.today_events(scope="all", sport=sport, competition=None)

    assert data["errors"] == []
    assert data["pages_scanned"] == max_pages
    assert data["batches_scanned"] == 2
    assert sorted(completed) == list(range(0, max_pages * 40, 40))
    assert completed.index((workers - 1) * 40) < completed.index(0)
    assert [event["id"] for event in data["events"]] == [0, None, *range(40, max_pages * 40, 40)]


@pytest.mark.parametrize("failed_offset", [0, 40])
def test_today_returns_partial_results_after_page_error(monkeypatch, failed_offset):
    now = datetime.now(app.WARSAW)
    today = now.isoformat()
    tomorrow = (now + timedelta(days=1)).isoformat()
    monkeypatch.setattr(app, "TODAY_WORKERS", 2)
    calls = []

    def fetch(sport, offset, **kwargs):
        calls.append(offset)
        if offset == failed_offset:
            raise app.HTTPException(status_code=502, detail="Betclic upstream timeout")
        return {"matches": [match(offset, tomorrow if offset >= 80 else today)], "total": 0}

    monkeypatch.setattr(app, "_fetch_matches_page", fetch)

    data = app.today_events(scope="all", sport="football", competition=None)

    assert sorted(calls) == [0, 40, 80, 120]
    assert data["pages_scanned"] == 4
    assert data["batches_scanned"] == 2
    assert [event["id"] for event in data["events"]] == [40 - failed_offset]
    assert data["partial"] is True
    assert data["errors"] == [{"offset": failed_offset, "detail": "Betclic upstream timeout"}]


def test_today_stops_at_known_total_before_next_batch(monkeypatch):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "TODAY_WORKERS", 2)
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kwargs: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 81,
    })

    data = app.today_events(scope="all", sport="football", competition=None)

    assert sorted(calls) == [0, 40, 80]
    assert data["pages_scanned"] == 3
    assert data["batches_scanned"] == 2
    assert data["partial"] is False


@pytest.mark.parametrize("pages_per_chunk", [2, 4])
def test_today_multiple_chunks_via_http(monkeypatch, sport, pages_per_chunk):
    now = datetime.now(app.WARSAW)
    today = now.isoformat()
    tomorrow = (now + timedelta(days=1)).isoformat()
    monkeypatch.setattr(app, "TODAY_PAGES_PER_CHUNK", pages_per_chunk)
    calls = []
    detail_calls = []
    final_page = pages_per_chunk * 3

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_matches(self, requested_sport, offset):
            assert requested_sport == sport
            calls.append(offset)
            page_index = offset // 40
            event = match(offset, today, "ATP 250 Selected League")
            events = [event, event, match(offset + 1, today, "ATP 250 Other League")]
            if page_index == final_page:
                events.insert(0, match(-1, tomorrow))
            elif page_index > final_page:
                events = [match(offset, tomorrow)]
            return {"matches": events, "total": 0}

        def get_match(self, *_args, **_kwargs):
            detail_calls.append(True)
            raise AssertionError("/today must not fetch event details")

    monkeypatch.setattr(app, "_client", FakeClient)
    monkeypatch.setattr(app, "_cache", {})
    collected_ids = []
    with TestClient(app.app) as client:
        for chunk in range(4):
            calls.clear()
            params = {"scope": "all", "sport": sport, "competition": "SELECTED"}
            if chunk:
                params["chunk"] = chunk
            response = client.get("/today", params=params)
            assert response.status_code == 200
            data = response.json()
            assert data["chunk"] == chunk
            assert data["done"] is (chunk == 3)
            assert data["next_chunk"] == (None if chunk == 3 else chunk + 1)
            assert data["partial"] is (chunk != 3)
            assert data["pages_scanned"] == pages_per_chunk
            assert data["batches_scanned"] == 1
            assert data["errors"] == []
            assert sorted(calls) == list(range(chunk * pages_per_chunk * 40, (chunk + 1) * pages_per_chunk * 40, 40))
            assert all(event["competition"] == "ATP 250 Selected League" and "markets" not in event for event in data["events"])
            collected_ids.extend(event["id"] for event in data["events"])

    assert collected_ids == list(range(0, (final_page + 1) * 40, 40))
    assert detail_calls == []


def test_today_legacy_cap_keeps_chunks_contiguous(monkeypatch, sport):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", 3)
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kwargs: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 0,
    })

    for chunk in range(2):
        data = app.today_events(scope="all", sport=sport, competition=None, chunk=chunk)
        assert data["pages_scanned"] == 3
        assert data["done"] is False
        assert data["next_chunk"] == chunk + 1
    assert sorted(calls) == list(range(0, 240, 40))


def test_today_empty_competition_result_does_not_end_chunks(monkeypatch, sport):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_, **kwargs: {
        "matches": [match(1, today, "ATP 250 Other League")], "total": 0,
    })

    data = app.today_events(scope="all", sport=sport, competition="Selected", chunk=2)

    assert data["events"] == []
    assert data["done"] is False
    assert data["next_chunk"] == 3


def test_today_failed_chunk_does_not_signal_end(monkeypatch, sport):
    calls = []

    def fetch(requested_sport, offset, **kwargs):
        calls.append(offset)
        raise app.HTTPException(status_code=502, detail="Betclic upstream timeout")

    monkeypatch.setattr(app, "_fetch_matches_page", fetch)

    data = app.today_events(scope="all", sport=sport, competition=None, chunk=1)

    assert sorted(calls) == [160, 200, 240, 280]
    assert data["pages_scanned"] == 4
    assert data["events"] == []
    assert data["done"] is False
    assert data["next_chunk"] == 2
    assert data["partial"] is True
    assert data["errors"] == [{"offset": offset, "detail": "Betclic upstream timeout"} for offset in sorted(calls)]


@pytest.mark.parametrize("chunk", ["-1", "invalid"])
def test_today_rejects_invalid_chunk(monkeypatch, sport, chunk):
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *args, **kwargs: calls.append(args))

    with TestClient(app.app) as client:
        response = client.get("/today", params={"scope": "all", "sport": sport, "chunk": chunk})

    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize("competition", [
    "Australian Open", "Roland Garros", "French Open", "Wimbledon", "US Open",
    "ATP Masters 1000 Rome", "ATP 1000", "WTA 1000 Madrid",
    "ATP 500", "WTA 500", "ATP 250", "WTA 250",
    "ATP Finals", "WTA Finals", "ATP Challenger", "Challenger Tour",
    "ATP Challenger Poznan - kwalifikacje", "WTA 250 - qualifying",
    "US Open - Qualifications", "wTa-1000 - qUaLiFyInG",
    "ATP/WTA 500", "Roland-Garros", "ATP Tour 250",
])
def test_allowed_tennis_categories(competition):
    assert app._is_allowed_tennis_match(match(1, None, competition))


@pytest.mark.parametrize("competition,name", [
    ("Wimbledon Doubles", "A - B"), ("US Open mixed doubles", "A - B"),
    ("ATP 500 - Debel", "A - B"), ("WTA 1000 - Deble", "A - B"),
    ("Roland Garros - mikst", "A - B"), ("ATP 250", "A/B - C/D"),
    ("WTA 250", "A & B - C & D"), ("ATP 500", "A + B - C + D"),
    ("Wimbledon gra podwójna", "A - B"),
    ("ATP 250", "A - B (DOUBLES)"), ("WTA Finals", "A - B (mixed)"),
    ("ITF - ATP Challenger", "A - B"), ("ATP 250 Juniors", "A - B"),
    ("Wimbledon Girls", "A - B"), ("US Open Boys", "A - B"),
    ("Wimbledon U18", "A - B"), ("US Open juniorskie", "A - B"),
    ("ATP 250 UTR", "A - B"), ("ATP 250 College", "A - B"),
    ("ATP 250 NCAA", "A - B"), ("ATP 250 Exhibition", "A - B"),
    ("ATP 250 pokazowy", "A - B"), ("ATP 250 amateur", "A - B"),
    ("WTA 125", "A - B"), ("Next Gen ATP Finals", "A - B"),
    ("Local League", "A - B"), (None, "ATP 250 A - B"),
    ("ATP 250", "A - B (UTR)"),
])
def test_excluded_tennis_categories(competition, name):
    event = match(1, None, competition)
    event.name = name
    assert not app._is_allowed_tennis_match(event)


@pytest.mark.parametrize("category", ["M15", "M25", "W15", "W25", "W35", "W50", "W75", "W100"])
def test_tennis_itf_tiers_override_allowed_labels(category):
    assert not app._is_allowed_tennis_match(match(1, None, f"ATP Challenger {category.lower()}"))
    assert not app._is_allowed_tennis_match(match(1, None, f"ITF {category}"))


def test_today_upstream_read_timeout_returns_http_partial_result(monkeypatch, sport):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "_cache", {})
    timeouts = []

    class FakeClient:
        def __init__(self, locale, timeout):
            self.timeout = timeout
            self._session = SimpleNamespace(headers={})

        def get_matches(self, requested_sport, offset):
            timeouts.append(self.timeout)
            if offset == 0:
                raise ReadTimeout("slow Betclic page")
            return {"matches": [match(offset, today, "ATP 250")], "total": 0}

    monkeypatch.setattr(app, "BetclicClient", FakeClient)
    with TestClient(app.app) as client:
        response = client.get("/today", params={"scope": "all", "sport": sport})

    expected_pages = 4
    assert response.status_code == 200
    data = response.json()
    assert data["partial"] is True
    assert data["pages_scanned"] == expected_pages
    assert data["returned"] == expected_pages - 1
    assert data["errors"] == [{"offset": 0, "detail": "Betclic upstream error: slow Betclic page"}]
    assert timeouts == [(1, 5)] * expected_pages
    assert app._client().timeout == (5, 12)


def test_today_does_not_wait_for_slow_worker_shutdown(monkeypatch):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "TODAY_UPSTREAM_TIMEOUT", 0.05)
    monkeypatch.setattr(app, "TODAY_PAGES_PER_CHUNK", 2)
    release = Event()
    started = Event()
    finished = Event()

    def fetch(sport, offset, **kwargs):
        if offset == 0:
            started.set()
            release.wait(timeout=10)
            finished.set()
        return {"matches": [match(offset, today, "ATP 250")], "total": 0}

    monkeypatch.setattr(app, "_fetch_matches_page", fetch)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(app.today_events, scope="all", sport="football", competition=None)
        try:
            assert started.wait(timeout=1)
            data = future.result(timeout=3)
            assert not finished.is_set()
            assert [event["id"] for event in data["events"]] == [40]
            assert data["partial"] is True
            assert data["errors"] == [{"offset": 0, "detail": "Betclic upstream timeout after 1.05s"}]
        finally:
            release.set()
            assert finished.wait(timeout=1)


def test_event_compact_removes_suspended_and_heavy_markets(monkeypatch):
    selection = lambda name: SimpleNamespace(name=name, odds=2.0)
    markets = [
        SimpleNamespace(name="Wynik meczu", suspended=False, selections=[selection("A"), selection("B")]),
        SimpleNamespace(name="Dokładny wynik", suspended=False, selections=[selection(str(i)) for i in range(15)]),
        SimpleNamespace(name="Podwójna szansa", suspended=True, selections=[selection("A lub B")]),
    ]
    monkeypatch.setattr(app, "_fetch_event", lambda *_args, **_kwargs: match(7, datetime.now(app.WARSAW).isoformat(), markets=markets))

    data = app.event(7, category=None, compact=True, include_suspended=False)

    assert [market["name"] for market in data["markets"]] == ["Wynik meczu"]
