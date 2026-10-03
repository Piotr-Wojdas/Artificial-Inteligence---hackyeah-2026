# Solary

Wpisujesz adres, a moduł:

1. znajduje budynek (OpenStreetMap) i jego dach (Google Solar API),
2. rozmieszcza na dachu panele dla wybranej mocy i rysuje je na zdjęciu lotniczym,
3. szacuje, ile prądu te panele wyprodukują w Polsce: rocznie i w każdym miesiącu.

W środku jest biblioteka w Pythonie, polecenie w terminalu, serwer HTTP dla frontendu i gotowa strona demo.
Folder jest samodzielny: skopiuj jego zawartość do pustego repozytorium.

## Szybki start

Potrzebujesz Pythona 3.11+ i [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env        # wpisz klucz: GOOGLE_MAPS_API_KEY=...
uv sync                     # instaluje zależności
uv run python -m solary "Mariacka 1, Katowice" --kwp 6     # wynik w terminalu + 3 obrazki w data/roof/
uv run python -m solary.api                                # strona demo: http://127.0.0.1:8000
uv run pytest                                              # 133 testy, bez internetu
```

Klucz: w [Google Cloud Console](https://console.cloud.google.com/) włącz **Solar API** w projekcie z podpiętymi
płatnościami i utwórz klucz API. Zapytania są płatne według cennika Google Maps Platform, więc sprawdź go przed
wdrożeniem. Nowy budynek to jedno zapytanie o dach (dwa, gdy nie ma zdjęć wysokiej jakości) i jedno o warstwy mapy plus
trzy pobrania plików. Kolejne zapytania o ten sam budynek idą z dysku przez 30 dni.

Klucza nie wpisuj do kodu ani do repozytorium. Plik `.env` jest w `.gitignore`.

## Co jest w folderze

| Ścieżka | Zawartość |
|---|---|
| `solary/config.py` | wszystkie założenia w jednym miejscu (`Config`) |
| `solary/geocode.py` | adres → współrzędne (OpenStreetMap Nominatim) |
| `solary/solar_api.py` | klient Google Solar API, cache, usuwanie danych po 30 dniach |
| `solary/panels.py` | połacie dachu i wybór paneli dla zadanej mocy |
| `solary/production.py` | szacunek produkcji: rocznie, miesięcznie, PVGIS |
| `solary/render.py` | obrazki PNG: „czy to Twój dom?” i układ paneli |
| `solary/service.py` | `analyze()`: cała funkcja w jednym wywołaniu |
| `solary/__main__.py` | polecenie `python -m solary` |
| `solary/api.py` | serwer FastAPI + strona demo |
| `solary/env.py`, `solary/errors.py` | wczytywanie `.env`, klasa błędów |
| `web/index.html` | strona demo (czysty HTML i JavaScript, bez budowania) |
| `tests/` | testy bez dostępu do sieci |

## Jak to działa

1. **Adres.** Nominatim szuka adresu tylko w Polsce. Przedrostek „ul.” jest usuwany, bo z nim Nominatim nic nie
   znajduje. Jeśli tekst pasuje do kilku miejsc, pierwsze idzie do analizy, a pozostałe wracają w polu
   `alternatives`. Gdy udało się dopasować tylko ulicę, bez numeru domu, wynik ma ostrzeżenie `street_only`.
2. **Dach.** Google Solar API zwraca dla najbliższego budynku połacie dachu (nachylenie, kierunek, powierzchnia)
   i listę wszystkich paneli, które się mieszczą. Każdy panel ma położenie i roczną energię, w której są już
   cienie drzew, sąsiednich budynków i samego dachu.
3. **Układ paneli.** Dla N paneli bierzemy pierwsze N z listy Google. To kolejność ich algorytmu układania,
   więc panele tworzą zwarte pola. Alternatywa `panel_order="yield"` bierze panele ściśle od najlepszego:
   daje 0–1% więcej energii, ale panele są porozrzucane.
4. **Produkcja roczna.** Suma rocznej energii DC wybranych paneli × (1 − straty systemu 14%).
5. **Miesiące.** Roczną produkcję każdej połaci dzielimy profilem miesięcznym z PVGIS, policzonym dla
   współrzędnych tego dachu oraz nachylenia i kierunku tej połaci. Ten sam wynik PVGIS daje niezależny szacunek
   bez lokalnych cieni. Pokazujemy go obok jako kontrolę (`pvgis_kwh_per_kwp`).
6. **Obrazki.** Z warstw mapy Google powstają trzy pliki PNG: budynek z czerwoną ramką i kropką w miejscu
   adresu, wszystkie panele oraz wybrane panele na mapie nasłonecznienia dachu.

Gdy Google nie ma danych o dachu, wynik ma `"roof_available": false` i szacunek z samego PVGIS dla podanej mocy,
nachylenia i kierunku (domyślnie 35° na południe), bez rozmieszczenia paneli.

### Przykładowe wyniki (3.10.2026)

| Adres | Układ | Produkcja | Z 1 kWp | PVGIS dla tej orientacji |
|---|---|---|---|---|
| Mariacka 1, Katowice | 15 paneli, 6,0 kWp | 5 729 kWh/rok | 955 kWh | 921 kWh |
| Świdnicka 10, Wrocław | 15 paneli, 6,0 kWp | 5 457 kWh/rok | 910 kWh | 879 kWh |
| Rynek Wielki 1, Zamość | brak danych dachu, 5 kWp, 35° płd. | 5 267 kWh/rok | 1 053 kWh | to jest PVGIS |

## Założenia

Wszystkie są w `solary/config.py`. API zwraca je w polu `assumptions`.

| Pole | Wartość | Znaczenie |
|---|---|---|
| `system_loss_pct` | 14 | straty od paneli do gniazdka: falownik, kable, zabrudzenie (domyślna wartość PVGIS) |
| `panel_watts` | brak, czyli 400 W | panel, który układa Google: 400 W, 1,879 × 1,045 m. Inna moc przelicza moc i energię proporcjonalnie; ma sens tylko dla paneli o podobnych wymiarach |
| `panel_order` | `google` | kolejność wybierania paneli (`google` albo `yield`) |
| `pvgis_mounting` | `building` | PVGIS: panele na dachu, a nie na wolnostojącym stelażu |
| `default_kwp` | 6 | moc pokazywana, gdy nikt jej nie wybrał |
| `sizes_kwp` | 3, 5, 6, 8, 10, 15, 20, 30, 50 | moce w tabeli porównawczej |
| `generic_tilt_deg`, `generic_azimuth_deg` | 35°, 180° | orientacja w szacunku bez danych dachu |
| `country_codes` | `pl` | kraj wyszukiwania adresów (`None` = cały świat) |
| `qualities` | HIGH, MEDIUM | akceptowana jakość zdjęć Google |
| `warn_distance_m` | 10 | powyżej tej odległości budynku od adresu pojawia się ostrzeżenie |
| `cache_days` | 30 | po tylu dniach dane Google są usuwane z dysku |

Moc w kWp jest zaokrąglana do całych paneli: 6 kWp to 15 paneli, a 5 kWp to 13 paneli, czyli 5,2 kWp.

## Polecenie w terminalu

```bash
uv run python -m solary "Mariacka 1, Katowice" --kwp 6
uv run python -m solary --lat 50.25740 --lon 19.02476 --panels 20 --no-images
uv run python -m solary "Rynek Wielki 1, Zamość" --kwp 5 --tilt 30 --azimuth 135
uv run python -m solary "Mariacka 1, Katowice" --json > wynik.json
```

| Opcja | Domyślnie | Opis |
|---|---|---|
| `address` | – | adres w cudzysłowie, np. `"Mariacka 1, Katowice"` |
| `--lat`, `--lon` | – | współrzędne dachu zamiast adresu |
| `--kwp` | 6 | moc instalacji |
| `--panels` | – | liczba paneli zamiast mocy |
| `--panel-watts` | 400 | moc jednego panelu |
| `--order` | `google` | `google` albo `yield` |
| `--tilt`, `--azimuth` | 35, 180 | tylko dla szacunku bez danych dachu; azymut kompasowy, 180 = południe |
| `--no-images` | – | bez pobierania warstw mapy i bez obrazków |
| `--data` | `./data` | folder cache |
| `--json` | – | pełny wynik jako JSON |

## Użycie z Pythona

```python
from solary import analyze

res = analyze(address="Mariacka 1, Katowice", kwp=6)
if res["roof_available"]:
    print(res["selected"]["kwh_year"], res["selected"]["monthly_kwh"])
else:
    print(res["generic"]["kwh_year"])
```

Błędy przeznaczone dla użytkownika dziedziczą po `solary.errors.SolaryError`
(`AddressNotFound`, `GeocodingError`, `MissingApiKey`, `SolarApiError`).

## Serwer HTTP

```bash
uv run python -m solary.api --host 127.0.0.1 --port 8000
```

| Adres | Co zwraca |
|---|---|
| `GET /` | strona demo |
| `GET /docs` | interaktywna dokumentacja |
| `GET /api/health` | `{"ok": true, "google_key": true}` |
| `GET /api/roof?address=…&kwp=6` | analiza dachu pod adresem |
| `GET /api/roof?lat=…&lon=…&panels=15` | to samo dla współrzędnych i liczby paneli |
| `GET /api/roof/image/{nazwa}` | obrazek PNG z pola `images` |

Parametry `/api/roof`: `address` albo `lat` + `lon`; `kwp` albo `panels`; `images=false` wyłącza obrazki;
`tilt` i `azimuth` działają tylko w szacunku bez danych dachu.

Kody błędów: 400 złe parametry, 404 nie znaleziono adresu, 502 nie odpowiada Google albo Nominatim, 503 brak
klucza Google.

### Odpowiedź `/api/roof`

Zawsze:

| Pole | Opis |
|---|---|
| `roof_available` | `true`, gdy Google ma dach i mieści się na nim choć jeden panel |
| `query` | `address`, `lat`, `lon` punktu, którego szukano |
| `address_label` | pełny adres dopasowany przez OpenStreetMap |
| `house_level` | `false`, gdy dopasowano tylko ulicę |
| `alternatives` | inne miejsca pasujące do tekstu: `lat`, `lon`, `label`, `house_level` |
| `warnings` | `street_only`, `far_from_address`, `monthly_fallback`, `images_unavailable` |
| `sizes` | tabela mocy: `kwp`, `panels`, `kwh_year`, `kwh_per_kwp` |
| `images` | adresy obrazków: `confirm`, `all`, `selected` |
| `assumptions`, `attribution` | założenia i tekst o źródłach danych |

Gdy `roof_available` jest `true`:

| Pole | Opis |
|---|---|
| `building` | `lat`, `lon`, `distance_m` od adresu, `roof_area_m2`, `max_panels`, `max_kwp`, `imagery_quality`, `imagery_date` |
| `panel` | `watts`, `google_watts`, `height_m`, `width_m` |
| `segments` | połacie: `segment`, `facing` (N, NE, …), `pitch_deg`, `azimuth_deg`, `area_m2`, `max_panels`, `kwh_dc_per_panel` |
| `selected` | wybrany układ: `panels`, `kwp`, `kwh_year`, `kwh_per_kwp`, `monthly_kwh` (12 liczb), `monthly_source`, `pvgis_kwh_year`, `per_segment` |
| `selected.per_segment` | `segment`, `facing`, `pitch_deg`, `azimuth_deg`, `panels`, `kwp`, `kwh_year`, `kwh_per_kwp`, `pvgis_kwh_per_kwp`, `vs_pvgis` |

Gdy `roof_available` jest `false`:

| Pole | Opis |
|---|---|
| `reason` | `no_roof_data` albo `no_panels_fit` |
| `generic` | `kwp`, `kwh_year`, `kwh_per_kwp`, `monthly_kwh`, `monthly_source`, `tilt_deg`, `azimuth_deg`, `facing` |

`monthly_source` ma wartość `pvgis` albo `typical_poland`. Ta druga oznacza, że PVGIS nie odpowiedział i miesiące
pochodzą z typowego profilu dla środkowej Polski.

### Własny frontend

Strona demo woła API pod tym samym adresem. Jeśli Twój frontend działa gdzie indziej, ustaw w `.env`
`SOLARY_CORS_ORIGINS=http://localhost:5173` albo przekieruj `/api` przez proxy serwera deweloperskiego.
Suwak liczby paneli na stronie demo wysyła po prostu to samo zapytanie z innym `panels`.

Serwer obsługuje jedną analizę naraz, bo pliki cache nie są zapisywane równolegle. Do demo i małego ruchu to
wystarcza.

## Ograniczenia

- **Zasięg.** Google Solar API zna w Polsce głównie większe miasta. W testach z 3.10.2026 dane były dla
  Warszawy, Wrocławia, Katowic i Sopotu, a nie było ich dla Zamościa, Suwałk i Białowieży.
- **Wiek zdjęć.** Zdjęcia bywają stare (dla Katowic z 2017 roku). Nowego budynku albo przebudowanego dachu może
  na nich nie być. Datę zdjęcia zwraca pole `imagery_date`.
- **Zły budynek.** Google zwraca budynek najbliższy punktowi. Dlatego jest obrazek „czy to Twój dom?”,
  ostrzeżenia i lista innych dopasowań.
- **Dachy płaskie.** Google kładzie panele na płasko, zgodnie z połacią. W praktyce na płaskim dachu stawia się
  stelaże pod kątem, więc realny układ i uzysk będą inne.
- **Cienie.** Są wliczone w sumę roczną, ale podział na miesiące ich nie zna.
- **To szacunek.** Wynik opiera się na danych wieloletnich. Pojedynczy rok może się różnić o kilka procent,
  a strat 14% nikt nie zmierzył dla konkretnej instalacji.
- **Brak finansów.** Moduł liczy energię, nie ceny, zwrot ani rozliczenia z siecią.

## Źródła danych i ich warunki

- **Google Solar API.** Dane wolno przechowywać najwyżej 30 dni: moduł usuwa starsze pliki z `data/roof/` przy
  każdej analizie i przy starcie serwera. Przy wynikach trzeba pokazać, że pochodzą od Google. Obrazki powstają
  z warstw Solar API, a nie ze zrzutów ekranu Google Maps.
- **OpenStreetMap Nominatim.** Publiczny serwer pozwala na jedno zapytanie na sekundę (moduł tego pilnuje)
  i wymaga nagłówka User-Agent z nazwą aplikacji: ustaw `SOLARY_USER_AGENT` w `.env`. Przy wynikach trzeba
  podać „© autorzy OpenStreetMap”. Przy większym ruchu postaw własny serwer albo użyj płatnego geokodera.
- **PVGIS** (Komisja Europejska, JRC). Bezpłatny, bez klucza. Wyniki są cache'owane w `data/pvgis_cache.json`.

## Testy

`uv run pytest` uruchamia 133 testy. Nie łączą się z siecią: odpowiedzi Google, PVGIS i Nominatim są podstawione,
a rysowanie obrazków działa na małych, sztucznych plikach GeoTIFF. Sprawdzają wybór paneli i zaokrąglanie mocy,
przeliczenia produkcji, zamianę kierunków między Google i PVGIS, geometrię paneli, cache i jego wygasanie,
to, że klucz nie trafia do komunikatów błędów, oraz API razem z ochroną ścieżek do obrazków.

Test na żywych danych to samo polecenie z „Szybkiego startu”.
