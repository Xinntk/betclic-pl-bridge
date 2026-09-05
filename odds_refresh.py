from __future__ import annotations

import copy
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


def build_odds_snapshot():
    started = time.monotonic()
    now = now_warsaw()
    today = now.date().isoformat()
    previous = previous_today(load_json(SNAPSHOT_PATH, {}), today)

    sports = {}
    failures = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(refresh_sport, sport, previous, today): sport
            for sport in ("football", "tennis")
        }
        for future in as_completed(futures):
            sport = futures[future]
            try:
                sports[sport] = future.result()
            except Exception as exc:
                failures[sport] = str(exc)
                old = previous.get("sports", {}).get(sport) if previous else None
                if isinstance(old, dict) and old.get("events"):
                    reused = copy.deepcopy(old)
                    reused["stale"] = True
                    reused["source"] = "previous_same_day"
                    reused.setdefault("errors", []).append({
                        "stage": "sport",
                        "detail": str(exc),
                    })
                    sports[sport] = reused
                else:
                    sports[sport] = {
                        "sport": sport,
                        "source": "unavailable",
                        "stale": True,
                        "event_count": 0,
                        "stale_event_ids": [],
                        "summary_only_event_ids": [],
                        "discovery": {},
                        "errors": [{"stage": "sport", "detail": str(exc)}],
                        "events": [],
                    }

    if all(payload.get("stale") and not payload.get("events") for payload in sports.values()):
        raise SnapshotUnavailable("all sports unavailable and no same-day fallback exists")

    snapshot = {
        "schema_version": 3,
        "source": "direct_betclic",
        "generated_at_warsaw": now_warsaw().isoformat(),
        "date_warsaw": today,
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
        "schema_version": 3,
        "ok": True,
        "degraded": bool(failures or error_count or stale_count or summary_only_count),
        "source": "direct_betclic",
        "last_attempt_at_warsaw": now_warsaw().isoformat(),
        "date_warsaw": today,
        "duration_seconds": round(time.monotonic() - started, 3),
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
            "seconds": round(time.monotonic() - started, 2),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        status = {
            "schema_version": 3,
            "ok": False,
            "degraded": True,
            "source": "direct_betclic",
            "last_attempt_at_warsaw": now_warsaw().isoformat(),
            "date_warsaw": now_warsaw().date().isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": str(exc),
        }
        atomic_write_json(STATUS_PATH, status)
        print(json.dumps(status, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
