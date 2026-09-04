# Betclic PL Odds Bridge

Mały, **read-only** mostek JSON do publicznej oferty kursowej Betclic PL. Nie loguje się na konto, nie zna hasła, nie stawia zakładów i nie wykonuje żadnych operacji na saldzie.

Bazuje na nieoficjalnym kliencie `TapsHTS/betclic-api`, który odczytuje publiczny backend `offering.begmedia.com`. Ten projekt wymusza polską regulację/locale (`PL`, `pl`, `betclic.pl`).

## Najprostsze wdrożenie: Vercel

1. Wrzuć te pliki do nowego repozytorium GitHub, np. `betclic-pl-bridge`.
2. Załóż/zaloguj się na Vercel.
3. `Add New` → `Project` → importuj repozytorium.
4. Vercel powinien automatycznie wykryć FastAPI/Python. Nie ustawiaj Build Command ani Output Directory.
5. Kliknij `Deploy`.
6. Po wdrożeniu dostaniesz adres w stylu `https://betclic-pl-bridge-xxxx.vercel.app`.

Vercel Hobby jest wystarczający do prywatnego/hobbystycznego użycia tego mostka.

## Test

Otwórz w przeglądarce:

- `/health`
- `/today?sport=football`
- `/today?sport=tennis`
- `/today?sport=football&competition=Ekstraklasa`
- `/slate?sport=football&limit=10&today=true&odds=true`
- `/slate?sport=tennis&limit=10&today=true&odds=true`
- `/search?q=Lech`
- `/docs` — automatyczna dokumentacja FastAPI

Przykład pełnego adresu:

`https://TWOJ-ADRES.vercel.app/today?sport=football`

## Szybka lista na dziś (v2)

Endpoint `/today` zwraca dzisiejsze wydarzenia według strefy `Europe/Warsaw`.
Football domyślnie wybiera męskie seniorskie rozgrywki z menu, a tenis korzysta
z własnego filtra singla. Obie ścieżki pobierają mecze po aktualnych competition IDs.
Żadna lista `/today` nie wykonuje
`get_match()` ani nie pobiera szczegółowych rynków wydarzeń.

### Football: domyślny scope rozgrywek

`/today?sport=football` (także `scope=curated`) korzysta z SportMenu oraz
`GetMatchesByCompetitionWithNotifications`. ID są odkrywane dynamicznie; reguły
w `football_scope.py` opisują nazwy, kategorie i kraje, a nie stałe competition IDs.
Selekcja odbywa się **przed** zapytaniami o mecze. Nie ma automatycznego przejścia
na globalny feed, również gdy menu zwróci błąd.

Domyślny zakres lig:

| Kraj | Dopuszczone ligi |
| --- | --- |
| Anglia | Premier League, Championship, League One |
| Hiszpania | La Liga, Segunda División |
| Włochy | Serie A, Serie B |
| Niemcy | Bundesliga, 2. Bundesliga, 3. Liga |
| Francja | Ligue 1, Ligue 2 |
| Polska | Ekstraklasa, I Liga, II Liga (także nazwy Betclic 1./2. Liga) |
| Portugalia | Primeira Liga / Liga Betclic, Segunda Liga |
| Holandia | Eredivisie, Eerste Divisie |
| Belgia | Pro League, Challenger Pro League |
| Turcja | Süper Lig, TFF 1. Lig |
| Szkocja | Premiership, Championship |
| Austria | Bundesliga, 2. Liga |
| Szwajcaria | Super League, Challenge League |
| Czechy | Pierwszy i drugi poziom |
| Dania | Superliga, 1st Division |
| Norwegia | Eliteserien, 1. divisjon |
| Szwecja | Allsvenskan, Superettan |
| Grecja | Super League, Super League 2 |

