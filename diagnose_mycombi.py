"""Read-only PL offering diagnostic; no account or betslip interaction."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from app import WARSAW, _client
from mycombi import METHOD, SERVICE, fetch_mycombi, quote_mycombi


def selection(value):
    try:
        market_id, selection_id = map(int, value.split(":"))
        if not all(0 < identity < 2**63 for identity in (market_id, selection_id)):
            raise ValueError
        return {"market_id": str(market_id), "selection_id": str(selection_id)}
    except ValueError:
        raise argparse.ArgumentTypeError("Use positive int64 MARKET_ID:SELECTION_ID")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_id", type=int)
    parser.add_argument("--selection", type=selection, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 0 < args.event_id < 2**63:
        parser.error("event_id must be a positive int64")
    client = _client(timeout=(1, 4))
    report = {"checked_at_warsaw": datetime.now(WARSAW).isoformat(), "service": SERVICE, "method": METHOD,
              "metadata": fetch_mycombi(client, args.event_id)}
    if args.selection:
        report["quote"] = quote_mycombi(client, args.event_id, args.selection)
    encoded = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded)
    if report["metadata"]["errors"]:
        raise SystemExit(1)
    if args.selection and not report["quote"]["valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
