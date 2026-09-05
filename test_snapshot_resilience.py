from __future__ import annotations

from datetime import datetime, timedelta

import mycombi_refresh
import snapshot_resilience as resilience


def _event(identity: int, start: datetime, odds: float = 1.5):
    return {
        "id": identity,
        "name": f"Team {identity} A - Team {identity} B",
        "date_warsaw": start.isoformat(),
        "markets": [
            {
                "name": "Winner",
                "selections": [{"name": "A", "odds": odds}],
            }
        ],
    }


def _sport(events, stale_ids=()):
    return {
        "sport": "football",
        "source": "direct_betclic",
        "stale": False,
        "event_count": len(events),
        "stale_event_ids": list(stale_ids),
        "summary_only_event_ids": [],
        "discovery": {},
        "errors": [],
        "events": events,
    }


def test_missing_event_is_reused_when_recent_and_future():
    now = datetime.now(resilience.WARSAW)
    old = _event(123, now + timedelta(hours=2))
    current = _sport([{"id": None, "name": None}])
    previous = _sport([old])

    result = resilience.stabilize_sport_payload(
        current,
        previous,
        (now - timedelta(minutes=10)).isoformat(),
        now,
    )

    assert [item["id"] for item in result["events"]] == [123]
    assert result["stale_event_ids"] == [123]
    assert result["events"][0]["_snapshot_meta"]["reason"] == "missing_from_discovery"
    assert any(error["stage"] == "validation" for error in result["errors"])
    assert any(error["stage"] == "discovery_missing" for error in result["errors"])


def test_missing_event_expires_instead_of_being_kept_forever():
    now = datetime.now(resilience.WARSAW)
    old = _event(123, now + timedelta(hours=2))
    old["_snapshot_meta"] = {
        "stale": True,
        "reason": "missing_from_discovery",
        "stale_since_warsaw": (
            now - timedelta(seconds=resilience.FALLBACK_MAX_AGE_SECONDS + 5)
        ).isoformat(),
    }

    result = resilience.stabilize_sport_payload(
        _sport([]),
        _sport([old]),
        now.isoformat(),
        now,
    )
    assert result["events"] == []
    assert result["stale_event_ids"] == []


def test_repeated_detail_fallback_expires_old_market_prices():
    now = datetime.now(resilience.WARSAW)
    old = _event(555, now + timedelta(hours=2), odds=1.91)
    old["_snapshot_meta"] = {
        "stale": True,
        "reason": "detail_refresh_failed",
        "stale_since_warsaw": (
            now - timedelta(seconds=resilience.FALLBACK_MAX_AGE_SECONDS + 5)
        ).isoformat(),
    }
    current = _sport([old], stale_ids=[555])

    result = resilience.stabilize_sport_payload(current, _sport([old]), now.isoformat(), now)
    assert result["events"][0]["markets"] == []
    assert 555 in result["summary_only_event_ids"]
    assert result["events"][0]["_snapshot_meta"]["reason"] == "detail_fallback_expired"


def test_recent_last_good_quote_is_used_when_live_quote_fails():
    now = datetime.now(resilience.WARSAW)
    selections = [
        {"market_id": 10, "selection_id": 101},
        {"market_id": 20, "selection_id": 202},
    ]
    cache = {
        "date_warsaw": now.date().isoformat(),
        "results": [{
            "request_id": "old",
            "action": "quote",
            "event_id": 123,
            "selections": selections,
            "quoted_at_warsaw": (now - timedelta(minutes=45)).isoformat(),
            "valid": True,
            "odds": 1.87,
            "errors": [],
        }],
    }
    live = {
        "schema_version": 2,
        "date_warsaw": now.date().isoformat(),
        "results": [{
            "request_id": "new",
            "action": "quote",
            "event_id": 123,
            "selections": selections,
            "quoted_at_warsaw": now.isoformat(),
            "valid": False,
            "odds": None,
            "errors": [{"code": "UPSTREAM", "detail": "temporary"}],
        }],
    }

    output, cache_output, fallback_count = resilience.reconcile_quotes_with_last_good(
        live, cache, now.date().isoformat(), now
    )
    result = output["results"][0]
    assert fallback_count == 1
    assert result["request_id"] == "new"
    assert result["valid"] is True
    assert result["odds"] == 1.87
    assert result["stale"] is True
    assert result["fallback"] is True
    assert result["fallback_age_seconds"] >= 44 * 60
    assert cache_output["results"]


def test_old_quote_is_not_used_as_fallback():
    now = datetime.now(resilience.WARSAW)
    selections = [
        {"market_id": 10, "selection_id": 101},
        {"market_id": 20, "selection_id": 202},
    ]
    cache = {
        "date_warsaw": now.date().isoformat(),
        "results": [{
            "event_id": 123,
            "selections": selections,
            "quoted_at_warsaw": (
                now - timedelta(seconds=resilience.MYCOMBI_FALLBACK_MAX_AGE_SECONDS + 5)
            ).isoformat(),
            "valid": True,
            "odds": 1.87,
            "errors": [],
        }],
    }
    live = {
        "schema_version": 2,
        "date_warsaw": now.date().isoformat(),
        "results": [{
            "request_id": "new",
            "event_id": 123,
            "selections": selections,
            "quoted_at_warsaw": now.isoformat(),
            "valid": False,
            "odds": None,
            "errors": [{"code": "UPSTREAM"}],
        }],
    }

    output, cache_output, fallback_count = resilience.reconcile_quotes_with_last_good(
        live, cache, now.date().isoformat(), now
    )
    assert fallback_count == 0
    assert output["results"][0]["valid"] is False
    assert cache_output["results"] == []


def test_live_quote_failure_keeps_exact_combination_identity(monkeypatch):
    request = {
        "request_id": "quote-failure",
        "action": "quote",
        "event_id": 123,
        "label": "A + over 1.5",
        "selections": [
            {"market_id": 10, "selection_id": 101},
            {"market_id": 20, "selection_id": 202},
        ],
    }

    monkeypatch.setattr(mycombi_refresh.core, "raw_requests", lambda: [request])
    monkeypatch.setattr(mycombi_refresh.core, "request_action", lambda item: "quote")
    monkeypatch.setattr(mycombi_refresh.core, "normalize_quote_request", lambda item: dict(request))
    monkeypatch.setattr(mycombi_refresh.core, "_client", lambda timeout=None: object())
    monkeypatch.setattr(
        mycombi_refresh.core,
        "quote_mycombi",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary upstream failure")),
    )
    monkeypatch.setattr(
        mycombi_refresh.core,
        "retry",
        lambda label, fn, attempts: fn(),
    )
    monkeypatch.setattr(mycombi_refresh.core, "atomic_write_json", lambda *args, **kwargs: None)

    payload = mycombi_refresh.refresh_quotes_with_identity("2026-09-05", {123})
    result = payload["results"][0]
    assert result["valid"] is False
    assert result["event_id"] == 123
    assert result["selections"] == request["selections"]
    assert result["label"] == request["label"]
    assert result["errors"][0]["code"] == "REQUEST_OR_UPSTREAM_ERROR"
