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

    matches, total = app._fetch_today("tennis")

    assert len(matches) == total == 21
    assert calls == [0, 20]


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
