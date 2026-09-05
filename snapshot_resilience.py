from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

FALLBACK_MAX_AGE_SECONDS = max(300, int(os.getenv("SNAPSHOT_FALLBACK_MAX_AGE_SECONDS", "5400")))
MYCOMBI_FALLBACK_MAX_AGE_SECONDS = max(
    300, int(os.getenv("MYCOMBI_FALLBACK_MAX_AGE_SECONDS", str(FALLBACK_MAX_AGE_SECONDS)))
)
EVENT_GRACE_AFTER_START_SECONDS = max(
    0, int(os.getenv("SNAPSHOT_EVENT_GRACE_AFTER_START_SECONDS", "1800"))
)
MYCOMBI_LAST_GOOD_QUOTES_PATH = Path(
    os.getenv("MYCOMBI_LAST_GOOD_QUOTES_PATH", "snapshots/mycombi_last_good_quotes.json")
)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(WARSAW) if dt.tzinfo else dt.replace(tzinfo=WARSAW)
    except (TypeError, ValueError):
        return None


def age_seconds(value: str | None, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def valid_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    try:
        event_id = int(event.get("id"))
    except (TypeError, ValueError):
        return False
    return event_id > 0 and bool(str(event.get("name") or "").strip())


def event_id(event: dict[str, Any]) -> int:
    return int(event["id"])


def event_datetime(event: dict[str, Any]) -> datetime | None:
    return parse_iso(event.get("date_warsaw") or event.get("date"))


def event_can_be_retained(event: dict[str, Any], now: datetime) -> bool:
    starts = event_datetime(event)
    if starts is None:
        return True
    return starts >= now - timedelta(seconds=EVENT_GRACE_AFTER_START_SECONDS)


def stale_since(event: dict[str, Any], fallback: datetime) -> datetime:
    meta = event.get("_snapshot_meta")
    if isinstance(meta, dict):
        parsed = parse_iso(meta.get("stale_since_warsaw"))
        if parsed is not None:
            return parsed
    return fallback


def mark_stale_event(
    event: dict[str, Any],
    *,
    since: datetime,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    cloned = copy.deepcopy(event)
    cloned["_snapshot_meta"] = {
        "stale": True,
        "reason": reason,
        "stale_since_warsaw": since.isoformat(),
        "fallback_age_seconds": round(max(0.0, (now - since).total_seconds()), 1),
        "max_fallback_age_seconds": FALLBACK_MAX_AGE_SECONDS,
    }
    return cloned


def stabilize_sport_payload(
    current: dict[str, Any],
    previous_sport: dict[str, Any] | None,
    previous_generated_at: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Remove malformed rows and retain recently-known events missing from one discovery pass.

    The fallback age is anchored to the first stale pass via per-event metadata, so a bad
    event can never remain fresh forever just because the snapshot itself is regenerated.
    """
    result = copy.deepcopy(current)
    errors = list(result.get("errors", []))
    previous_sport = previous_sport if isinstance(previous_sport, dict) else {}
    previous_events = {
        event_id(item): item
        for item in previous_sport.get("events", [])
        if valid_event(item)
    }
    previous_generated = parse_iso(previous_generated_at) or now

    events: list[dict[str, Any]] = []
    invalid_count = 0
    for item in result.get("events", []):
        if valid_event(item):
            events.append(copy.deepcopy(item))
        else:
            invalid_count += 1
    if invalid_count:
        errors.append({
            "stage": "validation",
            "detail": f"dropped {invalid_count} malformed event row(s)",
            "malformed_event_count": invalid_count,
        })

    stale_ids = {int(value) for value in result.get("stale_event_ids", []) if value is not None}
    summary_only_ids = {
        int(value) for value in result.get("summary_only_event_ids", []) if value is not None
    }

    # Existing stale-detail fallbacks must also expire. Carry the original stale timestamp
    # forward rather than resetting it on every refresh.
    stabilized: list[dict[str, Any]] = []
    for item in events:
        identity = event_id(item)
        if identity not in stale_ids:
            item.pop("_snapshot_meta", None)
            stabilized.append(item)
            continue

        previous_item = previous_events.get(identity, item)
        since = stale_since(previous_item, previous_generated)
        fallback_age = max(0.0, (now - since).total_seconds())
        if fallback_age <= FALLBACK_MAX_AGE_SECONDS:
            stabilized.append(
                mark_stale_event(
                    item,
                    since=since,
                    reason="detail_refresh_failed",
                    now=now,
                )
            )
        else:
            stripped = copy.deepcopy(item)
            stripped["markets"] = []
            stripped = mark_stale_event(
                stripped,
                since=since,
                reason="detail_fallback_expired",
                now=now,
            )
            stabilized.append(stripped)
            summary_only_ids.add(identity)
            errors.append({
                "stage": "fallback_expired",
                "event_id": identity,
                "detail": "previous market prices exceeded maximum fallback age",
                "fallback_age_seconds": round(fallback_age, 1),
            })

    events = stabilized
    current_ids = {event_id(item) for item in events}

    # If discovery temporarily omits an event that was present in the previous snapshot,
    # retain the previous event for a bounded period. This is the failure mode that caused
    # Manchester City - Coventry to disappear from one otherwise healthy refresh.
    for identity, old_event in previous_events.items():
        if identity in current_ids or not event_can_be_retained(old_event, now):
            continue
        since = stale_since(old_event, previous_generated)
        fallback_age = max(0.0, (now - since).total_seconds())
        if fallback_age > FALLBACK_MAX_AGE_SECONDS:
            continue
        events.append(
            mark_stale_event(
                old_event,
                since=since,
                reason="missing_from_discovery",
                now=now,
            )
        )
        stale_ids.add(identity)
        errors.append({
            "stage": "discovery_missing",
            "event_id": identity,
            "detail": "event missing from current discovery; reused recent same-day snapshot",
            "reused_previous": True,
            "fallback_age_seconds": round(fallback_age, 1),
        })

    events.sort(key=lambda item: item.get("date_warsaw") or item.get("date") or "")
    result["events"] = events
    result["event_count"] = len(events)
    result["stale_event_ids"] = sorted(stale_ids)
    result["summary_only_event_ids"] = sorted(summary_only_ids)
    result["errors"] = errors
    result["fallback_max_age_seconds"] = FALLBACK_MAX_AGE_SECONDS
    return result


def requested_event_ids(raw_requests: list[Any]) -> set[int]:
    ids: set[int] = set()
    for item in raw_requests:
        if not isinstance(item, dict):
            continue
        try:
            identity = int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        if identity > 0:
            ids.add(identity)
    return ids


def quote_key(item: dict[str, Any]) -> str | None:
    try:
        identity = int(item.get("event_id"))
    except (TypeError, ValueError):
        return None
    selections = item.get("selections")
    if not isinstance(selections, list) or not selections:
        return None
    normalized: list[tuple[int, int]] = []
    try:
        for selection in selections:
            normalized.append((int(selection["market_id"]), int(selection["selection_id"])))
    except (KeyError, TypeError, ValueError):
        return None
    normalized.sort()
    return f"{identity}:" + ",".join(f"{market}:{selection}" for market, selection in normalized)


def _quote_is_valid(item: dict[str, Any]) -> bool:
    odds = item.get("odds")
    return item.get("valid") is True and isinstance(odds, (int, float)) and odds > 1


def reconcile_quotes_with_last_good(
    live_payload: dict[str, Any],
    cache_payload: dict[str, Any],
    today: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Use a recent last-good quote only when a new live quote fails.

    Successful live quotes always win. Fallback quotes preserve their original quote time,
    expose their age, and expire after MYCOMBI_FALLBACK_MAX_AGE_SECONDS.
    """
    cache_results = cache_payload.get("results", []) if cache_payload.get("date_warsaw") == today else []
    cache: dict[str, dict[str, Any]] = {}
    for item in cache_results:
        if not isinstance(item, dict) or not _quote_is_valid(item):
            continue
        key = quote_key(item)
        age = age_seconds(item.get("quoted_at_warsaw"), now)
        if key and age is not None and age <= MYCOMBI_FALLBACK_MAX_AGE_SECONDS:
            cache[key] = copy.deepcopy(item)

    output_results: list[dict[str, Any]] = []
    fallback_count = 0
    for raw in live_payload.get("results", []):
        item = copy.deepcopy(raw) if isinstance(raw, dict) else {}
        key = quote_key(item)
        if key and _quote_is_valid(item):
            item["stale"] = False
            item["fallback"] = False
            cache[key] = copy.deepcopy(item)
            output_results.append(item)
            continue

        cached = cache.get(key) if key else None
        if cached is None:
            item["stale"] = False
            item["fallback"] = False
            output_results.append(item)
            continue

        age = age_seconds(cached.get("quoted_at_warsaw"), now)
        if age is None or age > MYCOMBI_FALLBACK_MAX_AGE_SECONDS:
            item["stale"] = False
            item["fallback"] = False
            item["fallback_expired"] = True
            output_results.append(item)
            continue

        fallback = copy.deepcopy(cached)
        for field in ("request_id", "label", "event_id", "selections"):
            if item.get(field) is not None:
                fallback[field] = copy.deepcopy(item[field])
        fallback["stale"] = True
        fallback["fallback"] = True
        fallback["fallback_age_seconds"] = round(age, 1)
        fallback["max_fallback_age_seconds"] = MYCOMBI_FALLBACK_MAX_AGE_SECONDS
        fallback["live_attempt_at_warsaw"] = now.isoformat()
        fallback["live_errors"] = copy.deepcopy(item.get("errors", []))
        output_results.append(fallback)
        fallback_count += 1

    output = copy.deepcopy(live_payload)
    output["schema_version"] = max(3, int(output.get("schema_version") or 0))
    output["results"] = output_results
    output["fallback_count"] = fallback_count
    output["fallback_max_age_seconds"] = MYCOMBI_FALLBACK_MAX_AGE_SECONDS

    pruned_cache = []
    for item in cache.values():
        age = age_seconds(item.get("quoted_at_warsaw"), now)
        if age is not None and age <= MYCOMBI_FALLBACK_MAX_AGE_SECONDS:
            pruned_cache.append(item)
    cache_output = {
        "schema_version": 1,
        "source": "direct_betclic_mycombi_last_good",
        "date_warsaw": today,
        "generated_at_warsaw": now.isoformat(),
        "max_fallback_age_seconds": MYCOMBI_FALLBACK_MAX_AGE_SECONDS,
        "results": pruned_cache,
    }
    return output, cache_output, fallback_count


def expire_old_metadata_fallbacks(
    payload: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], int]:
    output = copy.deepcopy(payload)
    expired = 0
    results = []
    for raw in output.get("results", []):
        item = copy.deepcopy(raw) if isinstance(raw, dict) else {}
        if item.get("stale") is True and item.get("cached") is True:
            age = age_seconds(item.get("fetched_at_warsaw"), now)
            if age is None or age > MYCOMBI_FALLBACK_MAX_AGE_SECONDS:
                item["available"] = False
                item["markets"] = []
                item["odds"] = None
                item["expired_fallback"] = True
                item.setdefault("errors", []).append({
                    "code": "FALLBACK_EXPIRED",
                    "detail": "cached MyCombi metadata exceeded maximum fallback age",
                })
                expired += 1
        results.append(item)
    output["results"] = results
    output["expired_fallback_count"] = expired
    output["fallback_max_age_seconds"] = MYCOMBI_FALLBACK_MAX_AGE_SECONDS
    return output, expired
