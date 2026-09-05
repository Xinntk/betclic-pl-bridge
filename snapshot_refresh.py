from __future__ import annotations

import copy
import json
import os
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app import WARSAW, _client, _fetch_today, _match_to_dict
from mycombi import fetch_mycombi, quote_mycombi

SNAPSHOT_PATH = Path(os.getenv("SNAPSHOT_PATH", "snapshots/latest.json"))
STATUS_PATH = Path(os.getenv("SNAPSHOT_STATUS_PATH", "snapshots/status.json"))
MYCOMBI_PATH = Path(os.getenv("MYCOMBI_SNAPSHOT_PATH", "snapshots/mycombi.json"))
MYCOMBI_REQUESTS_PATH = Path(os.getenv("MYCOMBI_REQUESTS_PATH", "snapshots/mycombi_requests.json"))
MYCOMBI_QUOTES_PATH = Path(os.getenv("MYCOMBI_QUOTES_PATH", "snapshots/mycombi_quotes.json"))

EVENT_WORKERS = max(1, int(os.getenv("SNAPSHOT_EVENT_WORKERS", "10")))
MYCOMBI_WORKERS = max(1, int(os.getenv("SNAPSHOT_MYCOMBI_WORKERS", "6")))
EVENT_ATTEMPTS = max(1, int(os.getenv("SNAPSHOT_EVENT_ATTEMPTS", "2")))
DISCOVERY_ATTEMPTS = max(1, int(os.getenv("SNAPSHOT_DISCOVERY_ATTEMPTS", "2")))
MYCOMBI_ATTEMPTS = max(1, int(os.getenv("SNAPSHOT_MYCOMBI_ATTEMPTS", "2")))
MYCOMBI_REFRESH_SECONDS = max(300, int(os.getenv("MYCOMBI_REFRESH_SECONDS", "900")))
MAX_QUOTE_REQUESTS = max(1, int(os.getenv("MAX_MYCOMBI_QUOTE_REQUESTS", "40")))


class SnapshotUnavailable(RuntimeError):
    pass


def now_warsaw() -> datetime:
    return datetime.now(WARSAW)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return copy.deepcopy(default)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def retry(label: str, fn: Callable[[], Any], attempts: int) -> Any:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # upstream libraries raise several exception types
            last = exc
            if attempt < attempts:
                time.sleep(min(3.0, 0.6 * (2 ** (attempt - 1))) + random.random() * 0.25)
    assert last is not None
    raise RuntimeError(f"{label} failed after {attempts} attempt(s): {last}") from last


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(WARSAW) if dt.tzinfo else dt.replace(tzinfo=WARSAW)
    except (ValueError, TypeError):
        return None


def previous_today(previous: dict[str, Any], today: str) -> dict[str, Any]:
    return previous if previous.get("date_warsaw") == today else {}


