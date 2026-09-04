"""Public Betclic competition discovery; wire fields verified in the PL frontend."""

from dataclasses import dataclass
import re
import struct

from betclic_api.parser import parse_match_from_proto
from betclic_api.proto import decode_protobuf, encode_field_string, encode_field_varint, grpc_frame


BASE_URL = "https://offering.begmedia.com/web/offering.access.api"
MENU_SERVICE = "offering.access.api.SportMenuService"
MENU_METHOD = "GetSportMenu"
MATCH_SERVICE = "offering.access.api.MatchService"
MATCH_METHOD = "GetMatchesByCompetitionWithNotifications"


@dataclass(frozen=True)
class TennisCompetition:
    id: int
    name: str
    category: str = ""


def _text(fields, number):
    value = fields.get(number, [b""])[0]
    return value.decode("utf-8") if isinstance(value, bytes) else ""


def parse_sport_menu(payload: bytes) -> list[TennisCompetition]:
    """SportMenu: sports=2; tennis categories=6; competition id/name=1/2."""
    competitions = {}

    def add(raw, category=""):
        item = decode_protobuf(raw)
        if _text(item, 3) not in ("", "tennis") or item.get(6, [0])[0]:
            return  # Multi-competition menu links are not individual IDs.
        identity = item.get(1, [0])[0]
        if isinstance(identity, int) and identity > 0:
            previous = competitions.get(identity)
            if previous is None or category:
                competitions[identity] = TennisCompetition(identity, _text(item, 2), category)

    menu = decode_protobuf(payload)
    for raw_sport in menu.get(2, []):
        sport = decode_protobuf(raw_sport)
        if _text(sport, 2) != "tennis":
            continue
        for raw in sport.get(1, []) + sport.get(5, []):
            add(raw)
        for raw_category in sport.get(6, []):
            category = decode_protobuf(raw_category)
            for raw in category.get(2, []):
                add(raw, _text(category, 1))
        for raw_country in sport.get(7, []):
            country = decode_protobuf(raw_country)
            for raw in country.get(3, []):
                add(raw)
    return list(competitions.values())


def parse_competition_matches(payload: bytes):
    """GetCompetitionPayload contains full initial match list in field 3."""
    fields = decode_protobuf(payload)
    matches = []
    for raw in fields.get(3, []):
        match = parse_match_from_proto(raw)
        # Betclic emits seven fractional digits; Python 3.10 accepts up to six.
        if match.date:
            match.date = re.sub(r"(T\d{2}:\d{2}:\d{2}\.\d{6})\d+", r"\1", match.date)
        # The upstream parser confuses field 6 (has_live_stream) with is_live (4).
        match.is_live = bool(decode_protobuf(raw).get(4, [0])[0])
        matches.append(match)
    return matches


def _initial_payload(client, service, method, request, payload_field):
    """Read the initial snapshot, then close the live notification stream."""
    with client._session.post(
        f"{BASE_URL}/{service}/{method}", data=grpc_frame(request),
        timeout=client.timeout, stream=True,
    ) as response:
        response.raise_for_status()
        buffer = bytearray()

        def read_exact(size):
            while len(buffer) < size:
                # read() may wait for another HTTP chunk on a live gzip stream,
                # even when the decompressor has emitted the complete snapshot.
                chunk = response.raw.read1(65536, decode_content=True)
                if not chunk:
                    raise RuntimeError("Incomplete gRPC-web frame")
                buffer.extend(chunk)
            data = bytes(buffer[:size])
            del buffer[:size]
            return data

        while True:
            header = read_exact(5)
            flags, size = header[0], struct.unpack(">I", header[1:])[0]
            if size > 8_000_000:
                raise RuntimeError("gRPC-web frame exceeds size limit")
            data = read_exact(size)
            if flags & 0x80:
                raise RuntimeError(f"gRPC stream ended before payload: {data.decode('utf-8', errors='replace')}")
            if flags:
                raise RuntimeError(f"Unsupported gRPC-web frame flags: {flags}")
            root = decode_protobuf(data)
            if payload_field in root:
                return root[payload_field][0]


def fetch_sport_menu(client):
    payload = _initial_payload(
        client, MENU_SERVICE, MENU_METHOD, encode_field_string(1, client.locale), 3,
    )
    return parse_sport_menu(payload)


def fetch_competition_matches(client, competition_id: int):
    request = encode_field_varint(1, competition_id) + encode_field_string(3, client.locale)
    payload = _initial_payload(client, MATCH_SERVICE, MATCH_METHOD, request, 1)
    return parse_competition_matches(payload)
