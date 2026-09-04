# MyCombi PL: potwierdzenie publicznego kalkulatora, 2026-09-04

Źródła: aktualny frontend [Betclic PL](https://www.betclic.pl/pilka-nozna-sfootball),
[main-LHSS6QCN.js](https://www.betclic.pl/main-LHSS6QCN.js),
[chunk-DEcDEqnw.js](https://www.betclic.pl/chunk-DEcDEqnw.js).

SHA-256 pobranych plików:

- main: `742214fadeb79aa2af97e68220ae47e3dd8711886bda55ab6fecc02576548dd4`
- chunk strony meczu: `3d05268fde9d31d00101cada95ac56fceb0c008a86bc9b6a37ba82177e3383ee`

## Ścieżka frontendu i protokół

Przeanalizowano kod, nie wykonywano kliknięć dodających typy do betslipa.
W chunku strony meczu `myCombiSelectionClicked` uruchamia
`prepareBetBuilderSelections`, następnie `fetchBetbuilderEvent`.
Lista `marketsIdsAndSelectionIds` trafia do `getMyCombiWithNotifications`.
Repozytorium odczytów buduje `{language, matchId, combinations}`; nie jest to API konta.

Dokładny adres wywołany diagnostycznie:

```text
https://offering.begmedia.com/web/offering.access.api/offering.access.api.MyCombiService/GetMyCombiWithNotifications
```

To gRPC-web POST z nagłówkami publicznej oferty PL. Metoda zwraca pierwszy snapshot,
a następnie może wysyłać powiadomienia. Bridge zamyka strumień po pełnym pierwszym
payloadzie; nie czeka na zakończenie streamu. Brak cookies konta i Authorization.

| Wiadomość | Pola protobuf |
| --- | --- |
| GetMyCombiRequest | 1: match_id (int64), 2: language (string), 3: repeated MarketAndSelection |
| MarketAndSelection | 1: market_id (int64), 2: selection_id (int64) |
| GetMyCombiResponse | 1: MyCombiResponsePayload, 2: notifications (alternatywa) |
| MyCombiResponsePayload | 1: GetMyCombiPayload albo 2: ComboSelectionError |
| GetMyCombiPayload | 1: Match, 2: computed_odds (double) |
| ComboSelectionError | 1: enum, 2: title, 3: description |

Pusta lista `combinations` daje katalog MyCombi. `Match` zawiera ID=1,
nazwę=2, datę=3, is_live=4, is_betbuilder_eligible=14. Markety są w polach
9, 11→3 i 25. Market: ID=1, nazwa=2, eligibility=7; selekcje występują jako
main_selections=16, matrix=10→1→1, split_cards=11→2 i sliders=15→3→2→1.
Zagnieżdżone markety: group_markets=13 i tabs=14→2.

Selection: ID=1, compatibility=7, name=10, odds=12 (double), status=14.
Statusy: 0 UNSPECIFIED, 1 ONLINE, 2 SUSPENDED, 3 CLOSED.
Błędy kombinacji: 0 UNKNOWN, 1 NOT_COMBINABLE, 2 NOT_COMPATIBLE,
3 ALL_SELECTIONS_SUSPENDED, 4 MAX_SELECTIONS_REACHED.

**ID marketu to ID kontenera MyCombi, nie Selection.betslip_market_id (pole 15).**
Frontend używa `selection.marketId`. W odpowiedzi wykryto różne wartości tych ID.

## Potwierdzenie przed integracją z app.py

Event `1210189877370880`: **Paris Saint-Germain – Monaco**, start
`2026-09-04T19:05:00Z` (21:05 Warszawa), `is_live=false`.

O 19:59:31 Europe/Warsaw wykonano:

```text
python diagnose_mycombi.py 1210189877370880 --selection 1211746605895682:1211746605891589 --selection 1211747370299420:1211747370295431 --output .venv/mycombi-diagnostic.json
```

Katalog: **95 marketów, 574 selekcje**. Kombinacja „Paris Saint-Germain” oraz
„Powyżej 2,5” dała `valid=true`, `odds=1.68`, `errors=[]`.
Oddzielna próba PSG + remis z rynku wyniku meczu zwróciła enum **NOT_COMBINABLE**
oraz polski opis błędu. Kursu nie mnożono ani nie wyliczano lokalnie.

## Potwierdzenie przez endpointy bridge

O 20:03:43 Europe/Warsaw lokalny TestClient wywołał GET metadanych i POST quote
z rzeczywistym publicznym upstreamem. Oba HTTP 200, łącznie **1,094 s**.
PSG + powyżej 2,5 dało **1,69**, `valid=true`, `errors=[]`; event nadal pre-match.
To pomiar lokalny, nie pomiar Vercel.

Podczas diagnostyki kontener rynku goli zmienił ID z `1211747370299420` na
`1211747370299421`, mimo niezmienionego ID selekcji. Stara para została poprawnie
odrzucona jako `SELECTION_UNAVAILABLE`; po odświeżeniu GET wycena przeszła.
Dlatego użytkownik API powinien pobierać aktualne pary ID, a nie zapisywać ich na stałe.
Kursy i dostępność są zmienne; przykładowe wartości to wyłącznie zapis diagnostyczny.

Nie używano SearchService, get_match, loginu, operacji konta, betslipa ani placement API.
