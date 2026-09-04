# Potwierdzenie competition-based discovery, 2026-09-04

Źródło schematu: publiczny frontend [Betclic PL](https://www.betclic.pl/tenis-s2),
bundle [main-LHSS6QCN.js](https://www.betclic.pl/main-LHSS6QCN.js).
SHA-256 pobranego bundla:
`742214fadeb79aa2af97e68220ae47e3dd8711886bda55ab6fecc02576548dd4`.

## Schemat odczytany z bundla

Bazowy adres: `https://offering.begmedia.com/web/offering.access.api`.
Zapytania używają publicznych nagłówków PL i języka `pl` z `_client()`.

| Metoda | Request protobuf | Response protobuf |
| --- | --- | --- |
| `offering.access.api.SportMenuService/GetSportMenu` | pole 1: language (string) | pole 3: SportMenu |
| `offering.access.api.MatchService/GetMatchesByCompetitionWithNotifications` | pole 1: competition_id (int64), pole 3: language (string) | pole 1: GetCompetitionPayload; jego pole 3: powtarzane Match |

SportMenu: pole 2 to lista sportów. SportMenuItem: sport_code=2,
tops_and_pinned=1, competitions=5, categories=6, countries=7.
CategoryItem: name=1, competitions=2. CountryItem: competitions=3.
CompetitionItem: competition_id=1, competition_name=2, sport_code=3,
is_multi_competition=6. Zbiorcze linki menu nie są traktowane jako pojedyncze ID.

Match: match_id=1, name=2, match_date_utc=3, is_live=4, has_live_stream=6,
competition=8, contestants=12. Parser biblioteki myli pole 6 z is_live;
nowy parser poprawia to wyłącznie dla ścieżki competition-based.

## Potwierdzenie przed zmianą `/today`

Uruchomiono `python diagnose_tennis.py --output .venv/tennis-diagnostic.json`.
Pomyślny pomiar: **2026-09-04 13:29:35 Europe/Warsaw**.
SportMenu oraz obie listy US Open pobrano łącznie w **0,344 s**.

| ID z menu | Rozgrywki | Wszystkie mecze odpowiedzi | Dzisiaj w Warszawie |
| --- | --- | --- | --- |
| 196 | US Open M. | 16 | 6 |
| 211 | US Open K. | 16 | 7 |

Przykłady zweryfikowanych wydarzeń:

- `1214690727329792`: Daniil Medvedev – Arthur Rinderknech, `2026-09-04T16:10:00.0000000Z`.
- `1215017185742848`: Alexander Bublik – Tommy Paul, `2026-09-04T16:10:00.0000000Z`.
- `1215030446030848`: Yibing Wu – Carlos Alcaraz, `2026-09-04T17:00:00.0000000Z`.

Pierwszy odczyt przez `raw.read()` czekał na kolejne porcje strumienia gzip.
Zastąpiono go `raw.read1()` z buforem kompletnego gRPC-web frame i zamknięciem
odpowiedzi po pierwszym payloadzie. To nie wymaga oczekiwania na trailer ani
kolejne powiadomienia. Dlatego zależności wymagają urllib3 2.2+.

Daty miały siedem cyfr ułamka sekundy. Python 3.10 odrzucał je przy
`datetime.fromisoformat()`. Normalizacja w nowym parserze zachowuje sześć cyfr
i umożliwia poprawny filtr Europe/Warsaw bez zmiany ścieżki football.

## Weryfikacja po podłączeniu

O 13:31:40 lokalny `/today?sport=tennis` pobrał z publicznego upstreamu **25**
dzisiejszych wydarzeń, w tym **13 US Open**, w **0,437 s**.
`source="competitions"`, `partial=false`, `errors=[]`.
Użyte ID: `[196, 211, 18015, 35923, 28865, 22265]`.

To pomiary lokalnego kodu z aktualnym upstreamem, nie pomiary wdrożenia Vercel.
Menu, dostępność meczów i czasy odpowiedzi zmieniają się; skrypt diagnostyczny
pozwala powtórzyć sprawdzenie. ID rozgrywek nie są zakodowane w implementacji.
