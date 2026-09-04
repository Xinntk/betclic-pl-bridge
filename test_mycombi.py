import struct

from fastapi.testclient import TestClient
import pytest
from requests.exceptions import ReadTimeout

import app
import mycombi
from betclic_api.proto import decode_protobuf as decode, encode_field_string as msg, encode_field_varint as var, grpc_frame
from test_tennis_competitions import FakeResponse

EVENT = 1210189877370880
PAIRS = [{"market_id": "100", "selection_id": "101"}, {"market_id": "200", "selection_id": "201"}]


def double(field, value):
    return bytes([(field << 3) | 1]) + struct.pack("<d", value)


def selection(identity, *, status=1, compatible=True):
    return (var(1, identity) + msg(10, f"Choice {identity}") + double(12, 1.4)
            + var(14, status) + var(7, int(compatible)) + var(15, 99999))


def market(identity, children=None):
    return var(1, identity) + msg(2, f"Market {identity}") + var(7, 1) + (children if children is not None else msg(16, selection(identity + 1)))


def payload(odds=None, *, event_id=EVENT, eligible=True, markets=None):
    match = var(1, event_id) + msg(2, "PSG - Monaco") + msg(3, "2026-09-04T19:05:00Z") + var(14, int(eligible))
    roots = [market(100), market(200)] if markets is None else markets
    match += msg(11, b"".join(msg(3, m) for m in roots))
    return msg(1, msg(1, match) + (double(2, odds) if odds is not None else b""))


@pytest.fixture
def offering(monkeypatch):
    state = {"requests": [], "responses": [], "quote": payload(1.68), "metadata": payload(), "failure": None}

    def post(session, url, **kwargs):
        assert url == "https://offering.begmedia.com/web/offering.access.api/offering.access.api.MyCombiService/GetMyCombiWithNotifications"
        assert not session.cookies and "Authorization" not in session.headers
        assert session.headers["x-bg-regulation"] == "PL"
        assert kwargs["stream"] is True and kwargs["timeout"] == (1, app.MYCOMBI_UPSTREAM_TIMEOUT)
        fields = decode(kwargs["data"][5:])
        assert fields[1] == [EVENT] and fields[2] == [b"pl"]
        state["requests"].append(fields)
        if state["failure"]:
            raise state["failure"]
        raw = state["quote"] if 3 in fields else state["metadata"]
        frame = grpc_frame(msg(1, raw))
        response = FakeResponse([frame[:3], frame[3:8], frame[8:]])
        state["responses"].append(response)
        return response

    monkeypatch.setattr("requests.Session.post", post)
    for method in ("get_match", "get_matches", "search"):
        monkeypatch.setattr(app.BetclicClient, method, lambda *a, **kw: pytest.fail("Wrong upstream path"))
    return state


def test_http_metadata_and_quote_use_only_anonymous_offering_rpc(offering):
    with TestClient(app.app) as client:
        data = client.get(f"/event/{EVENT}/mycombi").json()
        response = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS},
                               headers={"Authorization": "must-not-forward", "Cookie": "account=must-not-forward"})
    assert data["source"] == "mycombi" and data["available"] is True
    assert data["event_id"] == str(EVENT)
    assert data["markets"][0]["selections"][0]["market_id"] == "100"  # not 99999, the betslip ID
    assert response.status_code == 200
    assert response.json()["valid"] is True and response.json()["odds"] == 1.68
    assert response.json()["errors"] == [] and response.json()["selections"] == PAIRS
    assert response.headers["cache-control"] == "no-store"
    assert len(offering["requests"]) == 3
    assert [decode(pair) for pair in offering["requests"][-1][3]] == [{1: [100], 2: [101]}, {1: [200], 2: [201]}]
    assert all(response.closed for response in offering["responses"])


def test_parse_all_frontend_market_layouts_and_deduplicate():
    matrix = msg(10, msg(1, msg(1, selection(101))) + msg(1, msg(2, b"")))
    split = msg(11, msg(2, selection(201)))
    slider = msg(15, msg(3, msg(2, msg(1, selection(301)))))
    group = msg(13, market(400))
    tab = msg(14, msg(2, market(500)))
    roots = [market(100, matrix), market(200, split), market(300, slider), market(0, group + tab), market(100, matrix)]
    data = mycombi.parse_payload(payload(markets=roots), EVENT)
    assert [m["market_id"] for m in data["markets"]] == ["100", "200", "300", "400", "500"]
    assert [s["selection_id"] for m in data["markets"] for s in m["selections"]] == ["101", "201", "301", "401", "501"]


