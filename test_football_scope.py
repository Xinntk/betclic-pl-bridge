from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from requests.exceptions import ReadTimeout

import app
from betclic_api.models import Match, Team
from betclic_api.proto import encode_field_string as string, encode_field_varint as integer
from football_scope import _is_allowed_cup_match, is_allowed_football_competition, is_allowed_football_match, is_reserve_team
from tennis_competitions import FootballCompetition, parse_sport_menu


@pytest.mark.parametrize("country,name", [
    ("EN", "Premier League"), ("EN", "Anglia Championship"), ("EN", "League One"), ("EN", "Anglia League 1"),
    ("ES", "La Liga"), ("ES", "Segunda División"), ("ES", "Hiszpania 2. liga"),
    ("IT", "Serie A"), ("IT", "Serie B"), ("DE", "Bundesliga"), ("DE", "2. Bundesliga"),
    ("DE", "Niemcy Bundesliga 2"), ("DE", "3. Liga"), ("FR", "Ligue 1"), ("FR", "Ligue 2"),
    ("PL", "Ekstraklasa"), ("PL", "I Liga"), ("PL", "II Liga"), ("PL", "Betclic 1. Liga"), ("PL", "Betclic 2. Liga"),
    ("PT", "Liga Betclic"), ("PT", "Primeira Liga"), ("PT", "Segunda Liga"),
    ("NL", "Eredivisie"), ("NL", "Eerste Divisie"), ("NL", "Holandia 2. liga"),
    ("BE", "Jupiler Pro League"), ("BE", "Challenger Pro League"), ("BE", "Belgia 1. liga"), ("BE", "Belgia 2. liga"),
    ("TR", "Süper Lig"), ("TR", "TFF 1. Lig"), ("TR", "Turcja Lig 1"),
    ("ND", "Szkocja Premiership"), ("SC", "Scottish Championship"),
    ("AT", "Austria Bundesliga"), ("AT", "Austria 2. Liga"),
    ("CH", "Swiss Super League"), ("CH", "Challenge League"),
    ("CZ", "Chance Liga"), ("CZ", "Czechy 2. liga"),
    ("DK", "Superliga"), ("DK", "Dania Dywizja 1"),
    ("NO", "Eliteserien"), ("NO", "1. divisjon"),
    ("SE", "Allsvenskan"), ("SE", "Superettan"), ("GR", "Super League"), ("GR", "Super League 2"),
])
def test_allowed_domestic_leagues(country, name):
    assert is_allowed_football_competition(FootballCompetition(987654, name, country_code=country))


@pytest.mark.parametrize("name", [
    "UEFA Champions League", "UEFA Europa League", "UEFA Conference League", "UEFA Europa Conference League",
    "Liga Mistrzów - kwalifikacje", "Liga Europy - eliminacje", "Liga Konferencji - kwalifikacje",
    "UEFA Champions League Qualification", "World Cup", "MŚ", "EURO", "UEFA EURO 2028",
    "MŚ - kwalifikacje - Europa", "FIFA World Cup Qualification CONMEBOL",
    "World Cup Qualifiers South America", "EURO - kwalifikacje", "UEFA Nations League",
    "UEFA Liga Narodów", "Copa America", "Africa Cup of Nations", "CONCACAF Gold Cup", "AFC Asian Cup",
])
def test_allowed_official_international_competitions(name):
    assert is_allowed_football_competition(FootballCompetition(99999, name, country_code="ZZ"))


@pytest.mark.parametrize("country,name", [
    ("EN", "FA Cup"), ("EN", "EFL Cup"), ("EN", "Anglia Puchar Ligi"), ("ES", "Copa del Rey"),
    ("IT", "Coppa Italia"), ("DE", "DFB-Pokal"), ("FR", "Coupe de France"), ("PL", "Puchar Polski"),
    ("PT", "Taça de Portugal"), ("NL", "KNVB Beker"), ("BE", "Croky Cup"), ("TR", "Turcja Puchar"),
    ("SC", "Scottish Cup"), ("AT", "ÖFB Cup"), ("CH", "Swiss Cup"), ("CZ", "MOL Cup"),
    ("DK", "DBU Pokalen"), ("NO", "NM Cupen"), ("SE", "Svenska Cupen"), ("GR", "Greek Cup"),
])
def test_allowed_major_cups(country, name):
    assert is_allowed_football_competition(FootballCompetition(999, name, country_code=country))


