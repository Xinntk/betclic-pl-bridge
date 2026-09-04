from __future__ import annotations

import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime
from threading import Lock
from types import SimpleNamespace
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from betclic_api import BetclicClient
from betclic_api.client import MARKET_CODES, SPORTS
from tennis_competitions import TennisCompetition, fetch_sport_menu, fetch_competition_matches


APP_NAME = "Betclic PL Odds Bridge"
WARSAW = ZoneInfo("Europe/Warsaw")
CACHE_TTL = int(os.getenv("CACHE_TTL", "45"))
TODAY_MAX_PAGES = int(os.getenv("TODAY_MAX_PAGES", "12"))
TODAY_PAGES_PER_CHUNK = int(os.getenv("TODAY_PAGES_PER_CHUNK", "4"))
TODAY_TENNIS_TIMEOUT = float(os.getenv("TODAY_TENNIS_TIMEOUT", "4"))
TODAY_TENNIS_WORKERS = int(os.getenv("TODAY_TENNIS_WORKERS", "4"))
SPORT_MENU_CACHE_TTL = max(300, int(os.getenv("SPORT_MENU_CACHE_TTL", "600")))
TODAY_WORKERS = int(os.getenv("TODAY_WORKERS", "4"))
TODAY_UPSTREAM_TIMEOUT = float(os.getenv("TODAY_UPSTREAM_TIMEOUT", "5"))
TODAY_PAGE_SIZE = 40
SLATE_ODDS_LIMIT = int(os.getenv("SLATE_ODDS_LIMIT", "8"))
SLATE_ODDS_WORKERS = int(os.getenv("SLATE_ODDS_WORKERS", "4"))
DEFAULT_LOCALE = os.getenv("BETCLIC_LOCALE", "pl")
DEFAULT_REGULATION = os.getenv("BETCLIC_REGULATION", "PL")
DEFAULT_SITE = os.getenv("BETCLIC_SITE", "https://www.betclic.pl")