@pytest.mark.parametrize("code,name", mycombi.ERROR_CODES.items())
def test_upstream_combination_errors_are_not_reported_as_valid(offering, code, name):
    offering["quote"] = msg(2, var(1, code) + msg(2, "Niedozwolona kombinacja") + msg(3, "Nie można połączyć"))
    with TestClient(app.app) as client:
        response = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS})
    data = response.json()
    assert response.status_code == 200 and data["valid"] is False and data["odds"] is None
    assert data["errors"][0]["code"] == name


@pytest.mark.parametrize("odds", [None, 1, float("nan"), float("inf"), -2])
def test_missing_or_invalid_odds_never_become_a_valid_quote(offering, odds):
    offering["quote"] = payload(odds)
    with TestClient(app.app) as client:
        response = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS})
    assert response.status_code in (200, 502)
    assert response.json()["valid"] is False and response.json()["odds"] is None
    assert response.json()["errors"]


def test_other_event_response_fails_closed(offering):
    offering["quote"] = payload(1.68, event_id=EVENT + 1)
    with TestClient(app.app) as client:
        response = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS})
    assert response.status_code == 502 and response.json()["valid"] is False


@pytest.mark.parametrize("pair", [{"market_id": "999", "selection_id": "101"}, {"market_id": "100", "selection_id": "999"}])
def test_unknown_selection_or_market_cannot_be_quoted_for_this_event(offering, pair):
    with TestClient(app.app) as client:
        data = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": [pair, PAIRS[1]]}).json()
    assert data["valid"] is False and data["errors"][0]["code"] == "SELECTION_UNAVAILABLE"
    assert len(offering["requests"]) == 1


@pytest.mark.parametrize("status,compatible", [(2, True), (3, True), (1, False)])
def test_ineligible_or_suspended_selection_is_not_sent_to_calculator(offering, status, compatible):
    offering["metadata"] = payload(markets=[market(100, msg(16, selection(101, status=status, compatible=compatible))), market(200)])
    with TestClient(app.app) as client:
        data = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS}).json()
    assert data["valid"] is False and len(offering["requests"]) == 1


def test_unavailable_event_returns_metadata_without_inventing_markets(offering):
    offering["metadata"] = payload(eligible=False, markets=[])
    with TestClient(app.app) as client:
        data = client.get(f"/event/{EVENT}/mycombi").json()
        quote = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS}).json()
    assert data["available"] is False and data["markets"] == []
    assert quote["valid"] is False and quote["errors"][0]["code"] == "UNAVAILABLE"


@pytest.mark.parametrize("failure,status", [(ReadTimeout("slow"), 504), (RuntimeError("grpc-status: 13"), 502)])
def test_upstream_failures_return_structured_errors(offering, failure, status):
    offering["failure"] = failure
    with TestClient(app.app) as client:
        assert client.get(f"/event/{EVENT}/mycombi").status_code == status
        response = client.post(f"/event/{EVENT}/mycombi/quote", json={"selections": PAIRS})
    assert response.status_code == status
    assert response.json()["valid"] is False and response.json()["errors"]


@pytest.mark.parametrize("body", [
    {"selections": []}, {"selections": PAIRS[:1]}, {"selections": [PAIRS[0], PAIRS[0]]},
    {"selections": [{"market_id": 1, "selection_id": n} for n in range(1, 22)]},
    {"selections": PAIRS, "stake": 10}, {"selections": PAIRS, "account_id": "x"},
    {"selections": [{**PAIRS[0], "stake": 10}, PAIRS[1]]},
    {"selections": [{"market_id": True, "selection_id": "101"}, PAIRS[1]]},
    {"selections": [{"market_id": 1.5, "selection_id": "101"}, PAIRS[1]]},
    {"selections": [{"market_id": "-1", "selection_id": "101"}, PAIRS[1]]},
    {"selections": [{"market_id": str(2**63), "selection_id": "101"}, PAIRS[1]]},
])
def test_invalid_payloads_and_write_related_fields_never_reach_upstream(offering, body):
    with TestClient(app.app) as client:
        assert client.post(f"/event/{EVENT}/mycombi/quote", json=body).status_code == 422
    assert offering["requests"] == []


def test_browser_can_query_quote_but_no_other_write_method_is_exposed():
    with TestClient(app.app) as client:
        response = client.options(f"/event/{EVENT}/mycombi/quote", headers={
            "Origin": "https://example.org", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert response.status_code == 200
    assert [route.path for route in app.app.routes if "POST" in getattr(route, "methods", ())] == ["/event/{event_id}/mycombi/quote"]
