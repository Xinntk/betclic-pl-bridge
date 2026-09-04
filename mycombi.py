"""Read-only MyCombi offering RPC, verified against the public PL frontend.

No account, betslip or placement service is used. Market IDs refer to the
displayed MyCombi market, not Selection.betslip_market_id (they can differ).
"""

import math
import struct

from betclic_api.proto import decode_protobuf, encode_field_string, encode_field_varint
from tennis_competitions import _initial_payload

SERVICE = "offering.access.api.MyCombiService"
METHOD = "GetMyCombiWithNotifications"
ERROR_CODES = {0: "UNKNOWN", 1: "NOT_COMBINABLE", 2: "NOT_COMPATIBLE",
               3: "ALL_SELECTIONS_SUSPENDED", 4: "MAX_SELECTIONS_REACHED"}
STATUSES = {0: "UNSPECIFIED", 1: "ONLINE", 2: "SUSPENDED", 3: "CLOSED"}


def _text(fields, key):
    value = fields.get(key, [b""])[0]
    return value.decode("utf-8") if isinstance(value, bytes) else ""


def _odds(fields, key):
    raw = fields.get(key, [None])[0]
    if raw is None:
        return None
    if not isinstance(raw, bytes) or len(raw) != 8:
        raise ValueError("Malformed MyCombi odds")
    value = struct.unpack("<d", raw)[0]
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid MyCombi odds")
    return value


def build_request(event_id, selections, language="pl"):
    return (encode_field_varint(1, event_id) + encode_field_string(2, language)
            + b"".join(encode_field_string(3, encode_field_varint(1, int(item["market_id"]))
                                            + encode_field_varint(2, int(item["selection_id"])))
                       for item in selections))


def _selection_rows(market):
    yield from market.get(16, [])  # main selections
    for row in market.get(10, []):  # nullable matrix cells
        for cell in decode_protobuf(row).get(1, []):
            yield from decode_protobuf(cell).get(1, [])
    for group in market.get(11, []):  # split cards
        yield from decode_protobuf(group).get(2, [])
    for slider in market.get(15, []):
        for value in decode_protobuf(slider).get(3, []):
            for cell in decode_protobuf(value).get(2, []):
                yield from decode_protobuf(cell).get(1, [])


def _markets(raw, depth=0):
    if depth > 12:
        raise ValueError("MyCombi market nesting exceeds limit")
    market = decode_protobuf(raw)
    market_id = str(market.get(1, [0])[0])
    selections = {}
    for row in _selection_rows(market):
        selection = decode_protobuf(row)
        identity = str(selection.get(1, [0])[0])
        if identity == "0":
            continue
        selections[identity] = {
            "selection_id": identity, "market_id": market_id,
            "name": _text(selection, 10), "odds": _odds(selection, 12),
            "status": STATUSES.get(selection.get(14, [0])[0], "UNSPECIFIED"),
            "is_betbuilder_compatible": bool(selection[7][0]) if 7 in selection else None,
        }
    if selections and market_id != "0":
        yield {"market_id": market_id, "name": _text(market, 2),
               "is_betbuilder_eligible": bool(market.get(7, [0])[0]),
               "selections": list(selections.values())}
    for child in market.get(13, []):
        yield from _markets(child, depth + 1)
    for tab in market.get(14, []):
        for child in decode_protobuf(tab).get(2, []):
            yield from _markets(child, depth + 1)


def parse_payload(raw, event_id):
    envelope = decode_protobuf(raw)
    if 2 in envelope:
        error = decode_protobuf(envelope[2][0])
        return {"event_id": str(event_id), "available": False, "markets": [], "odds": None,
                "errors": [{"code": ERROR_CODES.get(error.get(1, [0])[0], "UNKNOWN"),
                            "title": _text(error, 2), "detail": _text(error, 3)}]}
    if 1 not in envelope:
        raise ValueError("Missing MyCombi response payload")
    payload = decode_protobuf(envelope[1][0])
    if 1 not in payload:
        raise ValueError("Missing MyCombi match")
    match = decode_protobuf(payload[1][0])
    if match.get(1, [None])[0] != event_id:
        raise ValueError("MyCombi returned a different event")
    roots = match.get(9, []) + match.get(25, [])
    for subcategory in match.get(11, []):
        roots += decode_protobuf(subcategory).get(3, [])
    markets = {}
    for root in roots:
        for market in _markets(root):
            key = market["market_id"]
            if key in markets:
                existing = {s["selection_id"]: s for s in markets[key]["selections"]}
                existing.update({s["selection_id"]: s for s in market["selections"]})
                market["selections"] = list(existing.values())
            markets[key] = market
    return {"event_id": str(event_id), "name": _text(match, 2), "date": _text(match, 3),
            "is_live": bool(match.get(4, [0])[0]),
            "available": bool(match.get(14, [0])[0]), "markets": list(markets.values()),
            "odds": _odds(payload, 2), "errors": []}


def fetch_mycombi(client, event_id, selections=()):
    raw = _initial_payload(client, SERVICE, METHOD,
                           build_request(event_id, selections, client.locale), 1)
    return parse_payload(raw, event_id)


def quote_mycombi(client, event_id, selections):
    """Validate event membership against public metadata, then query the calculator."""
    selections = [{"market_id": str(s["market_id"]), "selection_id": str(s["selection_id"])}
                  for s in selections]
    result = {"valid": False, "odds": None, "selections": selections, "errors": []}
    if len(selections) < 2 or len({s["selection_id"] for s in selections}) != len(selections):
        result["errors"] = [{"code": "INVALID_SELECTIONS", "detail": "Choose at least two distinct selections"}]
        return result
    metadata = fetch_mycombi(client, event_id)
    if metadata["errors"] or not metadata["available"]:
        result["errors"] = metadata["errors"] or [{"code": "UNAVAILABLE", "detail": "MyCombi is unavailable for this event"}]
        return result
    available = {(m["market_id"], s["selection_id"]): s for m in metadata["markets"]
                 if m["is_betbuilder_eligible"] for s in m["selections"]}
    for requested in selections:
        selection = available.get((requested["market_id"], requested["selection_id"]))
        if not selection or selection["status"] != "ONLINE" or selection["is_betbuilder_compatible"] is not True:
            result["errors"].append({"code": "SELECTION_UNAVAILABLE", **requested,
                                     "detail": "Selection is not available for this event's MyCombi"})
    if result["errors"]:
        return result
    response = fetch_mycombi(client, event_id, selections)
    result["errors"] = response["errors"]
    if not result["errors"]:
        if response["odds"] is None or response["odds"] <= 1:
            result["errors"] = [{"code": "NO_QUOTE", "detail": "Upstream did not return a valid combined price"}]
        else:
            result.update(valid=True, odds=response["odds"])
    return result
