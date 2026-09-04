# Football curated discovery: weryfikacja 2026-09-04

Reguły zostały dopasowane do aktualnego polskiego SportMenu, pobranego tą samą
publiczną metodą gRPC-web co dla tenisa. Rozszerzony parser zachowuje kraj z
CountryItem (code=1, name=2, competitions=3) oraz country_code=4 z CompetitionItem.
Pozwala to odróżnić np. angielską Premier League od lig o tej samej nazwie w innych krajach.

Odczyt menu zawierał **269** football competitions. Reguły wybrały **56**, m.in.:

- `Ligue 1`, `Premier League`, `Ekstraklasa`;
- `Anglia League 1`, ale nie `Anglia League 2`;
- `Betclic 1. Liga` i `Betclic 2. Liga`, ale nie `Betclic 3. liga Gr.1`;
- `Niemcy Bundesliga 2` i `Niemcy 3. liga`;
- `Anglia FA Cup`, `Francja Puchar`, `Puchar Polski`;
- `Liga Mistrzów`, `Liga Europy`, `Liga Konferencji`, `UEFA Liga Narodów`, `MŚ`.

Próba lokalnego endpointu z aktualnym upstreamem, **2026-09-04 13:44:21 Europe/Warsaw**:

```python
app.today_events(sport="football", competition=None)
```

Wynik: `source="competitions"`, **62 dzisiejsze wydarzenia**, **2,047 s**,
`partial=false`, `errors=[]`. W pierwszej odpowiedzi, bez chunkowania, znalazły się:

- Lyon – Auxerre, Ligue 1, 19:00 Europe/Warsaw;
- Paris Saint-Germain – Monaco, Ligue 1, 21:05 Europe/Warsaw.

To pomiar lokalnego kodu z publicznym upstreamem, nie benchmark wdrożenia Vercel.
ID rozgrywek są odczytywane z menu i nie są zapisane w regułach selekcji.
Metadane menu mają domyślnie 10-minutowy cache na proces aplikacji.
Stara ścieżka globalna jest dostępna wyłącznie po jawnym `scope=all` dla football.
