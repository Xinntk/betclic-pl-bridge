from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

import snapshot_refresh as core
from snapshot_refresh import (
    MYCOMBI_PATH,
    MYCOMBI_QUOTES_PATH,
    MYCOMBI_REQUESTS_PATH,
    SNAPSHOT_PATH,
    atomic_write_json,
    load_json,
    now_warsaw,
    raw_requests,
    refresh_mycombi,
)
from snapshot_resilience import (
    MYCOMBI_FALLBACK_MAX_AGE_SECONDS,
    MYCOMBI_LAST_GOOD_QUOTES_PATH,
    expire_old_metadata_fallbacks,
    reconcile_quotes_with_last_good,
    requested_event_ids,
)

MYCOMBI_STATUS_PATH = Path("snapshots/mycombi_status.json")


def _auto_quote_requests(metadata: dict, max_requests: int) -> list[dict]:
    """Build a small set of live Betclic MyCombi candidates when no queue is supplied."""
    candidates = []
    for event in metadata.get("results", []):
        if not isinstance(event, dict) or not event.get("available"):
            continue
        event_id = event.get("event_id")
        if event_id is None:
            continue
        markets = []
        for market in event.get("markets", []):
            if not isinstance(market, dict) or not market.get("is_betbuilder_eligible"):
                continue
            online = [
                s for s in market.get("selections", [])
                if isinstance(s, dict)
                and s.get("status") == "ONLINE"
                and s.get("is_betbuilder_compatible") is True
                and s.get("market_id") is not None
                and s.get("selection_id") is not None
                and isinstance(s.get("odds"), (int, float))
                and s.get("odds") > 1
            ]
            if online:
                online.sort(key=lambda s: (abs(float(s["odds"]) - 1.7), str(s.get("name", ""))))
                markets.append(online[:3])
        for left, right in itertools.combinations(markets, 2):
            for a, b in itertools.product(left, right):
                candidates.append({
                    "request_id": f"auto-{event_id}-{a['market_id']}-{a['selection_id']}-{b['market_id']}-{b['selection_id']}",
                    "action": "quote",
                    "event_id": int(event_id),
                    "label": f"Auto MyCombi: {a.get('name','')} + {b.get('name','')}",
                    "selections": [
                        {"market_id": int(a["market_id"]), "selection_id": int(a["selection_id"])},
                        {"market_id": int(b["market_id"]), "selection_id": int(b["selection_id"])},
                    ],
                    "_hint": float(a["odds"]) * float(b["odds"]),
                })

    candidates.sort(key=lambda x: (abs(x.pop("_hint") - 2.5), x["event_id"], x["request_id"]))
    selected = []
    per_event = {}
    for candidate in candidates:
        event_id = candidate["event_id"]
        if per_event.get(event_id, 0) >= 2:
            continue
        selected.append(candidate)
        per_event[event_id] = per_event.get(event_id, 0) + 1
        if len(selected) >= max_requests:
            break
    return selected


def _refresh_metadata(today: str, event_ids: set[int]) -> dict:
    """Run the existing inspector, but synthesize a temporary inspect queue when empty."""
    current = raw_requests()
    if any(core.request_action(item) == "inspect" for item in current):
        return refresh_mycombi(today, event_ids)

    # Preserve the user's queue exactly; the generated requests exist only for this run.
    original_exists = MYCOMBI_REQUESTS_PATH.exists()
    original_text = MYCOMBI_REQUESTS_PATH.read_text(encoding="utf-8") if original_exists else None
    auto_requests = [
        {
            "request_id": f"auto-inspect-{event_id}",
            "action": "inspect",
            "event_id": int(event_id),
            "label": "Automatic MyCombi discovery",
        }
        for event_id in sorted(event_ids)[:core.MAX_INSPECT_REQUESTS]
    ]
    try:
        atomic_write_json(MYCOMBI_REQUESTS_PATH, {"requests": auto_requests})
        return refresh_mycombi(today, event_ids)
    finally:
        if original_exists and original_text is not None:
            MYCOMBI_REQUESTS_PATH.write_text(original_text, encoding="utf-8")
        elif MYCOMBI_REQUESTS_PATH.exists():
            MYCOMBI_REQUESTS_PATH.unlink()


