from __future__ import annotations

import json
import time
from pathlib import Path

from snapshot_refresh import (
    MYCOMBI_PATH,
    MYCOMBI_QUOTES_PATH,
    SNAPSHOT_PATH,
    atomic_write_json,
    load_json,
    now_warsaw,
    refresh_mycombi,
    refresh_quotes,
)

MYCOMBI_STATUS_PATH = Path("snapshots/mycombi_status.json")


def main() -> int:
    started = time.monotonic()
    now = now_warsaw()
    today = now.date().isoformat()
    try:
        snapshot = load_json(SNAPSHOT_PATH, {})
        if snapshot.get("date_warsaw") != today:
            raise RuntimeError("daily odds snapshot is missing or not from today")
        football = snapshot.get("sports", {}).get("football", {})
        event_ids = {
            int(event["id"])
            for event in football.get("events", [])
            if isinstance(event, dict) and event.get("id") is not None
        }
        if not event_ids:
            raise RuntimeError("today's football snapshot contains no event ids")

        metadata = refresh_mycombi(today, event_ids)
        quotes = refresh_quotes(today, event_ids)
        status = {
            "schema_version": 1,
            "ok": True,
            "degraded": bool(metadata.get("errors")),
            "source": "direct_betclic_mycombi_on_demand",
            "last_attempt_at_warsaw": now_warsaw().isoformat(),
            "date_warsaw": today,
            "duration_seconds": round(time.monotonic() - started, 3),
            "inspect_request_count": metadata.get("request_count", 0),
            "quote_request_count": quotes.get("request_count", 0),
            "metadata_error_count": len(metadata.get("errors", [])),
        }
        atomic_write_json(MYCOMBI_STATUS_PATH, status)
        print(json.dumps(status, ensure_ascii=False))
        return 0
    except Exception as exc:
        status = {
            "schema_version": 1,
            "ok": False,
            "degraded": True,
            "source": "direct_betclic_mycombi_on_demand",
            "last_attempt_at_warsaw": now_warsaw().isoformat(),
            "date_warsaw": today,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": str(exc),
        }
        atomic_write_json(MYCOMBI_STATUS_PATH, status)
        print(json.dumps(status, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