Ponadto: Liga Mistrzów, Liga Europy, Liga Konferencji i ich kwalifikacje,
Superpuchar UEFA, MŚ, EURO, kwalifikacje MŚ/EURO, Liga Narodów UEFA oraz główne
oficjalne turnieje reprezentacyjne: Copa América, Puchar Narodów Afryki,
Gold Cup, Puchar Azji, Liga Narodów CONCACAF i historyczny FIFA Confederations Cup
(tylko z jednoznacznym oznaczeniem FIFA). CAF Confederation Cup oraz niejednoznaczny
„Puchar Konfederacji” są wykluczone.
To wyjątki dla oficjalnych turniejów reprezentacyjnych, nie dopuszczenie lig azjatyckich.
Uwzględniane są główne krajowe puchary powyższych państw, m.in. FA Cup,
Copa del Rey, Coppa Italia, DFB-Pokal, Coupe de France i Puchar Polski.
Osobny filtr `_is_allowed_cup_match` odrzuca jednoznaczne etykiety `preliminary`,
`extra preliminary`, `qualifying` i ich warianty rund w nazwie rozgrywek lub kategorii.
Obsługuje też tekstowe `round_name`/`round`, jeśli dostarczy je adapter upstreamu;
obecny model listy nie udostępnia tych pól ani poziomu ligowego drużyny.
Filtr dotyczy krajowych pucharów, nie kwalifikacji UEFA ani zwykłych lig.

Przy braku informacji o rundzie stosujemy ostrożny fallback: odrzucamy pary
złożone wyłącznie z Ossett United, Pontefract Collieries, Quorn, Shepshed Dynamo,
Flackwell Heath, Hanwell Town, Aveley, Cheshunt, Three Bridges lub Kingstonian
(także z sufiksem FC). Sprawdzamy dokładne nazwy, bez zgadywania poziomu z fragmentów nazwy.
Potwierdzenie udziału drużyny w dozwolonej lidze z już pobranych list meczów
lub jednoznaczna etykieta głównej/późnej fazy (`Third Round Proper`, `Quarter-finals` itd.)
ma pierwszeństwo przed tym fallbackiem. Jawna etykieta rundy wstępnej oznacza odrzucenie.
Samo „round 1” ani data nie określają fazy. Pozostałe nieznane pary pozostają
w odpowiedzi, w tym duży klub przeciwko klubowi non-league.

Filtr wydarzeń odrzuca rezerwy i zespoły młodzieżowe rozpoznane po nazwie drużyny:
sufiksy B/II, U23/U21, Reserves, Jong, Primavera i rezerwy. Oznaczenia są sprawdzane
jako całe słowa; Willem II, B.93 i B 1903 nie są uznawane za rezerwy.
Te filtry dotyczą domyślnego football; debug `scope=all` pozostaje bez zmian.

Rozgrywki kobiece, młodzieżowe (w tym U19/U20/U21), rezerw, akademii, amatorskie,
regionalne i pokazowe są odrzucane. Ligi spoza wymienionych krajów i poziomów
również nie są pobierane. Numery lig są interpretowane według kraju: polska
II Liga jest dozwolona, angielska League Two i polska III Liga już nie.
Nieznana lub niejednoznaczna nazwa nie jest automatycznie dopuszczana.

Cache menu i metadanych trwa `SPORT_MENU_CACHE_TTL` (domyślnie 600 s, minimum 300 s)
w danym procesie aplikacji. `TODAY_FOOTBALL_WORKERS` (domyślnie 4) ogranicza liczbę
równoległych zapytań o mecze. Timeout połączenia wynosi 1 s, odczytu
`TODAY_FOOTBALL_TIMEOUT` (domyślnie 4 s), a limit oczekiwania na całe discovery,
łącznie z menu, to timeout + 1 s. Pomyślne listy rozgrywek korzystają z `CACHE_TTL`.