def refresh_quotes_with_identity(today: str, football_event_ids: set[int], metadata: dict | None = None) -> dict:
    """Quote MyCombi while preserving combination identity on upstream failure."""
    explicit_quotes = [
        item for item in core.raw_requests()
        if core.request_action(item) == "quote"
    ][:core.MAX_QUOTE_REQUESTS]
    quote_requests = explicit_quotes or _auto_quote_requests(metadata or {}, core.MAX_QUOTE_REQUESTS)
    results = []
    seen_request_ids = set()

    for raw_request in quote_requests:
        request = None
        try:
            request = core.normalize_quote_request(raw_request)
            if request["request_id"] in seen_request_ids:
                raise ValueError("duplicate request_id")
            seen_request_ids.add(request["request_id"])
            if request["event_id"] not in football_event_ids:
                raise ValueError("event_id is not available for direct MyCombi query")
            quote = core.retry(
                f"MyCombi quote {request['request_id']}",
                lambda req=request: core.quote_mycombi(
                    core._client(timeout=(2, 8)), req["event_id"], req["selections"]
                ),
                core.MYCOMBI_ATTEMPTS,
            )
            results.append({
                **request,
                "quoted_at_warsaw": core.now_warsaw().isoformat(),
                **quote,
            })
        except Exception as exc:
            if request is not None:
                identity = dict(request)
            elif isinstance(raw_request, dict):
                identity = {
                    "request_id": str(raw_request.get("request_id") or ""),
                    "action": "quote",
                }
                if raw_request.get("event_id") is not None:
                    identity["event_id"] = raw_request.get("event_id")
                if isinstance(raw_request.get("selections"), list):
                    identity["selections"] = raw_request.get("selections")
                if raw_request.get("label") is not None:
                    identity["label"] = raw_request.get("label")
            else:
                identity = {"request_id": "", "action": "quote"}

            results.append({
                **identity,
                "quoted_at_warsaw": core.now_warsaw().isoformat(),
                "valid": False,
                "odds": None,
                "errors": [{
                    "code": "REQUEST_OR_UPSTREAM_ERROR",
                    "detail": str(exc),
                }],
            })

    payload = {
        "schema_version": 3,
        "source": "direct_betclic_mycombi_quote",
        "date_warsaw": today,
        "generated_at_warsaw": core.now_warsaw().isoformat(),
        "request_count": len(quote_requests),
        "results": results,
    }
    core.atomic_write_json(core.MYCOMBI_QUOTES_PATH, payload)
    return payload


def main() -> int:
    started = time.monotonic()
    now = now_warsaw()
    today = now.date().isoformat()
    try:
        snapshot = load_json(SNAPSHOT_PATH, {})
        if snapshot.get("date_warsaw") != today:
            raise RuntimeError("daily odds snapshot is missing or not from today")

        football = snapshot.get("sports", {}).get("football", {})
        snapshot_event_ids = {
            int(event["id"])
            for event in football.get("events", [])
            if isinstance(event, dict) and event.get("id") is not None
        }
        if not snapshot_event_ids:
            raise RuntimeError("today's football snapshot contains no event ids")

        requests = raw_requests()
        queued_event_ids = requested_event_ids(requests)
        event_ids = snapshot_event_ids | queued_event_ids
        requested_missing_from_snapshot = queued_event_ids - snapshot_event_ids

        metadata = _refresh_metadata(today, event_ids)
        metadata, expired_metadata_count = expire_old_metadata_fallbacks(metadata, now_warsaw())
        atomic_write_json(MYCOMBI_PATH, metadata)

        previous_last_good = load_json(MYCOMBI_LAST_GOOD_QUOTES_PATH, {})
        live_quotes = refresh_quotes_with_identity(today, event_ids, metadata)
        quotes, last_good, fallback_quote_count = reconcile_quotes_with_last_good(
            live_quotes,
            previous_last_good,
            today,
            now_warsaw(),
        )
        atomic_write_json(MYCOMBI_QUOTES_PATH, quotes)
        atomic_write_json(MYCOMBI_LAST_GOOD_QUOTES_PATH, last_good)

        invalid_quote_count = sum(
            1 for item in quotes.get("results", [])
            if isinstance(item, dict) and item.get("valid") is not True
        )
        status = {
            "schema_version": 2,
            "ok": True,
            "degraded": bool(
                metadata.get("errors")
                or expired_metadata_count
                or fallback_quote_count
                or invalid_quote_count
                or requested_missing_from_snapshot
            ),
            "source": "direct_betclic_mycombi_on_demand",
            "last_attempt_at_warsaw": now_warsaw().isoformat(),
            "date_warsaw": today,
            "duration_seconds": round(time.monotonic() - started, 3),
            "fallback_max_age_seconds": MYCOMBI_FALLBACK_MAX_AGE_SECONDS,
            "inspect_request_count": metadata.get("request_count", 0),
            "quote_request_count": quotes.get("request_count", 0),
            "metadata_error_count": len(metadata.get("errors", [])),
            "expired_metadata_fallback_count": expired_metadata_count,
            "fallback_quote_count": fallback_quote_count,
            "invalid_quote_count": invalid_quote_count,
            "requested_event_missing_from_snapshot_count": len(requested_missing_from_snapshot),
            "requested_event_missing_from_snapshot_ids": sorted(requested_missing_from_snapshot),
        }
        atomic_write_json(MYCOMBI_STATUS_PATH, status)
        print(json.dumps(status, ensure_ascii=False))
        return 0
    except Exception as exc:
        status = {
            "schema_version": 2,
            "ok": False,
            "degraded": True,
            "source": "direct_betclic_mycombi_on_demand",
            "last_attempt_at_warsaw": now_warsaw().isoformat(),
            "date_warsaw": today,
            "duration_seconds": round(time.monotonic() - started, 3),
            "fallback_max_age_seconds": MYCOMBI_FALLBACK_MAX_AGE_SECONDS,
            "error": str(exc),
        }
        atomic_write_json(MYCOMBI_STATUS_PATH, status)
        print(json.dumps(status, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
