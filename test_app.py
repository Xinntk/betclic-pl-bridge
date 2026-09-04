from datetime import datetime, timedelta
from types import SimpleNamespace

import app


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
    }
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or pages[offset])
    monkeypatch.setattr(app, "_fetch_event", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected detail call")))

    data = app.today_events(sport="football", competition=None)

    assert data["returned"] == 41
    assert calls == [0, 40]
    assert all("markets" not in event for event in data["events"])


def test_today_competition_filter(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    page = {"matches": [match(1, today, "Ekstraklasa"), match(2, today, "La Liga")], "total": 2}
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_: page)

    data = app.today_events(sport="football", competition="ekstra")

    assert data["returned"] == 1
    assert data["events"][0]["competition"] == "Ekstraklasa"


def test_today_does_not_assume_a_full_page(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    pages = {
        0: {"matches": [match(i, today) for i in range(20)], "total": 21},
        20: {"matches": [match(20, today)], "total": 21},
    }
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or pages[offset])

    matches, total, pages_scanned = app._fetch_today("tennis")

    assert len(matches) == total == 21
    assert calls == [0, 20]
    assert pages_scanned == 2


def test_today_stops_after_page_with_future_events_when_total_is_zero(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 4, 12, tzinfo=app.WARSAW).astimezone(tz)

    monkeypatch.setattr(app, "datetime", FrozenDatetime)
    # UTC timestamps cross midnight in Warsaw two hours earlier than in UTC.
    today = "2026-09-04T21:59:00Z"
    tomorrow = "2026-09-04T22:00:00Z"
    pages = {
        0: {"matches": [match(1, today)], "total": 0},
        1: {"matches": [match(2, today), match(3, tomorrow), match(4, today)], "total": 0},
    }
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or pages[offset])

    data = app.today_events(sport="football", competition=None)

    assert calls == [0, 1]
    assert data["pages_scanned"] == 2
    assert data["upstream_total"] == 0
    assert data["returned"] == 3
    assert [event["id"] for event in data["events"]] == [1, 2, 4]


def test_today_stops_at_page_limit_when_total_is_zero(monkeypatch):
    today = datetime.now(app.WARSAW).replace(hour=12).isoformat()
    calls = []
    monkeypatch.setattr(app, "TODAY_MAX_PAGES", 3)
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset: calls.append(offset) or {
        "matches": [match(offset, today)], "total": 0,
    })

    data = app.today_events(sport="football", competition=None)

    assert calls == [0, 1, 2]
    assert data["pages_scanned"] == 3
    assert data["returned"] == 3


def test_today_counts_empty_page(monkeypatch):
    monkeypatch.setattr(app, "_fetch_matches_page", lambda *_: {"matches": [], "total": 0})

    data = app.today_events(sport="football", competition=None)

    assert data["pages_scanned"] == 1
    assert data["events"] == []


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
