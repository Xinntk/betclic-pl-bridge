from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from requests.exceptions import ReadTimeout

import app
import tennis_competitions as discovery
from betclic_api.models import Match
from betclic_api.proto import encode_field_string as string, encode_field_varint as integer, grpc_frame


def event(identity, competition_id=196, competition="US Open M.", date="2026-09-04T16:10:00Z", name="A - B"):
    return Match(id=identity, competition_id=competition_id, competition=competition, date=date, name=name)


@pytest.fixture
def backend(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 4, 12, tzinfo=app.WARSAW).astimezone(tz)

    state = SimpleNamespace(
        menu=[
            discovery.TennisCompetition(196, "US Open M.", "ATP"),
            discovery.TennisCompetition(211, "US Open K.", "WTA"),
            discovery.TennisCompetition(18015, "Como", "Challenger"),
            discovery.TennisCompetition(7, "ATP 500"),
            discovery.TennisCompetition(3962, "US Open Debel M.", "ATP"),
            discovery.TennisCompetition(10, "US Open Mixed", "WTA"),
            discovery.TennisCompetition(11, "ITF M15", "ITF"),
            discovery.TennisCompetition(12, "US Open Junior", "ATP"),
            discovery.TennisCompetition(13, "UTR Miami", "Turniej pokazowy"),
            discovery.TennisCompetition(14, "ATP 250 College"),
            discovery.TennisCompetition(15, "ATP 500 Exhibition"),
            discovery.TennisCompetition(16, "US Open", "Zwycięzca"),
        ],
        results={}, calls=[], menu_calls=0, forbidden_calls=[], barrier=None,
    )
    state.results = {item.id: [event(item.id, item.id, item.name)] for item in state.menu}

    def menu(client):
        state.menu_calls += 1
        if isinstance(state.menu, Exception):
            raise state.menu
        return state.menu

    def fetch(client, competition_id):
        state.calls.append(competition_id)
        assert client.timeout == (1, app.TODAY_TENNIS_TIMEOUT)
        if state.barrier:
            state.barrier.wait(timeout=2)
        result = state.results[competition_id]
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result

    def forbidden(*args, **kwargs):
        state.forbidden_calls.append(args)
        raise AssertionError("SearchService and global feed must never be used for tennis discovery")

    monkeypatch.setattr(app, "datetime", FrozenDatetime)
    monkeypatch.setattr(app, "TODAY_TENNIS_TIMEOUT", 4)
    monkeypatch.setattr(app, "TODAY_TENNIS_WORKERS", 2)
    monkeypatch.setattr(app, "SPORT_MENU_CACHE_TTL", 600)
    monkeypatch.setattr(app, "_tennis_menu_cache", None)
    monkeypatch.setattr(app, "_cache", {})
    monkeypatch.setattr(app, "fetch_sport_menu", menu)
    monkeypatch.setattr(app, "fetch_competition_matches", fetch)
    monkeypatch.setattr(app, "_fetch_matches_page", forbidden)
    monkeypatch.setattr(app.BetclicClient, "get_matches", forbidden)
    monkeypatch.setattr(app.BetclicClient, "search", forbidden)
    monkeypatch.setattr(app.BetclicClient, "get_match", forbidden)
    return state


@pytest.mark.parametrize("chunk", [0, 9])
def test_tennis_discovers_only_allowed_competitions_in_parallel(backend, chunk):
    backend.barrier = Barrier(2)
    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": "tennis", "chunk": chunk})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "competitions"
    assert data["competition_ids"] == [196, 211, 18015, 7]
    assert sorted(backend.calls) == sorted(data["competition_ids"])
    assert backend.forbidden_calls == []
    assert data["returned"] == 4
    assert data["partial"] is False
    assert data["errors"] == []
    assert all("markets" not in item for item in data["events"])
    assert "chunk" not in data


def test_tennis_deduplicates_filters_warsaw_day_and_competition(backend):
    backend.results[196] = [
        event(1), event(1), event(2, date="2026-09-03T22:00:00Z"),
        event(3, date="2026-09-04T22:00:00Z"), event(4, date="2026-09-03T21:59:00Z"),
        event(5, name="A/B - C/D"), event(6, competition="ITF M15"),
        event(7, date=None), event(8, date="invalid"),
    ]
    backend.results[211] = [event(1), event(9, 211, "US Open K.")]
    data = app.today_events(sport="tennis", competition="US OPEN")
    assert [item["id"] for item in data["events"]] == [1, 2, 9]
    assert data["filtered_out"] == 2
    assert data["returned"] == 3
    assert data["errors"] == []