@pytest.mark.parametrize("name,code", [
    ("Premier League Women", "EN"), ("Women's FA Cup", "EN"), ("UEFA Women's Champions League", "EU"),
    ("Liga Mistrzów Kobiet", "EU"), ("Ligue 1 Féminine", "FR"), ("Ekstraklasa K.", "PL"),
    ("Serie A Femminile", "IT"), ("Frauen Bundesliga", "DE"), ("EURO Women U19", "EU"),
    ("Premier League U19", "EN"), ("World Cup U20", "ZZ"), ("EURO U21", "EU"),
    ("UEFA Youth League", "EU"), ("Championship Academy", "EN"), ("Liga Juniorów", "PL"),
    ("Premier League Reserves", "EN"), ("Ekstraklasa Rezerw", "PL"), ("League One B Teams", "EN"),
    ("Championship Second Teams", "EN"), ("Premier League 2", "EN"), ("Primavera", "IT"),
    ("League Two", "EN"), ("Anglia League 2", "EN"), ("National League", "EN"),
    ("Regionalliga", "DE"), ("Oberliga", "DE"), ("III Liga", "PL"), ("Betclic 3. liga Gr.1", "PL"),
    ("Polska 4. Liga", "PL"), ("Ligue 3 Betclic", "FR"), ("Serie C", "IT"),
    ("Segunda RFEF", "ES"), ("Szkocja League 1", "ND"), ("Dania Dywizja 2", "DK"),
    ("Norwegia 2. Dywizja", "NO"), ("Tweede Divisie", "NL"),
    ("Premier League", "NZ"), ("Premier League", "IN"), ("Premier League", "MY"),
    ("A-League", "AU"), ("Premier League", "HK"), ("Serie A", "BR"),
    ("CAF - Liga Mistrzów", "ZZ"), ("AFC Champions League", "ZZ"), ("Club World Cup", "ZZ"),
    ("Premier League Amateur", "EN"), ("FA Cup Regional", "EN"), ("EURO Exhibition", "EU"),
])
def test_excluded_competitions(name, code):
    assert not is_allowed_football_competition(FootballCompetition(123, name, country_code=code))


@pytest.mark.parametrize("category", ["Women", "U21", "Youth", "Reserves", "B teams"])
def test_excluded_category_overrides_allowed_name(category):
    assert not is_allowed_football_competition(FootballCompetition(123, "Premier League", category, "EN"))


def test_country_metadata_prevents_ambiguous_league_matching():
    assert not is_allowed_football_competition(FootballCompetition(1, "Ligue 1", country_code="DZ"))
    assert is_allowed_football_competition(FootballCompetition(9999, "Ligue 1", country_name="France"))
    assert is_allowed_football_competition(FootballCompetition(7777, "Ekstraklasa"))
    assert not is_allowed_football_competition(FootballCompetition(3, "Unknown League", country_code="FR"))


@pytest.fixture
def backend(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 4, 12, tzinfo=app.WARSAW).astimezone(tz)

    state = SimpleNamespace(
        menu=[FootballCompetition(901, "Ligue 1", country_code="FR"),
              FootballCompetition(902, "FA Cup", country_code="EN"),
              FootballCompetition(903, "Premier League", country_code="EN"),
              FootballCompetition(904, "Ekstraklasa", country_code="PL"),
              FootballCompetition(905, "Premier League", country_code="NZ"),
              FootballCompetition(906, "Ligue 1 Women", country_code="FR"),
              FootballCompetition(907, "League Two", country_code="EN")],
        results={}, calls=[], menu_calls=0, forbidden=[], barrier=None,
    )
    state.results = {item.id: [Match(id=item.id, name="A - B", competition_id=item.id, competition=item.name,
                                    date="2026-09-04T18:00:00Z")] for item in state.menu}
    state.results[902][0].name = "Regional Club - Manchester City"

    def menu(client, sport_code="tennis"):
        assert sport_code == "football"
        state.menu_calls += 1
        if isinstance(state.menu, Exception):
            raise state.menu
        return state.menu

    def fetch(client, competition_id):
        state.calls.append(competition_id)
        if state.barrier:
            state.barrier.wait(timeout=2)
        result = state.results[competition_id]
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result

    def forbidden(*args, **kwargs):
        state.forbidden.append(args)
        raise AssertionError("Curated football must not call the global feed, search, or details")

    monkeypatch.setattr(app, "datetime", FrozenDatetime)
    monkeypatch.setattr(app, "TODAY_FOOTBALL_TIMEOUT", 4)
    monkeypatch.setattr(app, "TODAY_FOOTBALL_WORKERS", 2)
    monkeypatch.setattr(app, "_football_menu_cache", None)
    monkeypatch.setattr(app, "_cache", {})
    monkeypatch.setattr(app, "fetch_sport_menu", menu)
    monkeypatch.setattr(app, "fetch_competition_matches", fetch)
    monkeypatch.setattr(app, "_fetch_matches_page", forbidden)
    monkeypatch.setattr(app.BetclicClient, "get_matches", forbidden)
    monkeypatch.setattr(app.BetclicClient, "get_match", forbidden)
    monkeypatch.setattr(app.BetclicClient, "search", forbidden)
    return state


