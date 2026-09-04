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

Endpoint `/today` pobiera wszystkie strony listy dyscypliny i zwraca wyłącznie
dzisiejsze wydarzenia. Nie pobiera osobno szczegółów ani rynków każdego meczu,
dzięki czemu jest preferowanym, szybkim punktem startowym:

- `/today?sport=football`
- `/today?sport=tennis`
- `/today?sport=football&competition=Ekstraklasa` — opcjonalny filtr nazwy rozgrywek

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

## Co potem

Gdy publiczny URL działa, wklej go do rozmowy. Wtedy można używać go do codziennego workflow:

1. pobranie dzisiejszego slatu piłki/tenisa,
2. wybór kilku kandydatów,
3. pobranie szczegółowych rynków tylko tych wydarzeń,
4. osobny research formy, składów, kontuzji i kontekstu,
5. złożenie kuponu na podstawie **bieżących kursów Betclic PL**.

## Ważne

To nieoficjalne rozwiązanie. Backend lub format protobuf może się kiedyś zmienić i wtedy klient będzie wymagał aktualizacji. Używaj rozsądnej liczby zapytań; endpoint `/slate` ma krótki cache i celowo ogranicza liczbę eventów na jedno wywołanie.