Odpowiedź zawiera `source: "competitions"`, `selected_competitions`
(obiekty `id`, `name`, `category`, `country_code`, `country_name`),
`selected_competition_ids`, `returned`, `events`, `partial`, `errors`,
`filtered_out` i `generated_at_warsaw`. Lista selected obejmuje również wybrane
rozgrywki zakończone błędem. Błąd pojedynczej rozgrywki trafia do `errors` pod
`competition_id`, a odpowiedź z pozostałymi meczami ma `partial=true`.
`filtered_out` liczy unikalne dzisiejsze wydarzenia usunięte przez filtry rozgrywek,
rezerw lub niskopoziomowych par pucharowych. Odrzucenie przez filtr nie jest błędem upstreamu.
Błąd menu ma `stage: "sport_menu"`. Wyniki są deduplikowane po `match.id`
i ograniczane do dzisiejszej daty w Warszawie. `competition` nadal filtruje nazwę
rozgrywek w wynikach. Parametr `chunk` nie wpływa na domyślne discovery football.

### Globalny feed: tylko debug/fallback dla football

Stare zachowanie jest dostępne jawnie przez
`/today?sport=football&scope=all&chunk=0`. Ten tryb obejmuje również rozgrywki
spoza domyślnego zakresu. Dla football `scope=all` parametr `chunk` to numer fragmentu od 0 (domyślnie `0`, wartości
ujemne są odrzucane).

Football z `scope=all` i pozostałe sporty poza tenisem pobierają najwyżej `TODAY_PAGES_PER_CHUNK`
stron Betclica (domyślnie 4, konfigurowalne przez env). Strona obejmuje 40 wydarzeń: dla football
chunk 0 domyślnie pobiera offsety 0, 40, 80, 120, a chunk 1: 160, 200, 240, 280.
`TODAY_WORKERS` (domyślnie 4) określa
maksymalną liczbę równoległych zapytań wewnątrz chunku. Jeśli jest mniejsza od
rozmiaru chunku, strony są pobierane w kolejnych partiach.

Zachowany `TODAY_MAX_PAGES` (domyślnie 12) stanowi dodatkowy limit na request.
Efektywny rozmiar chunku to mniejsza z wartości limitu stron dla danego sportu i
`TODAY_MAX_PAGES`; ten sam rozmiar wyznacza offset początkowy kolejnego chunku,
więc strony nie są pomijane. Ustawiaj dodatnie wartości tych zmiennych env.
Limit nie ogranicza łącznej liczby stron pobranych przez kolejne requesty.

Po pobraniu partii wyniki są przetwarzane w kolejności offsetów i deduplikowane
w obrębie odpowiedzi. Przyszła data kończy skanowanie (`done=true`), przy zachowaniu
wszystkich dzisiejszych wydarzeń z już pobranej partii, także tych za przyszłym
wydarzeniem na tej samej stronie. Pusta strona lub osiągnięcie znanej liczby
wydarzeń upstream również kończy skanowanie. Filtr `competition` działa niezależnie:
brak pasujących wydarzeń nie oznacza końca danych.

Odpowiedź globalnego feedu zawiera:

- `chunk` — numer bieżącego fragmentu;
- `done` i `next_chunk` — przy końcu danych `true` i `null`; po wykorzystaniu
  chunku bez osiągnięcia końca `false` i `chunk + 1`;
- `pages_scanned` — liczba prób pobrania stron w tym requestcie, także pustych i nieudanych;
- `batches_scanned` — liczba pobranych partii w tym requestcie;
- `filtered_out` — 0 (te sporty nie korzystają z filtra tenisowego);
- `partial` — `true`, gdy pozostają kolejne chunki lub wystąpił błąd strony;
- `errors` — lista błędów z `offset` i `detail`, pusta przy braku błędów.

Błąd strony nie przerywa pobierania pozostałych stron chunku. Przy `errors` można
ponowić ten sam chunk; `done=true` nie wyklucza brakujących wyników z błędnych stron.
Endpoint nie wykonuje `get_match()` ani nie pobiera rynków poszczególnych wydarzeń.

Zapytania stron wykonywane przez `/today` mają timeout połączenia 1 s i odczytu
`TODAY_UPSTREAM_TIMEOUT` (domyślnie 5 s, konfigurowalne przez env). Dodatkowy limit
oczekiwania na partię wynosi timeout odczytu + 1 s (domyślnie 6 s). Wolna strona
trafia do `errors`, a odpowiedź z dostępnymi wynikami ma `partial=true`;
endpoint nie czeka na zakończenie jej wątku. Większa liczba partii w chunku może
wydłużyć łączny czas requestu. Timeout pozostałych endpointów pozostaje bez zmian.