def event_index(sport_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(sport_payload, dict):
        return {}
    return {
        str(event["id"]): event
        for event in sport_payload.get("events", [])
        if isinstance(event, dict) and event.get("id") is not None
    }


def direct_event_detail(event_id: int) -> dict[str, Any]:
    def call() -> dict[str, Any]:
        match = _client(timeout=(2, 8)).get_match(event_id)
        return _match_to_dict(match, include_markets=True, include_suspended=False, compact=True)

    return retry(f"event {event_id}", call, EVENT_ATTEMPTS)


def discover_sport(sport: str) -> tuple[list[Any], dict[str, Any]]:
    scope = "curated" if sport == "football" else "curated"
    return retry(
        f"{sport} discovery",
        lambda: _fetch_today(sport, 0, scope=scope),
        DISCOVERY_ATTEMPTS,
    )


def refresh_sport(sport: str, previous: dict[str, Any], today: str) -> dict[str, Any]:
    old_sport = previous.get("sports", {}).get(sport) if previous else None
    old_events = event_index(old_sport)

    try:
        summaries, scan = discover_sport(sport)
    except Exception as exc:
        if isinstance(old_sport, dict) and old_sport.get("events"):
            reused = copy.deepcopy(old_sport)
            reused["stale"] = True
            reused["source"] = "previous_same_day"
            reused.setdefault("errors", []).append({"stage": "discovery", "detail": str(exc)})
            return reused
        raise SnapshotUnavailable(f"{sport}: discovery unavailable and no same-day fallback: {exc}") from exc

    scan_errors = list(scan.get("errors", []))
    if not summaries and scan.get("partial") and isinstance(old_sport, dict) and old_sport.get("events"):
        reused = copy.deepcopy(old_sport)
        reused["stale"] = True
        reused["source"] = "previous_same_day"
        reused.setdefault("errors", []).extend({"stage": "discovery", **err} for err in scan_errors)
        return reused

    summary_rows = [_match_to_dict(item, include_markets=False) for item in summaries]
    details: list[dict[str, Any] | None] = [None] * len(summary_rows)
    errors: list[dict[str, Any]] = [{"stage": "discovery", **err} for err in scan_errors]
    stale_ids: list[int] = []
    summary_only_ids: list[int] = []

    def one(index: int, summary: dict[str, Any]):
        event_id = summary.get("id")
        if event_id is None:
            return index, summary, None
        try:
            return index, direct_event_detail(int(event_id)), None
        except Exception as exc:
            cached = old_events.get(str(event_id))
            if cached is not None:
                return index, copy.deepcopy(cached), {
                    "event_id": event_id,
                    "detail": str(exc),
                    "reused_previous": True,
                }
            return index, summary, {
                "event_id": event_id,
                "detail": str(exc),
                "reused_previous": False,
            }

    workers = min(EVENT_WORKERS, max(1, len(summary_rows)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, i, row) for i, row in enumerate(summary_rows)]
        for future in as_completed(futures):
            index, event, error = future.result()
            details[index] = event
            if error:
                errors.append({"stage": "detail", **error})
                if error["reused_previous"]:
                    stale_ids.append(int(error["event_id"]))
                else:
                    summary_only_ids.append(int(error["event_id"]))

    events = [event for event in details if event is not None]
    events.sort(key=lambda event: event.get("date_warsaw") or event.get("date") or "")
    return {
        "sport": sport,
        "source": "direct_betclic",
        "stale": False,
        "event_count": len(events),
        "stale_event_ids": sorted(stale_ids),
        "summary_only_event_ids": sorted(summary_only_ids),
        "discovery": scan,
        "errors": errors,
        "events": events,
    }


def should_refresh_mycombi(item: dict[str, Any] | None, now: datetime) -> bool:
    if not item:
        return True
    fetched = parse_iso(item.get("fetched_at_warsaw"))
    return fetched is None or (now - fetched).total_seconds() >= MYCOMBI_REFRESH_SECONDS


def direct_mycombi(event_id: int) -> dict[str, Any]:
    return retry(
        f"MyCombi {event_id}",
        lambda: fetch_mycombi(_client(timeout=(2, 8)), event_id),
        MYCOMBI_ATTEMPTS,
    )


def refresh_mycombi(football_events: list[dict[str, Any]], today: str) -> dict[str, Any]:
    now = now_warsaw()
    previous_all = previous_today(load_json(MYCOMBI_PATH, {}), today)
    old_by_id = {
        str(item.get("event_id")): item
        for item in previous_all.get("events", [])
        if isinstance(item, dict) and item.get("event_id") is not None
    }
    event_ids = [int(event["id"]) for event in football_events if event.get("id") is not None]
    output: dict[int, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    def one(event_id: int):
        old = old_by_id.get(str(event_id))
        if not should_refresh_mycombi(old, now):
            item = copy.deepcopy(old)
            item["cached"] = True
            item["stale"] = False
            return event_id, item, None
        try:
            data = direct_mycombi(event_id)
            return event_id, {
                "event_id": str(event_id),
                "fetched_at_warsaw": now_warsaw().isoformat(),
                "cached": False,
                "stale": False,
                **data,
            }, None
        except Exception as exc:
            if old:
                item = copy.deepcopy(old)
                item["cached"] = True
                item["stale"] = True
                return event_id, item, {"event_id": event_id, "detail": str(exc), "reused_previous": True}
            return event_id, {
                "event_id": str(event_id),
                "fetched_at_warsaw": now_warsaw().isoformat(),
                "cached": False,
                "stale": True,
                "available": False,
                "markets": [],
                "odds": None,
                "errors": [{"code": "UPSTREAM_ERROR", "detail": str(exc)}],
            }, {"event_id": event_id, "detail": str(exc), "reused_previous": False}

    workers = min(MYCOMBI_WORKERS, max(1, len(event_ids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, event_id) for event_id in event_ids]
        for future in as_completed(futures):
            event_id, item, error = future.result()
            output[event_id] = item
            if error:
                errors.append(error)

    items = [output[event_id] for event_id in event_ids if event_id in output]
    available_count = sum(bool(item.get("available")) for item in items)
    payload = {
        "schema_version": 1,
        "source": "direct_betclic_mycombi",
        "date_warsaw": today,
        "generated_at_warsaw": now_warsaw().isoformat(),
        "refresh_interval_seconds": MYCOMBI_REFRESH_SECONDS,
        "event_count": len(items),
        "available_count": available_count,
        "errors": errors,
        "events": items,
    }
    atomic_write_json(MYCOMBI_PATH, payload)
    return payload


def normalize_quote_request(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("request must be an object")
    request_id = str(item.get("request_id") or "").strip()
    if not request_id or len(request_id) > 120:
        raise ValueError("request_id is required and must be <= 120 characters")
    event_id = int(item.get("event_id"))
    if event_id <= 0:
        raise ValueError("event_id must be positive")
    selections = item.get("selections")
    if not isinstance(selections, list) or not 2 <= len(selections) <= 20:
        raise ValueError("selections must contain 2..20 items")
    normalized = []
    seen = set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise ValueError("each selection must be an object")
        market_id = int(selection.get("market_id"))
        selection_id = int(selection.get("selection_id"))
        if market_id <= 0 or selection_id <= 0:
            raise ValueError("market_id and selection_id must be positive")
        if selection_id in seen:
            raise ValueError("selection_id values must be distinct")
        seen.add(selection_id)
        normalized.append({"market_id": market_id, "selection_id": selection_id})
    return {
        "request_id": request_id,
        "event_id": event_id,
        "label": str(item.get("label") or "")[:240],
        "selections": normalized,
    }


def refresh_quotes(today: str) -> dict[str, Any]:
    raw = load_json(MYCOMBI_REQUESTS_PATH, {"requests": []})
    requests = raw.get("requests", []) if isinstance(raw, dict) else []
    requests = requests[:MAX_QUOTE_REQUESTS]
    results = []
    seen_request_ids = set()

    for raw_request in requests:
        try:
            request = normalize_quote_request(raw_request)
            if request["request_id"] in seen_request_ids:
                raise ValueError("duplicate request_id")
            seen_request_ids.add(request["request_id"])
            quote = retry(
                f"MyCombi quote {request['request_id']}",
                lambda req=request: quote_mycombi(
                    _client(timeout=(2, 8)), req["event_id"], req["selections"]
                ),
                MYCOMBI_ATTEMPTS,
            )
            results.append({**request, "quoted_at_warsaw": now_warsaw().isoformat(), **quote})
        except Exception as exc:
            results.append({
                "request_id": str(raw_request.get("request_id") if isinstance(raw_request, dict) else ""),
                "quoted_at_warsaw": now_warsaw().isoformat(),
                "valid": False,
                "odds": None,
                "errors": [{"code": "REQUEST_OR_UPSTREAM_ERROR", "detail": str(exc)}],
            })

    payload = {
        "schema_version": 1,
        "source": "direct_betclic_mycombi_quote",
        "date_warsaw": today,
        "generated_at_warsaw": now_warsaw().isoformat(),
        "request_count": len(requests),
        "results": results,
    }
    atomic_write_json(MYCOMBI_QUOTES_PATH, payload)
    return payload


def validate_snapshot(snapshot: dict[str, Any], today: str) -> None:
    if snapshot.get("schema_version") != 3:
        raise ValueError("unexpected snapshot schema")
    if snapshot.get("date_warsaw") != today:
        raise ValueError("snapshot date mismatch")
    sports = snapshot.get("sports")
    if not isinstance(sports, dict) or set(sports) != {"football", "tennis"}:
        raise ValueError("football and tennis sections are required")
    for sport, payload in sports.items():
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ValueError(f"invalid {sport} payload")
        ids = [str(event.get("id")) for event in payload["events"] if event.get("id") is not None]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate {sport} event ids")
        for event in payload["events"]:
            for market in event.get("markets", []):
                for selection in market.get("selections", []):
                    odds = selection.get("odds")
                    if odds is not None and (not isinstance(odds, (int, float)) or odds <= 0):
                        raise ValueError(f"invalid odds in event {event.get('id')}")


def build_snapshot() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    now = now_warsaw()
    today = now.date().isoformat()
    previous = previous_today(load_json(SNAPSHOT_PATH, {}), today)

    sports: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(refresh_sport, sport, previous, today): sport for sport in ("football", "tennis")}
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
                    reused.setdefault("errors", []).append({"stage": "sport", "detail": str(exc)})
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
        "sports": {"football": sports["football"], "tennis": sports["tennis"]},
    }
    validate_snapshot(snapshot, today)
    atomic_write_json(SNAPSHOT_PATH, snapshot)

    mycombi = refresh_mycombi(sports["football"].get("events", []), today)
    quotes = refresh_quotes(today)

    error_count = sum(len(payload.get("errors", [])) for payload in sports.values()) + len(mycombi.get("errors", []))
    stale_count = sum(len(payload.get("stale_event_ids", [])) for payload in sports.values())
    summary_only_count = sum(len(payload.get("summary_only_event_ids", [])) for payload in sports.values())
    status = {
        "schema_version": 1,
        "ok": True,
        "degraded": bool(failures or error_count or stale_count or summary_only_count),
        "source": "direct_betclic",
        "last_attempt_at_warsaw": now_warsaw().isoformat(),
        "date_warsaw": today,
        "duration_seconds": round(time.monotonic() - started, 3),
        "event_counts": {sport: payload.get("event_count", len(payload.get("events", []))) for sport, payload in sports.items()},
        "stale_event_count": stale_count,
        "summary_only_event_count": summary_only_count,
        "mycombi_event_count": mycombi.get("event_count", 0),
        "mycombi_available_count": mycombi.get("available_count", 0),
        "quote_request_count": quotes.get("request_count", 0),
        "error_count": error_count,
        "sport_failures": failures,
    }
    atomic_write_json(STATUS_PATH, status)
    return snapshot, mycombi, quotes


def main() -> int:
    started = time.monotonic()
    try:
        snapshot, mycombi, quotes = build_snapshot()
        print(json.dumps({
            "ok": True,
            "date": snapshot["date_warsaw"],
            "football": snapshot["sports"]["football"]["event_count"],
            "tennis": snapshot["sports"]["tennis"]["event_count"],
            "mycombi": mycombi["available_count"],
            "quotes": quotes["request_count"],
            "seconds": round(time.monotonic() - started, 2),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        status = {
            "schema_version": 1,
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
