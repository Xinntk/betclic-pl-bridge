"""Read-only live diagnostic: python diagnose_tennis.py [--output report.json]."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import time

from app import WARSAW, _client, _parse_dt, _is_allowed_tennis_competition
from tennis_competitions import fetch_sport_menu, fetch_competition_matches, MATCH_METHOD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = datetime.now(WARSAW)
    started = time.monotonic()
    menu = fetch_sport_menu(_client(timeout=(2, 6)))
    us_open = [item for item in menu if "us open" in item.name.casefold() and _is_allowed_tennis_competition(item)]

    def fetch(item):
        begin = time.monotonic()
        matches = fetch_competition_matches(_client(timeout=(2, 6)), item.id)
        today = [match for match in matches if (dt := _parse_dt(match.date)) and dt.date() == now.date()]
        return {"competition": asdict(item), "elapsed_seconds": round(time.monotonic() - begin, 3),
                "returned": len(matches), "today": [asdict(match) for match in today]}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(fetch, us_open))
    report = {"checked_at_warsaw": now.isoformat(), "method": MATCH_METHOD,
              "tennis_menu_competitions": len(menu),
              "elapsed_seconds": round(time.monotonic() - started, 3), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not any(result["today"] for result in results):
        raise SystemExit("No today's US Open matches found; diagnostic has not confirmed discovery.")


if __name__ == "__main__":
    main()