### Tenis: discovery według competition_id

`/today?sport=tennis` pobiera publiczne menu przez
`offering.access.api.SportMenuService/GetSportMenu` i wybiera dozwolone rozgrywki
z gałęzi `tennis`. Rozpoznaje zarówno nazwy rozgrywek, jak i kategorię menu
(np. Challenger). Pomija deble, ITF i pozostałe wykluczone kategorie oraz pozycje
menu dotyczące wyłącznie zwycięzcy turnieju.

Dla wybranych ID wywołuje
`offering.access.api.MatchService/GetMatchesByCompetitionWithNotifications`.
Schemat metody został odczytany z aktualnego frontendu i potwierdzony na US Open.
Odczyt kończy się po pierwszej kompletnej odpowiedzi, bez oczekiwania na dalsze
powiadomienia strumienia. Ta ścieżka nie korzysta z SearchService,
`_fetch_matches_page()` ani `get_matches("tennis")`.

`TODAY_TENNIS_WORKERS` (domyślnie 4) ogranicza równoległe pobieranie rozgrywek.
`SPORT_MENU_CACHE_TTL` (domyślnie 600 s, minimum 300 s) określa czas cache menu
w danym procesie aplikacji. Cache list meczów rozgrywek używa `CACHE_TTL`.
Timeout połączenia to 1 s, a odczytu `TODAY_TENNIS_TIMEOUT` (domyślnie 4 s).
Całe discovery, łącznie z pobraniem menu, ma limit oczekiwania równy timeoutowi
odczytu + 1 s (domyślnie 5 s). Błąd lub timeout jednej rozgrywki nie usuwa
poprawnych wyników pozostałych.

Wyniki są deduplikowane po `match.id`, ograniczane do dzisiejszej daty w Warszawie
i filtrowane przez `_is_allowed_tennis_match()`. Daty z siedmioma cyframi ułamka
sekundy są normalizowane do formatu obsługiwanego przez Python 3.10.
Filtr `competition` nadal działa.

Odpowiedź zawiera `source: "competitions"`, `competition_ids` (wybrane ID, także
te zakończone błędem), `returned`, `events`, `filtered_out`, `partial` i `errors`.
`filtered_out` liczy unikalne dzisiejsze wydarzenia odrzucone przez filtr tenisowy
przed filtrem `competition`. Błąd rozgrywki ma postać
`{ "competition_id": 196, "detail": "..." }`, a błąd menu
`{ "stage": "sport_menu", "detail": "..." }`. `partial=true` sygnalizuje błędy;
można wtedy ponowić request. Przy błędzie menu nie ma zastępczego zapytania search/feed.

Parametr `chunk` jest dla tenisa przestarzały i ignorowany. Odpowiedź nie zawiera
pól paginacji (`chunk`, `next_chunk`, `done`, `pages_scanned`, `batches_scanned`).
`TODAY_TENNIS_PAGES_PER_CHUNK` i `TODAY_TENNIS_SEARCH_TIMEOUT` nie są już używane;
limity stron i `TODAY_WORKERS` nie dotyczą discovery tenisowego.

Tenis dopuszcza tylko rozpoznane kategorie singlowe: Australian Open, Roland Garros /
French Open, Wimbledon, US Open, ATP Masters 1000 / ATP 1000, WTA 1000,
ATP/WTA 500 i 250, ATP/WTA Finals oraz ATP Challenger / Challenger Tour, w tym
kwalifikacje do tych turniejów. Dopasowanie nie rozróżnia wielkości liter.
Odrzucane są deble (także pary zapisane ze znakiem `/`, `&` lub `+` w nazwie meczu), mixed,
ITF i kategorie M15/M25 oraz W15/W25/W35/W50/W75/W100, juniorzy, UTR, college,
exhibition i rozgrywki amatorskie. Wykluczenia w `competition` lub `name` mają
pierwszeństwo przed dopuszczeniem. Nieznane nazwy bez rozpoznanej kategorii są
odrzucane; sama nazwa zawodnika/meczu nie może dopuścić nieznanych rozgrywek.
Przyszłe wydarzenia są pomijane, ale nie przerywają przetwarzania kolejnych meczów.

