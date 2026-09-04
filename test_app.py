from datetime import datetime, timedelta
from threading import Barrier, Event, Lock
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app
import pytest


@pytest.fixture(autouse=True)
def today_config(monkeypatch):
    monkeypatch.setattr(app, "TODAY_WORKERS", 4)
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", 12)
    monkeypatch.setattr(app, "TODAY_PAGES_PER_CHUNK", 4)


@pytest.fixture(params=["football", "tennis"])
def sport(request):
    return request.param


def match(match_id, date, competition="Liga", markets=None):
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
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or pages[offset])
    monkeypatch.setattr(app, "_fetch_event", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected detail call")))

    data = app.today_events(sport="football", competition=None)

    assert data["returned"] == 41
    assert sorted(calls) == [0, 40, 80, 120]
    assert data["pages_scanned"] == 4
    assert data["errors"] == []
    assert all("markets" not in event for event in data["events"])


def test_today_competition_filter(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    page = {"matches": [match(1, today, "Ekstraklasa"), match(2, today, "La Liga")], "total": 2}
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_: page)

    data = app.today_events(sport="football", competition="ekstra")

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
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or pages[offset])

    matches, scan = app._fetch_today("tennis")

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
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or pages[offset])

    data = app.today_events(sport=sport, competition=None)

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
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 0,
    })

    data = app.today_events(sport="football", competition=None)

    assert sorted(calls) == [0, 40, 80]
    assert data["pages_scanned"] == 3
    assert data["batches_scanned"] == 1
    assert data["partial"] is True
    assert data["returned"] == 3


def test_today_counts_empty_page(monkeypatch):
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_: {"matches": [], "total": 0})

    data = app.today_events(sport="football", competition=None)

    assert data["pages_scanned"] == 4
    assert data["batches_scanned"] == 1
    assert data["events"] == []


@pytest.mark.parametrize("sport,workers", [("football", 4), ("tennis", 2)])
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

    def fetch(requested_sport, offset):
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

    data = app.today_events(sport=sport, competition=None)

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

    def fetch(sport, offset):
        calls.append(offset)
        if offset == failed_offset:
            raise app.HTTPException(status_code=502, detail="Betclic upstream timeout")
        return {"matches": [match(offset, tomorrow if offset >= 80 else today)], "total": 0}

    monkeypatch.setattr(app, "_fetch_matches_page", fetch)

    data = app.today_events(sport="football", competition=None)

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
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 81,
    })

    data = app.today_events(sport="football", competition=None)

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
        def get_matches(self, requested_sport, offset):
            assert requested_sport == sport
            calls.append(offset)
            page_index = offset // 40
            event = match(offset, today, "Selected League")
            events = [event, event, match(offset + 1, today, "Other League")]
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
            params = {"sport": sport, "competition": "SELECTED"}
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
            assert all(event["competition"] == "Selected League" and "markets" not in event for event in data["events"])
            collected_ids.extend(event["id"] for event in data["events"])

    assert collected_ids == list(range(0, (final_page + 1) * 40, 40))
    assert detail_calls == []


def test_today_legacy_cap_keeps_chunks_contiguous(monkeypatch, sport):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", 3)
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 0,
    })

    for chunk in range(2):
        data = app.today_events(sport=sport, competition=None, chunk=chunk)
        assert data["pages_scanned"] == 3
        assert data["done"] is False
        assert data["next_chunk"] == chunk + 1
    assert sorted(calls) == list(range(0, 240, 40))


def test_today_empty_competition_result_does_not_end_chunks(monkeypatch, sport):
    today = datetime.now(app.WARSAW).isoformat()
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_: {
        "matches": [match(1, today, "Other League")], "total": 0,
    })

    data = app.today_events(sport=sport, competition="Selected", chunk=2)

    assert data["events"] == []
    assert data["done"] is False
    assert data["next_chunk"] == 3


def test_today_failed_chunk_does_not_signal_end(monkeypatch, sport):
    calls = []

    def fetch(requested_sport, offset):
        calls.append(offset)
        raise app.HTTPException(status_code=502, detail="Betclic upstream timeout")

    monkeypatch.setattr(app, "_fetch_matches_page", fetch)

    data = app.today_events(sport=sport, competition=None, chunk=1)

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
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *args: calls.append(args))

    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": sport, "chunk": chunk})

    assert response.status_code == 422
    assert calls == []


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