def test_default_football_fetches_dynamic_curated_ids_in_parallel_and_keeps_cup_clubs(backend):
    backend.barrier = Barrier(2)
    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": "football"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "competitions"
    assert data["selected_competition_ids"] == [901, 902, 903, 904]
    assert [item["id"] for item in data["selected_competitions"]] == [901, 902, 903, 904]
    assert sorted(backend.calls) == [901, 902, 903, 904]
    assert backend.forbidden == []
    assert data["returned"] == 4
    assert data["partial"] is False
    assert data["errors"] == []
    assert data["events"][1]["name"] == "Regional Club - Manchester City"
    assert all("markets" not in item for item in data["events"])
    assert data["generated_at_warsaw"].endswith("+02:00")


def test_curated_football_filters_dates_deduplicates_and_preserves_competition_filter(backend):
    base = backend.results[901][0]
    backend.results[901] = [base, base,
        Match(id=12, name="C - D", competition_id=901, competition="Ligue 1", date="2026-09-03T22:00:00Z"),
        Match(id=13, name="E - F", competition_id=901, competition="Ligue 1", date="2026-09-04T22:00:00Z"),
        Match(id=14, name="G - H", competition_id=901, competition="Ligue 1", date="invalid"),
    ]
    backend.results[902].append(base)
    data = app.today_events(sport="football", competition="ligue", chunk=9)
    assert [item["id"] for item in data["events"]] == [901, 12]
    assert data["returned"] == 2


def test_menu_cache_ttl_and_dynamic_ids(backend, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app, "SPORT_MENU_CACHE_TTL", 600)
    original = app._fetch_football_menu()
    backend.menu = [FootballCompetition(999, "Ligue 1", country_code="FR")]
    clock[0] += 599
    assert app._fetch_football_menu() == original
    clock[0] += 1
    assert app._fetch_football_menu()[0].id == 999
    assert backend.menu_calls == 2


def test_competition_error_returns_partial_http_result(backend):
    backend.results[901] = ReadTimeout("slow competition")
    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": "football"})
    assert response.status_code == 200
    data = response.json()
    assert data["returned"] == 3
    assert data["partial"] is True
    assert data["errors"] == [{"competition_id": 901, "detail": "slow competition"}]


def test_menu_error_does_not_trigger_automatic_global_fallback(backend):
    backend.menu = ReadTimeout("menu timeout")
    data = app.today_events(sport="football", competition=None)
    assert data["partial"] is True
    assert data["returned"] == 0
    assert data["errors"] == [{"stage": "sport_menu", "detail": "menu timeout"}]
    assert backend.calls == backend.forbidden == []


def test_curated_deadline_does_not_wait_for_slow_worker(backend, monkeypatch):
    monkeypatch.setattr(app, "TODAY_FOOTBALL_TIMEOUT", 0.05)
    release, started, finished = Event(), Event(), Event()

    def slow():
        started.set()
        release.wait(timeout=10)
        finished.set()
        return []

    backend.results[901] = slow
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(app.today_events, sport="football", competition=None)
        try:
            assert started.wait(timeout=1)
            data = future.result(timeout=3)
            assert not finished.is_set()
            assert data["returned"] == 3
            assert data["partial"] is True
            assert data["errors"][0]["competition_id"] == 901
        finally:
            release.set()
            assert finished.wait(timeout=1)


def test_scope_all_uses_only_legacy_feed(backend, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_fetch_matches_page", lambda sport, offset, **kw: calls.append(offset) or {
        "matches": [Match(id=offset, name="A - B", competition="New Zealand Premier League", date="2026-09-04T18:00:00Z")], "total": 0,
    })
    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": "football", "scope": "all", "chunk": 1})
    assert response.status_code == 200
    data = response.json()
    assert sorted(calls) == [160, 200, 240, 280]
    assert data["chunk"] == 1 and data["next_chunk"] == 2
    assert data["returned"] == 4
    assert backend.menu_calls == 0
    assert backend.calls == []