Diagnostyka aktualnego US Open (wykonuje publiczne zapytania sieciowe):

```powershell
python diagnose_tennis.py --output us-open-diagnostic.json
```

Skrypt kończy się błędem, jeśli nie znajdzie dzisiejszych meczów US Open.
Przed zmianą endpointu potwierdzono 13 takich meczów: 6 dla ID `196` i 7 dla `211`.
Szczegóły schematu i pomiarów: [diagnostics/tennis-discovery.md](diagnostics/tennis-discovery.md).

Przykłady:

- `/today?sport=football`
- `/today?sport=football&competition=Ligue%201`
- `/today?sport=football&scope=all&chunk=0`
- `/today?sport=football&scope=all&chunk=1`
- `/today?sport=tennis`
- `/today?sport=tennis&competition=ATP`
- `/today?sport=football&competition=Ekstraklasa`

Dla football **tylko z `scope=all`** zaczynaj od chunku 0 i pobieraj numer wskazany przez `next_chunk`, aż `done=true`.
Zachowuj te same `sport` i `competition` we wszystkich requestach. Przy łączeniu
odpowiedzi deduplikuj wydarzenia po `id`, ponieważ lista upstream może się zmieniać.

`/slate` pozostaje dostępny. Przy `odds=true` wzbogaca szczegółami maksymalnie 8
wydarzeń (konfigurowalne przez `SLATE_ODDS_LIMIT`) i robi to równolegle, maksymalnie
w 4 wątkach (`SLATE_ODDS_WORKERS`), aby ograniczyć ryzyko timeoutu na Vercelu.

## Dokładne rynki jednego meczu

Każdy event ma `id`. Potem:

`/event/ID`

Domyślnie zawieszone rynki (`suspended=true`) są pomijane. Można je jawnie
dołączyć przez `/event/ID?include_suspended=true`.

Do szybkiego pobrania tylko najważniejszych aktywnych rynków użyj:

`/event/ID?compact=true`

Tryb compact odrzuca ciężkie rynki (m.in. strzelców, asyst, dokładnego wyniku i
duże listy wyborów). Zwykły `/event/ID` nadal zwraca pełny zestaw aktywnych
rynków z domyślnej paczki Betclic.

Dla piłki można pobrać/połączyć wszystkie znane kategorie:

`/event/ID/football-all`

Albo konkretną kategorię:

`/event/ID?category=goals`

Znane kategorie piłkarskie: `result`, `goals`, `score_handicap`, `top`, `goalscorers`, `props`.

## Dynamiczne MyCombi / Bet Builder (read-only)

`GET /event/{event_id}/mycombi` pobiera katalog z publicznego
`MyCombiService/GetMyCombiWithNotifications`, z pustą listą kombinacji.
Odpowiedź zawiera `event_id`, `name`, `date`, `is_live`, `available`, `markets`,
`source: "mycombi"`, `errors` i `generated_at_warsaw`.
Każdy market ma `market_id`, nazwę, `is_betbuilder_eligible` i listę selekcji
z `selection_id`, `market_id`, nazwą, kursem jednostkowym, `status`
oraz `is_betbuilder_compatible`. Nie każda para selekcji z katalogu jest łączalna.

`POST /event/{event_id}/mycombi/quote` jest wyłącznie zapytaniem o kurs kombinacji:

```json
{
  "selections": [
    {"market_id": "1211746605895682", "selection_id": "1211746605891589"},
    {"market_id": "1211747370299421", "selection_id": "1211747370295431"}
  ]
}
```

Przykładowy wynik diagnostyczny dla PSG–Monaco:

```json
{
  "valid": true,
  "odds": 1.69,
  "selections": [
    {"market_id": "1211746605895682", "selection_id": "1211746605891589"},
    {"market_id": "1211747370299421", "selection_id": "1211747370295431"}
  ],
  "errors": [],
  "generated_at_warsaw": "2026-09-04T20:03:43.735262+02:00"
}
```

ID i kursy powyżej są historycznym przykładem. **Zawsze pobierz świeże pary ID
z GET metadanych.** Kontener marketu może zmienić ID, nawet jeśli selection ID
pozostaje ten sam. Nie używaj `betslip_market_id`. API akceptuje dodatnie ID int64
jako liczby lub ciągi cyfr; w odpowiedziach ID są stringami, żeby uniknąć utraty
precyzji w JavaScript. Request wymaga 2–20 różnych selekcji (20 to limit bridge,
nie deklaracja limitu Betclica). Dodatkowe pola, np. stake/account_id, dają HTTP 422.

Quote najpierw odczytuje aktualny katalog i weryfikuje pary ID, status ONLINE
oraz eligibility/compatibility, a następnie wysyła je do kalkulatora Betclica.
Nie mnoży kursów samodzielnie. `valid=false`, `odds=null` i `errors` oznaczają np.
`NOT_COMBINABLE`, `SELECTION_UNAVAILABLE` lub `UNAVAILABLE` (HTTP 200).
Po `SELECTION_UNAVAILABLE` odśwież katalog. Awaria upstreamu daje HTTP 502,
timeout HTTP 504; POST nadal zwraca `valid=false` i `errors`.

`MYCOMBI_UPSTREAM_TIMEOUT` domyślnie wynosi 4 s na odczyt (connect timeout 1 s).
Quote wykonuje najwyżej dwa zapytania odczytowe i nie jest cache'owany
(`Cache-Control: no-store`). Strumień gRPC-web zamykamy po pierwszym snapshotcie.
Żadne cookies ani nagłówki konta klienta bridge nie są przekazywane upstreamowi.
Nie ma loginu, betslipa, stawki, placement ani operacji na saldzie.

Powtarzalna diagnostyka poza endpointami:

```powershell
python diagnose_mycombi.py EVENT_ID --output metadata.json
python diagnose_mycombi.py EVENT_ID --selection MARKET_ID:SELECTION_ID --selection MARKET_ID:SELECTION_ID --output quote.json
```

Skrypt zwraca exit code 2 dla odrzuconej kombinacji.
Schemat, źródła bundli i potwierdzenie pre-match przed integracją:
[diagnostics/mycombi-discovery.md](diagnostics/mycombi-discovery.md).
Dotychczasowe `/event`, `compact=true` i discovery football/tennis pozostają bez zmian.

## Lokalny test na Windows

Jeśli masz Pythona 3.10+:

1. uruchom `run_local.bat`,
2. wejdź na `http://127.0.0.1:8000/docs`.

Do użycia przez ChatGPT potrzebny jest jednak publiczny HTTPS URL, więc lokalny tryb służy głównie do testu.

Testy automatyczne (w aktywnym środowisku Python):

```powershell
python -m pip install -r requirements.txt pytest httpx tzdata
python -m pytest -q
```

## Co potem

Gdy publiczny URL działa, wklej go do rozmowy. Wtedy można używać go do codziennego workflow:

1. pobranie dzisiejszego slatu piłki/tenisa,
2. wybór kilku kandydatów,
3. pobranie szczegółowych rynków tylko tych wydarzeń,
4. osobny research formy, składów, kontuzji i kontekstu,
5. złożenie kuponu na podstawie **bieżących kursów Betclic PL**.

## Ważne

To nieoficjalne rozwiązanie. Backend lub format protobuf może się kiedyś zmienić i wtedy klient będzie wymagał aktualizacji. Używaj rozsądnej liczby zapytań; endpoint `/slate` ma krótki cache i celowo ogranicza liczbę eventów na jedno wywołanie.