app = FastAPI(
    title=APP_NAME,
    version="2.0.0",
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
_tennis_menu_cache: tuple[float, list[TennisCompetition]] | None = None
_tennis_menu_lock = Lock()


def _client(timeout: tuple[float, float] = (5, 12)) -> BetclicClient:
    """Create a client configured for the Polish Betclic offering."""
    c = BetclicClient(locale=DEFAULT_LOCALE, timeout=timeout)
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


def _match_to_dict(
    match,
    include_markets: bool = True,
    *,
    include_suspended: bool = False,
    compact: bool = False,
) -> dict[str, Any]:
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
        markets = [m for m in (match.markets or []) if include_suspended or not m.suspended]
        if compact:
            markets = [m for m in markets if _is_compact_market(m)]
        out["markets"] = [_market_to_dict(m) for m in markets]
    return out


# Compact mode deliberately uses both a deny-list and a size guard. The names
# returned by Betclic are localized and can change, whereas very large player
# proposition markets are consistently identifiable by their selection count.
_HEAVY_MARKET_TERMS = (
    "strzelec", "strzelcy", "gole zawodnik", "asyst", "dokładny wynik",
    "dokladny wynik", "correct score", "goalscorer", "player to score",
    "player shots", "zawodnik strza", "kartki zawodnik", "player card",
)
_IMPORTANT_MARKET_TERMS = (
    "wynik meczu", "zwycięzca", "zwyciezca", "match winner", "moneyline",
    "podwójna szansa", "podwojna szansa", "double chance", "liczba goli",
    "suma goli", "total", "powyżej", "poniżej", "powyzej", "ponizej",
    "obie drużyny", "obie druzyny", "both teams", "handicap", "set",
)


def _is_compact_market(market) -> bool:
    name = (market.name or "").casefold()
    if market.suspended or len(market.selections or []) > 12:
        return False
    if any(term in name for term in _HEAVY_MARKET_TERMS):
        return False
    return any(term in name for term in _IMPORTANT_MARKET_TERMS)


_TENNIS_EXCLUDED = re.compile(
    r"\b(?:double\w*|debl\w*|debel|podwojn\w*|mixed\w*|mixte\w*|mikst\w*|"
    r"itf|[mw]\s*-?\s*(?:15|25|35|50|75|100)|junior\w*|juniorsk\w*|"
    r"boys|girls|chlop\w*|dziewcz\w*|u\s*-?\s*(?:12|14|16|18)|under\s*-?\s*18|"
    r"utr|college\w*|collegiate|ncaa|university|exhibition\w*|pokazow\w*|"
    r"amateur\w*|amator\w*|futures|legends|wheelchair|next\s+gen)\b|"
    r"\bwta\s*(?:125|challenger)\b"
)
_TENNIS_ALLOWED = re.compile(
    r"\b(?:australian\s+open|roland\s+garros|french\s+open|wimbledon|us\s+open|"
    r"(?:atp|wta)(?:\s+(?:masters|tour))*\s*(?:1000|500|250|finals)|"
    r"(?:atp\s+)?challenger(?:\s+tour)?)\b"
)


def _is_allowed_tennis_match(match) -> bool:
    """Allow named professional singles categories; exclusions always win."""
    def normalized(value):
        return "".join(
            char for char in unicodedata.normalize("NFKD", (value or "").casefold())
            if not unicodedata.combining(char)
        ).replace("ł", "l")

    competition = normalized(match.competition)
    name = normalized(match.name)
    # Pair names can omit 'doubles' but still list multiple players on each side.
    if any(separator in name for separator in ("/", "&", "+")) or _TENNIS_EXCLUDED.search(f"{competition} {name}"):
        return False
    competition = re.sub(r"[-–—/]+", " ", competition)
    return bool(_TENNIS_ALLOWED.search(competition))


def _fetch_matches_page(
    sport: str, offset: int, *, timeout: tuple[float, float] | None = None,
) -> dict[str, Any]:
    key = f"matches:{sport}:{offset}"
    if (hit := _cached(key)) is not None:
        return hit
    try:
        client = _client() if timeout is None else _client(timeout=timeout)
        result = client.get_matches(sport, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Betclic upstream error: {exc}") from exc
    return _store(key, result)


def _fetch_tennis_menu() -> list[TennisCompetition]:
    global _tennis_menu_cache
    with _tennis_menu_lock:
        if _tennis_menu_cache and time.monotonic() - _tennis_menu_cache[0] < SPORT_MENU_CACHE_TTL:
            return _tennis_menu_cache[1]
        menu = fetch_sport_menu(_client(timeout=(1, TODAY_TENNIS_TIMEOUT)))
        _tennis_menu_cache = (time.monotonic(), menu)
        return menu


def _is_allowed_tennis_competition(competition: TennisCompetition) -> bool:
    if competition.category.casefold() in ("zwycięzca", "zwyciezca", "outright", "winner"):
        return False
    return _is_allowed_tennis_match(SimpleNamespace(
        competition=f"{competition.category} {competition.name}", name="",
    ))


def _fetch_tennis_competition(competition_id: int):
    key = f"tennis_competition:{competition_id}"
    if (hit := _cached(key)) is not None:
        return hit
    matches = fetch_competition_matches(_client(timeout=(1, TODAY_TENNIS_TIMEOUT)), competition_id)
    return _store(key, matches)


def _fetch_today_tennis_competitions() -> tuple[list[Any], dict[str, Any]]:
    """Discover allowed competition IDs via SportMenu, then fetch their snapshots."""
    target = datetime.now(WARSAW).date()
    deadline = time.monotonic() + TODAY_TENNIS_TIMEOUT + 1
    matches: list[Any] = []
    seen_ids: set[Any] = set()
    filtered_out = 0
    errors: list[dict[str, Any]] = []
    competitions: list[TennisCompetition] = []
    executor = ThreadPoolExecutor(max_workers=max(1, TODAY_TENNIS_WORKERS))
    try:
        menu_future = executor.submit(_fetch_tennis_menu)
        try:
            menu = menu_future.result(timeout=max(0, deadline - time.monotonic()))
            competitions = [item for item in menu if _is_allowed_tennis_competition(item)]
        except Exception as exc:
            menu_future.cancel()
            errors.append({"stage": "sport_menu", "detail": str(exc) or "SportMenu timeout"})

        futures = {item.id: executor.submit(_fetch_tennis_competition, item.id) for item in competitions}
        completed, pending = wait(futures.values(), timeout=max(0, deadline - time.monotonic()))
        for future in pending:
            future.cancel()
        for item in competitions:
            future = futures[item.id]
            try:
                if future not in completed:
                    raise TimeoutError(f"Competition discovery timeout after {TODAY_TENNIS_TIMEOUT + 1:g}s")
                for match in future.result():
                    identity = match.id if match.id is not None else (match.name, match.date)
                    if identity in seen_ids:
                        continue
                    seen_ids.add(identity)
                    dt = _parse_dt(match.date)
                    if not dt or dt.date() != target:
                        continue
                    candidate = SimpleNamespace(
                        competition=f"{item.category} {match.competition or item.name}", name=match.name,
                    )
                    if match.competition_id not in (None, item.id) or not _is_allowed_tennis_match(candidate):
                        filtered_out += 1
                        continue
                    matches.append(match)
            except Exception as exc:
                errors.append({"competition_id": item.id, "detail": str(exc)})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return matches, {
        "source": "competitions",
        "competition_ids": [item.id for item in competitions],
        "filtered_out": filtered_out,
        "partial": bool(errors),
        "errors": errors,
    }


def _fetch_today(sport: str, chunk: int = 0) -> tuple[list[Any], dict[str, Any]]:
    """Use competitions for tennis and bounded page batches for the other sports."""
    if sport == "tennis":
        return _fetch_today_tennis_competitions()

    target = datetime.now(WARSAW).date()
    matches: list[Any] = []
    seen_ids: set[Any] = set()
    upstream_total = 0
    pages_scanned = 0
    batches_scanned = 0
    errors: list[dict[str, Any]] = []
    stop = False
    # Retain the legacy per-request safety cap without skipping pages between chunks.
    pages_per_chunk = max(1, min(TODAY_PAGES_PER_CHUNK, TODAY_MAX_PAGES))
    start_page = chunk * pages_per_chunk
    workers = max(1, min(TODAY_WORKERS, pages_per_chunk))

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        while pages_scanned < pages_per_chunk and not stop:
            offsets = range(
                (start_page + pages_scanned) * TODAY_PAGE_SIZE,
                (start_page + min(pages_scanned + workers, pages_per_chunk)) * TODAY_PAGE_SIZE,
                TODAY_PAGE_SIZE,
            )
            futures = {
                offset: executor.submit(
                    _fetch_matches_page, sport, offset, timeout=(1, TODAY_UPSTREAM_TIMEOUT),
                )
                for offset in offsets
                if not upstream_total or offset < upstream_total
            }
            if not futures:
                stop = True
                break
            # Bound the wait too: the client's streaming reads can outlast a
            # socket timeout if the upstream keeps sending small amounts of data.
            completed, pending = wait(futures.values(), timeout=TODAY_UPSTREAM_TIMEOUT + 1)
            for future in pending:
                future.cancel()
            pages_scanned += len(futures)
            batches_scanned += 1

            # Keep completed results in offset order even if another page
            # signals the end, fails, or exceeds the batch deadline.
            for offset, future in futures.items():
                try:
                    if future not in completed:
                        raise TimeoutError(f"Betclic upstream timeout after {TODAY_UPSTREAM_TIMEOUT + 1:g}s")
                    result = future.result()
                    page = list(result.get("matches", []))
                    total = int(result.get("total", 0) or 0)
                    page_matches = []
                    has_future_event = False
                    for match in page:
                        dt = _parse_dt(match.date)
                        if dt and dt.date() > target:
                            has_future_event = True
                        if dt and dt.date() == target:
                            identity = match.id if match.id is not None else (match.name, match.date)
                            page_matches.append((identity, match))
                except Exception as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    errors.append({"offset": offset, "detail": detail})
                    continue

                upstream_total = max(upstream_total, total)
                for identity, match in page_matches:
                    if identity not in seen_ids:
                        seen_ids.add(identity)
                        matches.append(match)
                if not page or has_future_event or (upstream_total and offset + TODAY_PAGE_SIZE >= upstream_total):
                    stop = True
    finally:
        # A slow request must not delay the partial response during pool cleanup.
        executor.shutdown(wait=False, cancel_futures=True)

    return matches, {
        "chunk": chunk,
        "next_chunk": None if stop else chunk + 1,
        "done": stop,
        "upstream_total": upstream_total,
        "pages_scanned": pages_scanned,
        "batches_scanned": batches_scanned,
        "filtered_out": 0,
        "partial": bool(errors) or not stop,
        "errors": errors,
    }


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
            "today": "/today?sport=football&competition=Ekstraklasa",
            "event": "/event/{match_id}?compact=true",
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


@app.get("/today")
def today_events(
    sport: str = Query("football"),
    competition: str | None = Query(None, description="Competition name substring"),
    chunk: Annotated[int, Query(ge=0, description="Zero-based chunk index; ignored for tennis (competition discovery)")] = 0,
):
    """Return today's tennis competition matches or a chunk of another sport."""
    matches, scan = _fetch_today(sport, chunk)
    if competition:
        needle = competition.casefold()
        matches = [m for m in matches if needle in (m.competition or "").casefold()]
    return {
        "sport": sport,
        "competition": competition,
        "returned": len(matches),
        **scan,
        "generated_at_warsaw": datetime.now(WARSAW).isoformat(),
        "events": [_match_to_dict(m, include_markets=False) for m in matches],
    }


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
    output = [_match_to_dict(match, include_markets=False) for match in matches]
    detail_indexes = [i for i, match in enumerate(matches) if match.id][:SLATE_ODDS_LIMIT] if odds else []
    if detail_indexes:
        workers = max(1, min(SLATE_ODDS_WORKERS, len(detail_indexes)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_fetch_event, int(matches[i].id)): i
                for i in detail_indexes
            }
            for future in as_completed(futures):
                output[futures[future]] = _match_to_dict(future.result(), include_markets=True)

    return {
        "sport": sport,
        "today_only": today,
        "offset": offset,
        "returned": len(output),
        "odds_enriched": len(detail_indexes),
        "odds_limit": SLATE_ODDS_LIMIT if odds else 0,
        "upstream_total": result.get("total", 0),
        "generated_at_warsaw": datetime.now(WARSAW).isoformat(),
        "events": output,
    }


@app.get("/event/{match_id}")
def event(
    match_id: int,
    category: str | None = Query(None),
    compact: bool = Query(False),
    include_suspended: bool = Query(False),
):
    """
    Return detailed markets for one event.

    For football, `category` may be one of:
    result, goals, score_handicap, top, goalscorers, props.
    Without a category, Betclic's default market bundle is returned.
    """
    match = _fetch_event(match_id, category=category)
    return _match_to_dict(
        match,
        include_markets=True,
        compact=compact,
        include_suspended=include_suspended,
    )


@app.get("/event/{match_id}/football-all")
def event_football_all(match_id: int):
    """Merge the default bundle with all football market categories known by the upstream client."""
    base = _fetch_event(match_id)
    base_dict = _match_to_dict(base, include_markets=True)
    markets = list(base_dict.get("markets", []))

    for category in MARKET_CODES:
        match = _fetch_event(match_id, category=category)
        markets.extend(_market_to_dict(m) for m in (match.markets or []) if not m.suspended)
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
            # Preserve the legacy /search?detail=true representation.
            output.append(_match_to_dict(
                _fetch_event(int(match.id)),
                include_markets=True,
                include_suspended=True,
            ))
        else:
            output.append(_match_to_dict(match, include_markets=False))

    return {"query": q, "returned": len(output), "events": output}