def test_sport_menu_cached_for_ten_minutes(backend, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    app._fetch_tennis_menu()
    clock[0] += 599
    app._fetch_tennis_menu()
    assert backend.menu_calls == 1
    clock[0] += 1
    app._fetch_tennis_menu()
    assert backend.menu_calls == 2


@pytest.mark.parametrize("failure", [ReadTimeout("competition timeout"), RuntimeError("upstream failure")])
def test_one_competition_error_returns_partial_http_result(backend, failure):
    backend.results[196] = failure
    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": "tennis"})
    assert response.status_code == 200
    data = response.json()
    assert data["returned"] == 3
    assert data["partial"] is True
    assert data["errors"] == [{"competition_id": 196, "detail": str(failure)}]
    assert backend.forbidden_calls == []


def test_menu_error_does_not_fall_back_to_search_or_feed(backend):
    backend.menu = ReadTimeout("menu timeout")
    data = app.today_events(sport="tennis", competition=None)
    assert data["competition_ids"] == []
    assert data["returned"] == 0
    assert data["partial"] is True
    assert data["errors"] == [{"stage": "sport_menu", "detail": "menu timeout"}]
    assert backend.calls == backend.forbidden_calls == []
    assert app._tennis_menu_cache is None


def test_tennis_deadline_returns_before_slow_worker_finishes(backend, monkeypatch):
    monkeypatch.setattr(app, "TODAY_TENNIS_TIMEOUT", 0.05)
    release, started, finished = Event(), Event(), Event()

    def slow():
        started.set()
        release.wait(timeout=10)
        finished.set()
        return []

    backend.results[196] = slow
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(app.today_events, sport="tennis", competition=None)
        try:
            assert started.wait(timeout=1)
            data = future.result(timeout=3)
            assert not finished.is_set()
            assert data["returned"] == 3
            assert data["partial"] is True
            assert data["errors"] == [{"competition_id": 196, "detail": "Competition discovery timeout after 1.05s"}]
        finally:
            release.set()
            assert finished.wait(timeout=1)


def competition_proto(identity, name, sport="tennis", multi=False):
    return integer(1, identity) + string(2, name) + string(3, sport) + integer(6, int(multi))


def test_sport_menu_parser_handles_categories_countries_and_duplicates():
    us_open = competition_proto(196, "US Open M.")
    category = string(1, "ATP") + string(2, us_open)
    country = string(1, "US") + string(3, competition_proto(211, "US Open K."))
    tennis = (string(2, "tennis") + string(1, us_open) + string(5, us_open)
              + string(6, category) + string(7, country)
              + string(5, competition_proto(999, "Multi", multi=True)))
    golf = string(2, "golf") + string(5, competition_proto(103, "US Open", sport="golf"))
    result = discovery.parse_sport_menu(string(2, tennis) + string(2, golf))
    assert result == [discovery.TennisCompetition(196, "US Open M.", "ATP"),
                      discovery.TennisCompetition(211, "US Open K.")]


def test_competition_parser_handles_real_us_open_timestamp_and_live_field():
    # Values observed by diagnose_tennis.py on 2026-09-04.
    raw_match = (integer(1, 1214690727329792) + string(2, "Daniil Medvedev - Arthur Rinderknech")
                 + string(3, "2026-09-04T16:10:00.0000000Z") + integer(4, 0) + integer(6, 1)
                 + string(8, integer(1, 196) + string(2, "US Open M.")))
    parsed = discovery.parse_competition_matches(string(3, raw_match))[0]
    assert parsed.id == 1214690727329792
    assert parsed.competition_id == 196
    assert parsed.date == "2026-09-04T16:10:00.000000Z"
    assert app._parse_dt(parsed.date).isoformat() == "2026-09-04T18:10:00+02:00"
    assert parsed.is_live is False
    assert parsed.markets == []


class FakeResponse:
    def __init__(self, pieces):
        self.pieces = iter(pieces)
        self.raw = self
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        pass

    def read1(self, size, decode_content):
        assert decode_content is True
        return next(self.pieces)  # Reading beyond the first full snapshot fails the test.


def test_transport_reads_first_snapshot_without_waiting_for_notifications():
    raw_match = integer(1, 10) + string(2, "A - B")
    frame = grpc_frame(string(1, string(3, raw_match)))
    response = FakeResponse([frame[:3], frame[3:8], frame[8:]])
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response

    client = SimpleNamespace(locale="pl", timeout=(1, 4), _session=SimpleNamespace(post=post))
    result = discovery.fetch_competition_matches(client, 196)
    assert result[0].id == 10
    assert response.closed
    assert calls[0][0].endswith("offering.access.api.MatchService/GetMatchesByCompetitionWithNotifications")
    assert calls[0][1]["data"] == grpc_frame(integer(1, 196) + string(3, "pl"))
    assert calls[0][1]["stream"] is True


def test_menu_transport_uses_verified_request_and_response_fields():
    menu = string(2, string(2, "tennis") + string(5, competition_proto(196, "US Open M.")))
    response = FakeResponse([grpc_frame(string(3, menu))])

    def post(url, **kwargs):
        assert url.endswith("offering.access.api.SportMenuService/GetSportMenu")
        assert kwargs["data"] == grpc_frame(string(1, "pl"))
        return response

    client = SimpleNamespace(locale="pl", timeout=(1, 4), _session=SimpleNamespace(post=post))
    assert discovery.fetch_sport_menu(client)[0].id == 196
    assert response.closed


def test_grpc_error_is_not_treated_as_empty_success():
    frame = grpc_frame(b"grpc-status: 13\r\ngrpc-message: unavailable\r\n")
    response = FakeResponse([b"\x80" + frame[1:]])
    client = SimpleNamespace(locale="pl", timeout=(1, 4), _session=SimpleNamespace(post=lambda *a, **kw: response))
    with pytest.raises(RuntimeError, match="grpc-status: 13"):
        discovery.fetch_sport_menu(client)
    assert response.closed
