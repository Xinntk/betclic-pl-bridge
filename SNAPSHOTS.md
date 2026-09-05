# Betclic snapshot architecture

This repository has two independent data paths. Both talk directly to Betclic's public offering backend. Vercel is **not** part of snapshot generation.

## 1. Daily odds snapshot

Workflow: `.github/workflows/refresh-bridge-snapshot.yml`

- Runs every 5 minutes and can also be dispatched manually.
- Entrypoint: `odds_refresh.py`.
- Source: direct Betclic client configured for the Polish offering.
- Output:
  - `snapshots/latest.json` — today's curated football and tennis events with compact markets.
  - `snapshots/status.json` — health/status information for the last refresh.
- A single event failure does not invalidate the whole snapshot.
- If an event refresh fails, same-day last-good event data can be reused and the event ID is listed in `stale_event_ids`.
- If a sport discovery fails, same-day last-good sport data can be reused and `stale` is set.
- At a day boundary, yesterday's data is never silently relabelled as today's data.
- JSON is written atomically.
- Generated files are validated before publication.
- Git publication retries against the latest `main`, so concurrent non-conflicting writers do not require manual rebases.

Consumers should always verify:

1. `date_warsaw` is today in `Europe/Warsaw`.
2. `snapshots/status.json` has `ok: true`.
3. Prefer `degraded: false`; if degraded, inspect stale/summary-only/error fields before trusting an affected event.

## 2. MyCombi on demand

Workflow: `.github/workflows/refresh-mycombi.yml`

MyCombi is deliberately **not** downloaded for every football event. A full all-event MyCombi dump is unnecessarily large and puts avoidable load on Betclic. Only requested events/combinations are queried.

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

It contains the event's MyCombi markets, `market_id`, `selection_id`, selection odds/status and BetBuilder compatibility. Inspect metadata may be cached for up to 15 minutes. A failed refresh may reuse same-day metadata only when it is explicitly marked `stale`.

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

A quote is queried live from Betclic. A failed quote is **never** replaced with an old price. Check `valid`, `odds`, and `errors`.

Status: `snapshots/mycombi_status.json`

Limits:

- up to 10 inspect requests per run;
- up to 40 quote requests per run;
- 2–20 distinct selections per quote.

Updating `snapshots/mycombi_requests.json` triggers the MyCombi workflow immediately. Keep the queue small and replace old requests rather than accumulating history.

## Dependency stability

The custom `betclic-api` dependency is pinned to a known-good commit rather than a moving `main` branch. Runtime package versions are pinned in `requirements.txt`.

If Betclic changes its upstream protocol, health/status validation should fail or mark data degraded instead of silently publishing malformed odds. Such an upstream protocol change is the main remaining external failure mode and requires adapting the parser/client.
