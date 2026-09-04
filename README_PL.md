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

Endpoint `/today` zwraca jeden fragment (chunk) dzisiejszych wydarzeń według strefy
`Europe/Warsaw`. Wszystkie sporty korzystają z tego samego mechanizmu chunkowania;
tenis ma dodatkowo filtr profesjonalnego singla i mniejszy domyślny chunk.
Parametr `chunk` to numer fragmentu od 0 (domyślnie `0`, wartości ujemne są odrzucane).

Football i pozostałe sporty pobierają najwyżej `TODAY_PAGES_PER_CHUNK` stron
Betclica (domyślnie 4), a tenis `TODAY_TENNIS_PAGES_PER_CHUNK` (domyślnie 2).
Oba limity są konfigurowalne przez env. Strona obejmuje 40 wydarzeń: dla football
chunk 0 domyślnie pobiera offsety 0, 40, 80, 120, a chunk 1: 160, 200, 240, 280.
Dla tenisa chunk 0 pobiera 0, 40, a chunk 1: 80, 120. `TODAY_WORKERS` (domyślnie 4) określa
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

Odpowiedź zawiera:

- `chunk` — numer bieżącego fragmentu;
- `done` i `next_chunk` — przy końcu danych `true` i `null`; po wykorzystaniu
  chunku bez osiągnięcia końca `false` i `chunk + 1`;
- `pages_scanned` — liczba prób pobrania stron w tym requestcie, także pustych i nieudanych;
- `batches_scanned` — liczba pobranych partii w tym requestcie;
- `filtered_out` — liczba unikalnych dzisiejszych wydarzeń odrzuconych przez filtr
  tenisowy, przed filtrem `competition`; dla innych sportów 0;
- `partial` — `true`, gdy pozostają kolejne chunki lub wystąpił błąd strony;
- `errors` — lista błędów z `offset` i `detail`, pusta przy braku błędów.

Błąd strony nie przerywa pobierania pozostałych stron chunku. Przy `errors` można
ponowić ten sam chunk; `done=true` nie wyklucza brakujących wyników z błędnych stron.
Endpoint nie wykonuje `get_match()` ani nie pobiera rynków poszczególnych wydarzeń.

Zapytania listy wykonywane przez `/today` mają timeout połączenia 1 s i odczytu
`TODAY_UPSTREAM_TIMEOUT` (domyślnie 5 s, konfigurowalne przez env). Dodatkowy limit
oczekiwania na partię wynosi timeout odczytu + 1 s (domyślnie 6 s). Wolna strona
trafia do `errors`, a odpowiedź z dostępnymi wynikami ma `partial=true`;
endpoint nie czeka na zakończenie jej wątku. Większa liczba partii w chunku może
wydłużyć łączny czas requestu. Timeout pozostałych endpointów pozostaje bez zmian.

Tenis dopuszcza tylko rozpoznane kategorie singlowe: Australian Open, Roland Garros /
French Open, Wimbledon, US Open, ATP Masters 1000 / ATP 1000, WTA 1000,
ATP/WTA 500 i 250, ATP/WTA Finals oraz ATP Challenger / Challenger Tour, w tym
kwalifikacje do tych turniejów. Dopasowanie nie rozróżnia wielkości liter.
Odrzucane są deble (także pary zapisane ze znakiem `/`, `&` lub `+` w nazwie meczu), mixed,
ITF i kategorie M15/M25 oraz W15/W25/W35/W50/W75/W100, juniorzy, UTR, college,
exhibition i rozgrywki amatorskie. Wykluczenia w `competition` lub `name` mają
pierwszeństwo przed dopuszczeniem. Nieznane nazwy bez rozpoznanej kategorii są
odrzucane; sama nazwa zawodnika/meczu nie może dopuścić nieznanych rozgrywek.
Filtr nie wpływa na `done`: przyszłe wydarzenie nawet z wykluczonej kategorii
kończy skanowanie, a pusta lista po odfiltrowaniu dzisiejszych meczów nie oznacza końca.

Przykłady:

- `/today?sport=football&chunk=0`
- `/today?sport=football&chunk=1`
- `/today?sport=tennis&chunk=0`
- `/today?sport=tennis&chunk=1`
- `/today?sport=football&competition=Ekstraklasa&chunk=0`

Zaczynaj od chunku 0 i pobieraj numer wskazany przez `next_chunk`, aż `done=true`.
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