def test_invalid_scope_is_rejected_before_fetch(backend):
    with TestClient(app.app) as client:
        response = client.get("/today", params={"sport": "football", "scope": "unknown"})
    assert response.status_code == 422
    assert backend.menu_calls == 0


def test_menu_parser_preserves_country_and_merges_pinned_duplicates():
    competition = integer(1, 999) + string(2, "Ligue 1") + string(3, "football") + string(4, "FR")
    country = string(1, "FR") + string(2, "Francja") + string(3, competition)
    football = string(2, "football") + string(1, competition) + string(7, country)
    result = parse_sport_menu(string(2, football), sport_code="football")
    assert result == [FootballCompetition(999, "Ligue 1", country_code="FR", country_name="Francja")]


@pytest.mark.parametrize("name", ["CAF Confederation Cup", "CAF Confederations Cup", "Puchar Konfederacji",
    "Puchar Konfederacji CAF", "Puchar Konfederacji - Afryka", "Confederations Cup"])
def test_caf_and_ambiguous_confederation_cups_are_excluded(name):
    assert not is_allowed_football_competition(FootballCompetition(1, name, country_code="ZZ"))


@pytest.mark.parametrize("name", ["FIFA Confederations Cup", "Puchar Konfederacji FIFA", "FIFA Puchar Konfederacji"])
def test_explicit_historical_fifa_cup_is_distinct(name):
    assert is_allowed_football_competition(FootballCompetition(1, name, country_code="ZZ"))


@pytest.mark.parametrize("name", ["Slavia Praha B", "Jong Ajax Amsterdam", "Bayern München II", "Chelsea U23",
    "Chelsea U 21", "Arsenal Reserves", "Roma Primavera", "Legia rezerwy", "slavia praha b (FC)"])
def test_reserve_team_markers(name):
    assert is_reserve_team(name)


@pytest.mark.parametrize("name", ["Willem II", "Willem II FC", "B 1903", "B.93", "Bayern München",
    "B-SAD", "Barnsley", "IIves", "Ajax Amsterdam", "Primaveras FC"])
def test_reserve_team_false_positives(name):
    assert not is_reserve_team(name)


@pytest.mark.parametrize("name", ["Ossett United - Pontefract Collieries", "Quorn FC - Shepshed Dynamo FC"])
def test_obvious_low_cup_pairs_are_removed_but_main_round_is_retained(name):
    match = Match(name=name)
    assert not is_allowed_football_match(match, FootballCompetition(1, "Anglia FA Cup", country_code="EN"))
    assert is_allowed_football_match(match, FootballCompetition(1, "Anglia FA Cup", "Quarter-finals", "EN"))
    assert not is_allowed_football_match(match, FootballCompetition(1, "Anglia FA Cup", "First qualifying round", "EN"))


@pytest.mark.parametrize("name", ["Quorn FC - Manchester City", "Unknown FC - Another Unknown",
    "Ossett United - Arsenal", "Paris-Saint-Germain - Monaco"])
def test_unknown_or_large_club_cup_pairs_are_retained(name):
    assert is_allowed_football_match(Match(name=name), FootballCompetition(1, "FA Cup", country_code="EN"))


def test_event_filters_count_unique_today_matches_and_use_structured_teams(backend):
    base = dict(competition_id=902, competition="FA Cup", date="2026-09-04T18:00:00Z")
    rejected = [Match(id=10, name="Ossett United - Pontefract Collieries", **base),
        Match(id=11, name="Quorn FC - Shepshed Dynamo FC", **base),
        Match(id=12, name="Slavia Praha B - Sparta Praha", **base),
        Match(id=13, name="Other display label", teams=[Team("Opponent"), Team("Jong Ajax Amsterdam")], **base)]
    backend.results[902] += rejected + [rejected[0], Match(id=14, name="Willem II - B.93", **base)]
    backend.menu.append(FootballCompetition(908, "Puchar Konfederacji", country_code="ZZ"))
    with TestClient(app.app) as client:
        data = client.get("/today", params={"sport": "football"}).json()
    assert data["filtered_out"] == 4
    assert data["returned"] == 5
    assert data["partial"] is False and data["errors"] == []
    assert 908 not in backend.calls
    assert not {10, 11, 12, 13} & {event["id"] for event in data["events"]}


