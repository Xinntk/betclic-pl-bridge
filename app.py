from __future__ import annotations

import os
import time
from datetime import datetime
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from betclic_api import BetclicClient
from betclic_api.client import MARKET_CODES, SPORTS


APP_NAME = "Betclic PL Odds Bridge"
WARSAW = ZoneInfo("Europe/Warsaw")
CACHE_TTL = int(os.getenv("CACHE_TTL", "45"))
DEFAULT_LOCALE = os.getenv("BETCLIC_LOCALE", "pl")
DEFAULT_REGULATION = os.getenv("BETCLIC_REGULATION", "PL")
DEFAULT_SITE = os.getenv("BETCLIC_SITE", "https://www.betclic.pl")

app = FastAPI(
    title=APP_NAME,
    version="0.1.0",
    description=(
        "Read-only bridge for Betclic's public offering data. "
        "Unofficial and not affiliated with Betclic."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = Lock()


def _client() -> BetclicClient:
    """Create a client configured for the Polish Betclic offering."""
    c = BetclicClient(locale=DEFAULT_LOCALE, timeout=(5, 12))
    # The upstream project currently defaults to the French regulation/site.
    # Overriding these request headers switches the public offering to PL.
    c._session.headers.update(  # noqa: SLF001 - intentional compatibility shim
        {
            "x-bg-regulation": DEFAULT_REGULATION,
            "x-bg-ref-regulator-zone": DEFAULT_REGULATION,
            "x-bg-ref-brand": "BETCLIC",
            "x-bg-ref-platform": "DESKTOP",
            "Origin": DEFAULT_SITE,
            "Referer": f"{DEFAULT_SITE}/",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
        }
    )
    return c


def _cached(key: str):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    return None


def _store(key: str, value: Any):
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=WARSAW)
        return dt.astimezone(WARSAW)
    except Exception:
        return None


def _selection_to_dict(selection) -> dict[str, Any]:
    return {"name": selection.name, "odds": selection.odds}


def _market_to_dict(market) -> dict[str, Any]:
    return {
        "name": market.name,
        "suspended": market.suspended,
        "selections": [_selection_to_dict(s) for s in market.selections],
    }


def _match_to_dict(match, include_markets: bool = True) -> dict[str, Any]:
    dt = _parse_dt(match.date)
    out = {
        "id": match.id,
        "name": match.name,
        "date": match.date,
        "date_warsaw": dt.isoformat() if dt else None,
        "competition": match.competition,
        "competition_id": match.competition_id,
        "is_live": match.is_live,
        "teams": [
            {"name": team.name, "short": team.short}
            for team in (match.teams or [])
        ],
    }
    if include_markets:
        out["markets"] = [_market_to_dict(m) for m in (match.markets or [])]
    return out


def _fetch_matches_page(sport: str, offset: int) -> dict[str, Any]:
    key = f"matches:{sport}:{offset}"
    if (hit := _cached(key)) is not None:
        return hit
    try:
        result = _client().get_matches(sport, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic upstream error: {exc}") from exc
    return _store(key, result)


def _fetch_event(match_id: int, category: str | None = None):
    key = f"event:{match_id}:{category or 'default'}"
    if (hit := _cached(key)) is not None:
        return hit

    market_code = None
    if category:
        market_code = MARKET_CODES.get(category, category)

    try:
        match = _client().get_match(match_id, market_code=market_code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic upstream error: {exc}") from exc
    return _store(key, match)


def _dedupe_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for market in markets:
        signature = (
            market.get("name"),
            tuple((s.get("name"), s.get("odds")) for s in market.get("selections", [])),
        )
        if signature not in seen:
            seen.add(signature)
            out.append(market)
    return out


@app.middleware("http")
async def no_store(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def root():
    return {
        "service": APP_NAME,
        "status": "ok",
        "regulation": DEFAULT_REGULATION,
        "locale": DEFAULT_LOCALE,
        "timezone": "Europe/Warsaw",
        "routes": {
            "slate": "/slate?sport=football&limit=15&offset=0&today=true",
            "event": "/event/{match_id}",
            "event_all_football_markets": "/event/{match_id}/football-all",
            "search": "/search?q=Lech",
            "docs": "/docs",
        },
    }


@app.get("/health")
def health():
    return {"ok": True, "time_warsaw": datetime.now(WARSAW).isoformat()}


@app.get("/sports")
def sports():
    return {"sports": sorted(SPORTS.keys())}


@app.get("/slate")
def slate(
    sport: str = Query("football"),
    limit: int = Query(15, ge=1, le=40),
    offset: int = Query(0, ge=0),
    today: bool = Query(True),
    odds: bool = Query(True, description="Fetch the event's default market bundle too"),
    q: str | None = Query(None, description="Optional substring filter for event/competition"),
):
    """
    Return a page of upcoming/live events, optionally with the default Betclic markets.

    This is the endpoint intended for a quick daily scan. Keep `limit` modest to
    avoid unnecessary upstream calls. Results are cached briefly.
    """
    result = _fetch_matches_page(sport, offset)
    matches = list(result.get("matches", []))

    if today:
        target = datetime.now(WARSAW).date()
        matches = [m for m in matches if (_parse_dt(m.date) and _parse_dt(m.date).date() == target)]

    if q:
        needle = q.casefold()
        matches = [
            m for m in matches
            if needle in (m.name or "").casefold()
            or needle in (m.competition or "").casefold()
        ]

    matches = matches[:limit]
    output = []

    for idx, match in enumerate(matches):
        if odds and match.id:
            detail = _fetch_event(int(match.id))
            output.append(_match_to_dict(detail, include_markets=True))
            # Be gentle with the upstream service when a full slate is requested.
            if idx + 1 < len(matches):
                time.sleep(0.08)
        else:
            output.append(_match_to_dict(match, include_markets=False))

    return {
        "sport": sport,
        "today_only": today,
        "offset": offset,
        "returned": len(output),
        "upstream_total": result.get("total", 0),
        "generated_at_warsaw": datetime.now(WARSAW).isoformat(),
        "events": output,
    }


@app.get("/event/{match_id}")
def event(match_id: int, category: str | None = Query(None)):
    """
    Return detailed markets for one event.

    For football, `category` may be one of:
    result, goals, score_handicap, top, goalscorers, props.
    Without a category, Betclic's default market bundle is returned.
    """
    match = _fetch_event(match_id, category=category)
    return _match_to_dict(match, include_markets=True)


@app.get("/event/{match_id}/football-all")
def event_football_all(match_id: int):
    """Merge the default bundle with all football market categories known by the upstream client."""
    base = _fetch_event(match_id)
    base_dict = _match_to_dict(base, include_markets=True)
    markets = list(base_dict.get("markets", []))

    for category in MARKET_CODES:
        match = _fetch_event(match_id, category=category)
        markets.extend(_market_to_dict(m) for m in (match.markets or []))
        time.sleep(0.05)

    base_dict["markets"] = _dedupe_markets(markets)
    base_dict["market_count"] = len(base_dict["markets"])
    return base_dict


@app.get("/search")
def search(
    q: str = Query(..., min_length=2),
    detail: bool = Query(False),
    limit: int = Query(10, ge=1, le=20),
):
    try:
        matches = _client().search(q)[:limit]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic upstream error: {exc}") from exc

    output = []
    for match in matches:
        if detail and match.id:
            output.append(_match_to_dict(_fetch_event(int(match.id)), include_markets=True))
        else:
            output.append(_match_to_dict(match, include_markets=False))

    return {"query": q, "returned": len(output), "events": output}
