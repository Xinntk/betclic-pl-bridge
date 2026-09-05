from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from snapshot_refresh import (
    SNAPSHOT_PATH,
    STATUS_PATH,
    SnapshotUnavailable,
    atomic_write_json,
    load_json,
    now_warsaw,
    previous_today,
    refresh_sport,
    validate_snapshot,
)
from snapshot_resilience import FALLBACK_MAX_AGE_SECONDS, stabilize_sport_payload


def _empty_failed_sport(sport: str, detail: str) -> dict:
    return {
        "sport": sport,
        "source": "unavailable",
        "stale": True,
        "event_count": 0,
        "stale_event_ids": [],
        "summary_only_event_ids": [],
        "discovery": {},
        "errors": [{"stage": "sport", "detail": detail}],
        "events": [],
    }


def build_odds_snapshot():
    started = time.monotonic()
    now = now_warsaw()
    today = now.date().isoformat()
    previous = previous_today(load_json(SNAPSHOT_PATH, {}), today)
    previous_generated_at = previous.get("generated_at_warsaw") if previous else None

    sports = {}
    failures = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(refresh_sport, sport, previous, today): sport
            for sport in ("football", "tennis")
        }
        for future in as_completed(futures):
            sport = futures[future]
            old_sport = previous.get("sports", {}).get(sport) if previous else None
            try:
                current = future.result()
                sports[sport] = stabilize_sport_payload(
                    current,
                    old_sport,
                    previous_generated_at,
                    now_warsaw(),
                )
            except Exception as exc:
                failures[sport] = str(exc)
                failed = _empty_failed_sport(sport, str(exc))
                stabilized = stabilize_sport_payload(
                    failed,
                    old_sport,
                    previous_generated_at,
                    now_warsaw(),
                )
                if stabilized.get("events"):
                    stabilized["source"] = "previous_same_day_bounded"
                    stabilized["stale"] = True
                sports[sport] = stabilized

    if all(payload.get("stale") and not payload.get("events") for payload in sports.values()):
        raise SnapshotUnavailable("all sports unavailable and no recent same-day fallback exists")

    snapshot = {
        "schema_version": 3,
        "source": "direct_betclic",
        "generated_at_warsaw": now_warsaw().isoformat(),
        "date_warsaw": today,
        "fallback_max_age_seconds": FALLBACK_MAX_AGE_SECONDS,
        "sports": {
            "football": sports["football"],
            "tennis": sports["tennis"],
        },
    }
    validate_snapshot(snapshot, today)
    atomic_write_json(SNAPSHOT_PATH, snapshot)

    error_count = sum(len(payload.get("errors", [])) for payload in sports.values())
    stale_count = sum(len(payload.get("stale_event_ids", [])) for payload in sports.values())
    summary_only_count = sum(len(payload.get("summary_only_event_ids", [])) for payload in sports.values())
    status = {
        "schema_version": 4,
        "ok": True,
        "degraded": bool(failures or error_count or stale_count or summary_only_count),
        "source": "direct_betclic",
        "last_attempt_at_warsaw": now_warsaw().isoformat(),
        "date_warsaw": today,
        "duration_seconds": round(time.monotonic() - started, 3),
        "fallback_max_age_seconds": FALLBACK_MAX_AGE_SECONDS,
        "event_counts": {
            sport: payload.get("event_count", len(payload.get("events", [])))
            for sport, payload in sports.items()
        },
        "stale_event_count": stale_count,
        "summary_only_event_count": summary_only_count,
        "error_count": error_count,
        "sport_failures": failures,
    }
    atomic_write_json(STATUS_PATH, status)
    return snapshot, status


def main() -> int:
    started = time.monotonic()
    try:
        snapshot, status = build_odds_snapshot()
        print(json.dumps({
            "ok": True,
            "date": snapshot["date_warsaw"],
            "football": snapshot["sports"]["football"]["event_count"],
            "tennis": snapshot["sports"]["tennis"]["event_count"],
            "degraded": status["degraded"],
            "stale_events": status["stale_event_count"],
            "seconds": round(time.monotonic() - started, 2),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        status = {
            "schema_version": 4,
            "ok": False,
            "degraded": True,
            "source": "direct_betclic",
            "last_attempt_at_warsaw": now_warsaw().isoformat(),
            "date_warsaw": now_warsaw().date().isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "fallback_max_age_seconds": FALLBACK_MAX_AGE_SECONDS,
            "error": str(exc),
        }
        atomic_write_json(STATUS_PATH, status)
        print(json.dumps(status, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