def test_allowed_league_evidence_overrides_low_cup_fallback_independent_of_order(backend):
    # A club may move tiers: use fixtures already fetched, including future dates.
    backend.results[902][0].name = "Quorn FC - Shepshed Dynamo FC"
    backend.results[903][0].name = "Quorn - Arsenal"
    backend.results[903][0].date = "2026-09-05T18:00:00Z"
    data = app.today_events(sport="football", competition=None)
    assert data["filtered_out"] == 0
    assert 902 in {event["id"] for event in data["events"]}
    assert backend.forbidden == []


@pytest.mark.parametrize("name", ["Flackwell Heath - Hanwell Town", "Aveley FC - Cheshunt FC",
    "Three Bridges - Kingstonian"])
def test_additional_non_league_cup_pairs(name):
    match = Match(name=name)
    cup = FootballCompetition(1, "FA Cup", country_code="EN")
    assert not _is_allowed_cup_match(match, cup)
    assert _is_allowed_cup_match(match, FootballCompetition(2, "League One", country_code="EN"))
    assert _is_allowed_cup_match(match, FootballCompetition(1, "FA Cup", "Third Round Proper", "EN"))


@pytest.mark.parametrize("stage", ["preliminary", "extra preliminary", "preliminary round",
    "First Qualifying Round", "Qualifying", "Fourth Qualification Round", "runda wstępna"])
@pytest.mark.parametrize("location", ["competition_name", "category", "round_name", "round"])
def test_explicit_early_rounds_are_rejected_for_unknown_clubs(stage, location):
    match = Match(name="Unknown FC - Other Unknown")
    cup = FootballCompetition(1, "FA Cup", country_code="EN")
    if location == "competition_name":
        cup = FootballCompetition(1, f"FA Cup - {stage}", country_code="EN")
    elif location == "category":
        cup = FootballCompetition(1, "FA Cup", stage, "EN")
    else:
        setattr(match, location, stage)
    assert is_allowed_football_competition(cup)
    assert not _is_allowed_cup_match(match, cup)


@pytest.mark.parametrize("name,cup,country", [
    ("Manchester United - Hanwell Town", "FA Cup", "EN"),
    ("Bayern München - Unknown FC", "DFB-Pokal", "DE"),
    ("Legia Warszawa - Unknown FC", "Puchar Polski", "PL"),
    ("Aveley Town - Cheshunt FC", "FA Cup", "EN"),  # exact aliases only
])
def test_large_or_uncertain_clubs_are_not_removed_by_cup_filter(name, cup, country):
    assert _is_allowed_cup_match(Match(name=name), FootballCompetition(1, cup, country_code=country))


def test_cup_round_filter_does_not_apply_to_leagues_or_uefa_qualifying():
    match = Match(name="Flackwell Heath - Hanwell Town")
    match.round_name = "Qualifying round"
    for cup in [FootballCompetition(1, "Premier League", country_code="EN"),
                FootballCompetition(2, "UEFA Champions League - Qualification", country_code="EU")]:
        assert is_allowed_football_competition(cup)
        assert _is_allowed_cup_match(match, cup)


def test_main_phase_in_competition_name_preserves_low_clubs():
    cup = FootballCompetition(1, "FA Cup - Third Round Proper", country_code="EN")
    assert is_allowed_football_competition(cup)
    assert _is_allowed_cup_match(Match(name="Aveley FC - Cheshunt FC"), cup)


def test_new_cup_exclusions_increment_filtered_out_once_per_today_event(backend):
    base = dict(competition_id=902, competition="FA Cup", date="2026-09-04T18:00:00Z")
    rejected = [Match(id=30, name="Flackwell Heath - Hanwell Town", **base),
        Match(id=31, name="Aveley FC - Cheshunt FC", **base),
        Match(id=32, name="Three Bridges - Kingstonian", **base)]
    qualifying = Match(id=33, name="Unknown FC - Other Unknown", **base)
    qualifying.competition = "FA Cup - Extra Preliminary Round"
    tomorrow = Match(id=34, name="Three Bridges - Kingstonian", **base)
    tomorrow.date = "2026-09-05T18:00:00Z"
    backend.results[902] += rejected + [rejected[0], qualifying, tomorrow,
        Match(id=35, name="Manchester United - Cheshunt FC", **base)]
    with TestClient(app.app) as client:
        data = client.get("/today", params={"sport": "football"}).json()
    assert data["filtered_out"] == 4
    assert data["returned"] == 5
    assert data["partial"] is False and data["errors"] == []
    assert 35 in {event["id"] for event in data["events"]}
    assert data["source"] == "competitions"
    assert backend.forbidden == []
