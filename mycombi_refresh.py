from __future__ import annotations

import json
import time
from pathlib import Path

import snapshot_refresh as core
from snapshot_refresh import (
    MYCOMBI_PATH,
    MYCOMBI_QUOTES_PATH,
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


def refresh_quotes_with_identity(today: str, football_event_ids: set[int]) -> dict:
    """Quote MyCombi while preserving combination identity on upstream failure.

    The original helper intentionally returned a minimal error object. That is fine for
    reporting but insufficient for last-good fallback because a failed result must still
    carry event_id + selections so it can be matched to the exact cached combination.
    """
    quote_requests = [
        item for item in core.raw_requests()
        if core.request_action(item) == "quote"
    ][:core.MAX_QUOTE_REQUESTS]
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
            # Preserve normalized identity whenever normalization succeeded. If it did not,
            # retain safe raw identity fields so diagnostics remain useful, but malformed
            # requests still cannot accidentally match a valid cached quote.
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

        # The explicit on-demand queue is trusted as an instruction to query Betclic directly.
        # Snapshot membership is advisory, not a hard gate: one degraded discovery pass must not
        # prevent a live MyCombi query for an event we already selected moments earlier.
        requests = raw_requests()
        queued_event_ids = requested_event_ids(requests)
        event_ids = snapshot_event_ids | queued_event_ids
        requested_missing_from_snapshot = queued_event_ids - snapshot_event_ids

        metadata = refresh_mycombi(today, event_ids)
        metadata, expired_metadata_count = expire_old_metadata_fallbacks(metadata, now_warsaw())
        atomic_write_json(MYCOMBI_PATH, metadata)

        previous_last_good = load_json(MYCOMBI_LAST_GOOD_QUOTES_PATH, {})
        live_quotes = refresh_quotes_with_identity(today, event_ids)
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
