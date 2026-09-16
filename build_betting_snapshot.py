from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

INPUT = Path("snapshots/latest.json")
OUTPUT = Path("snapshots/betting.json")

# Keep the published helper small enough for direct consumption while retaining
# the markets most useful for building accumulators. Tennis keeps all markets;
# football keeps common pre-match 1X2, double-chance, goals and BTTS markets.
FOOTBALL_MARKET_HINTS = (
    "wynik meczu",
    "podwójna szansa",
    "podwojna szansa",
    "gole",
    "suma goli",
    "obie drużyny",
    "obie druzyny",
    "strzelą",
    "strzela",
    "handicap",
)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def compact_market(market: dict) -> dict:
    out = {"name": market.get("name"), "id": market.get("id")}
    if market.get("suspended") is not None:
        out["suspended"] = bool(market.get("suspended"))
    selections = []
    for selection in market.get("selections", []):
        if not isinstance(selection, dict) or selection.get("odds") is None:
            continue
        item = {
            "id": selection.get("id"),
            "name": selection.get("name"),
            "odds": selection.get("odds"),
        }
        for key in ("line", "handicap", "value"):
            if selection.get(key) is not None:
                item[key] = selection[key]
        selections.append(item)
    if selections:
        out["selections"] = selections
    return out


def compact_event(event: dict, sport: str, now: datetime) -> dict | None:
    if event.get("id") is None or not event.get("name"):
        return None
    dt = parse_dt(event.get("date_warsaw") or event.get("date"))
    # Pre-match helper only: omit events already live or already started.
    if event.get("is_live"):
        return None
    if dt is not None and dt < now:
        return None

    markets = []
    for market in event.get("markets", []):
        if not isinstance(market, dict) or market.get("suspended"):
            continue
        if sport == "football":
            name = str(market.get("name") or "").casefold()
            if not any(hint in name for hint in FOOTBALL_MARKET_HINTS):
                continue
        compact = compact_market(market)
        if compact.get("selections"):
            markets.append(compact)

    if not markets:
        return None

    return {
        "id": event["id"],
        "name": event["name"],
        "date_warsaw": event.get("date_warsaw") or event.get("date"),
        "sport": sport,
        "competition": event.get("competition"),
        "teams": event.get("teams", []),
        "markets": markets,
    }


def main() -> None:
    latest = json.loads(INPUT.read_text(encoding="utf-8"))
    if latest.get("source") != "direct_betclic":
        raise SystemExit("latest snapshot is not direct_betclic")
    generated = parse_dt(latest.get("generated_at_warsaw"))
    if generated is None:
        raise SystemExit("latest snapshot has invalid generated_at_warsaw")
    today = latest.get("date_warsaw")
    now = datetime.now(generated.tzinfo)
    events = []
    for sport in ("football", "tennis"):
        for event in latest.get("sports", {}).get(sport, {}).get("events", []):
            compact = compact_event(event, sport, now)
            if compact is not None:
                events.append(compact)
    events.sort(key=lambda e: e.get("date_warsaw") or "")

    payload = {
        "schema_version": 1,
        "source": "direct_betclic",
        "date_warsaw": today,
        "generated_at_warsaw": latest.get("generated_at_warsaw"),
        "event_count": len(events),
        "events": events,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
