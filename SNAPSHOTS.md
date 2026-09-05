# Betclic snapshot architecture

This repository has two independent data paths. Both talk directly to Betclic's public offering backend. Vercel is **not** part of snapshot generation.

## Freshness policy

The system favors live Betclic data, but one transient upstream/discovery failure must not make a valid event disappear.

- Default maximum fallback age: **90 minutes (5400 seconds)**.
- A recent same-day last-good price may be used only when the current refresh fails.
- Fallback data is always explicitly marked `stale` / `fallback` and includes its age.
- The stale clock is anchored to the first failed refresh and does **not** reset on later failed refreshes.
- Once the 90-minute limit is exceeded, old market prices/quotes are not treated as current.
- Yesterday's data is never silently reused as today's data.

The 90-minute limit is deliberately conservative: an approximately one-hour-old price is still useful as a temporary reference, while multi-hour-old prices are not.

## 1. Daily odds snapshot

Workflow: `.github/workflows/refresh-bridge-snapshot.yml`

- Runs every 5 minutes and can also be dispatched manually.
- Entrypoint: `odds_refresh.py`.
- Source: direct Betclic client configured for the Polish offering.
- Output:
  - `snapshots/latest.json` — today's curated football and tennis events with compact markets.
  - `snapshots/status.json` — health/status information for the last refresh.
- A single event failure does not invalidate the whole snapshot.
- Malformed discovery rows (for example `id: null`) are rejected before publication.
- If an event detail refresh fails, recent same-day last-good event data may be reused and the event ID is listed in `stale_event_ids`.
- If a normally present event disappears from one discovery pass, the previous same-day event is retained for at most 90 minutes and marked with `_snapshot_meta.reason = "missing_from_discovery"`.
- If a complete sport discovery fails, recent same-day sport data may be reused within the same bounded freshness policy.
- Old fallback market prices are removed after the freshness limit instead of being silently carried forever.
- At a day boundary, yesterday's data is never silently relabelled as today's data.
- JSON is written atomically.
- Generated files are validated before publication.
- Git publication retries against the latest `main`, so concurrent non-conflicting writers do not require manual rebases.

Consumers should always verify:

1. `date_warsaw` is today in `Europe/Warsaw`.
2. `snapshots/status.json` has `ok: true`.
3. Prefer `degraded: false`; if degraded, inspect stale/summary-only/error fields for the affected event.
4. If an event is stale, inspect `_snapshot_meta.fallback_age_seconds` before using its prices.

## 2. MyCombi on demand

Workflow: `.github/workflows/refresh-mycombi.yml`

MyCombi is deliberately **not** downloaded for every football event. A full all-event MyCombi dump is unnecessarily large and puts avoidable load on Betclic. Only requested events/combinations are queried.

An explicit MyCombi request is allowed to query Betclic directly even if the event was omitted by one degraded daily snapshot. Daily snapshot membership is therefore advisory rather than a hard gate for an explicitly requested event.

Input: `snapshots/mycombi_requests.json`

### Inspect an event

```json
{
  "requests": [
    {
      "request_id": "unique-id",
      "action": "inspect",
      "event_id": 123456789,
      "label": "optional human label"
    }
  ]
}
```

Output: `snapshots/mycombi.json`

It contains the event's MyCombi markets, `market_id`, `selection_id`, selection odds/status and BetBuilder compatibility. Inspect metadata may be cached. If a refresh fails, cached same-day metadata can only be used inside the bounded freshness window and must be explicitly marked stale. Expired cached metadata is not exposed as usable current markets.

### Quote a concrete MyCombi combination

```json
{
  "requests": [
    {
      "request_id": "unique-quote-id",
      "action": "quote",
      "event_id": 123456789,
      "label": "Team to win + over 1.5",
      "selections": [
        {"market_id": 111, "selection_id": 222},
        {"market_id": 333, "selection_id": 444}
      ]
    }
  ]
}
```

Output: `snapshots/mycombi_quotes.json`

The workflow always attempts a live Betclic quote first. Successful live quotes are persisted in `snapshots/mycombi_last_good_quotes.json`. If a later live attempt fails, the exact same combination may use its last successful quote only when it is from today and no older than 90 minutes. Such a result is explicitly marked:

- `valid: true`
- `fallback: true`
- `stale: true`
- `fallback_age_seconds: ...`
- `live_errors: [...]`

A quote older than the configured fallback limit is never substituted for a failed live quote.

Status: `snapshots/mycombi_status.json`

Limits:

- up to 10 inspect requests per run;
- up to 40 quote requests per run;
- 2–20 distinct selections per quote.

Updating `snapshots/mycombi_requests.json` triggers the MyCombi workflow immediately. Keep the queue small and replace old requests rather than accumulating history.

## Dependency stability

The custom `betclic-api` dependency is pinned to a known-good commit rather than a moving `main` branch. Runtime package versions are pinned in `requirements.txt`.

If Betclic changes its upstream protocol, health/status validation should fail or mark data degraded instead of silently publishing malformed odds. Such an upstream protocol change is the main remaining external failure mode and requires adapting the parser/client.
