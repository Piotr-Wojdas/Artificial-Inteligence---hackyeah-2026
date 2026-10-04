# Solari

Solari odpowiada na pytanie „czy i jak opłaca mi się fotowoltaika na moim dachu?”. Wpisujesz adres, a aplikacja
znajduje dach, rozkłada na nim panele, liczy produkcję prądu i opłacalność oraz pokazuje, jak domowym magazynem
energii sterowałby agent AI. Projekt powstał na HackYeah 2026.

## Jak uruchomić

Wszystkie polecenia wpisuj w głównym folderze repozytorium, czyli tam, gdzie leży ten plik.

### 1. Co trzeba mieć

- **[uv](https://docs.astral.sh/uv/getting-started/installation/).** Instaluje zależności i w razie potrzeby samego
  Pythona (projekt wymaga wersji 3.12 lub nowszej).
  - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  - Linux i macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Klucz Google Maps Platform z włączonym Solar API.** W [Google Cloud Console](https://console.cloud.google.com/)
  włącz **Solar API** w projekcie z podpiętymi płatnościami i utwórz klucz API. Bez klucza działają testy
  i polecenie magazynu energii dla podanych współrzędnych, ale nie analiza dachu.

### 2. Instalacja

```bash
git clone https://github.com/Piotr-Wojdas/Artificial-Inteligence---hackyeah-2026.git
cd Artificial-Inteligence---hackyeah-2026
cp .env.example .env        # w cmd.exe: copy .env.example .env
uv sync                     # tworzy .venv i instaluje zależności (razem z pytest)
```

Otwórz plik `.env` i wpisz klucz: `GOOGLE_MAPS_API_KEY=...`. Klucza nie wpisuj do kodu ani do repozytorium;
plik `.env` jest w `.gitignore`.

### 3. Strona

```bash
uv run python -m solary.api
```

Otwórz http://127.0.0.1:8000, wpisz adres (na przykład „Mariacka 1, Katowice”) i kliknij „Analizuj dach”. Serwer
zatrzymasz przez Ctrl+C. Interaktywna dokumentacja API jest pod http://127.0.0.1:8000/docs.

- Pierwsza analiza nowego adresu trwa kilkanaście sekund, kolejne idą z dysku.
- Pierwsze przeliczenie magazynu energii trwa od pół minuty do około dwóch, bo pobiera ceny i pogodę z całego roku.
- Inny port albo dostęp z sieci lokalnej: `uv run python -m solary.api --host 0.0.0.0 --port 8001`.

### 4. Terminal

```bash
uv run python -m solary "Mariacka 1, Katowice" --kwp 6                  # dach: wynik w terminalu i 3 obrazki w data/roof/
uv run python -m solary "Mariacka 1, Katowice" --panels 15 --layout pro # panele rozmieszcza nasz algorytm
uv run python -m solary.battery plan "Mariacka 1, Katowice" --battery-kwh 10   # magazyn: rachunek za rok i plan do końca jutra
uv run python -m solary.battery plan --lat 50.26 --lon 19.02 --kwp 6    # magazyn bez analizy dachu (nie wymaga klucza Google)
```

Wszystkie opcje opisują sekcje [Polecenie w terminalu](#polecenie-w-terminalu) i
[Magazyn energii sterowany przez AI](#magazyn-energii-sterowany-przez-ai).

### 5. Testy

```bash
uv run pytest
```

188 testów, bez internetu i bez klucza Google. Jeden jest pomijany, dopóki nie zainstalujesz grupy `rl` (punkt 7).

### 6. Docker, Render i publiczny adres

```bash
docker build -t solari .
docker run --rm -p 8000:8000 -e GOOGLE_MAPS_API_KEY=twoj_klucz solari      # strona: http://127.0.0.1:8000
```

- **Render.com.** `render.yaml` opisuje usługę `solari` budowaną z `Dockerfile` (plan darmowy, region Frankfurt,
  kontrola zdrowia pod `/api/health`). Utwórz w Render nowy Blueprint z tego repozytorium i wpisz
  `GOOGLE_MAPS_API_KEY` w panelu usługi.
- **Publiczny adres dla lokalnego serwera.** Uruchom serwer (punkt 3), a potem `run_demo_tunnel.bat`. Skrypt
  wymaga programu [cloudflared](https://github.com/cloudflare/cloudflared/releases)
  (`cloudflared.exe` w tym folderze albo w PATH) i wypisuje tymczasowy adres HTTPS prowadzący do portu 8000.

### 7. Nauka i ocena agenta (opcjonalnie)

Nauczony agent jest w repozytorium (`solary/battery/policy.npz`), więc do uruchomienia aplikacji ten krok nie jest
potrzebny.

```bash
uv run python -m solary.battery evaluate     # wszystkie strategie na roku testowym -> solary/battery/evaluation.json
uv sync --group rl                           # torch, gymnasium i stable-baselines3: tylko do nauki
uv run python -m solary.battery train        # nauka agenta od nowa -> solary/battery/policy.npz
```

### Gdy coś nie działa

| Objaw | Co zrobić |
|---|---|
| `ModuleNotFoundError: No module named 'solary'` | uruchamiasz z innego folderu: przejdź do głównego folderu repozytorium |
| strona pokazuje „Brak klucza Google Solar API”, API zwraca 503 | brakuje `GOOGLE_MAPS_API_KEY` w `.env` albo serwer startował przed jego wpisaniem: uruchom go ponownie |
| „Nie znaleziono takiego adresu” (404) | dopisz miasto i numer domu; wyszukiwanie obejmuje tylko Polskę |
| „Google Solar API nie posiada danych” | Google zna w Polsce głównie większe miasta; aplikacja pokazuje wtedy szacunek z PVGIS bez układu paneli |
| błąd 502 | nie odpowiada usługa zewnętrzna (Google, OpenStreetMap, PSE, NASA POWER albo Open-Meteo): spróbuj za chwilę |
| port 8000 jest zajęty | dodaj `--port 8001` |

## Co robi Solari

1. Znajduje budynek (OpenStreetMap) i jego dach (Google Solar API).
2. Rozmieszcza na dachu panele dla wybranej mocy: według Google albo własnym algorytmem, który zostawia odstęp
   od krawędzi i omija kominy, i rysuje je na zdjęciu lotniczym.
3. Szacuje, ile prądu te panele wyprodukują: rocznie i w każdym miesiącu.
4. Liczy orientacyjną opłacalność: koszt instalacji, roczne oszczędności i okres zwrotu.
5. Steruje domowym magazynem energii: agent nauczony metodą uczenia ze wzmocnieniem co 15 minut decyduje, kiedy
   ładować baterię, kiedy zasilać z niej dom, a kiedy sprzedawać prąd, patrząc na ceny RCE i prognozę pogody.

Z tych samych funkcji korzystają trzy wejścia: strona w przeglądarce, polecenia w terminalu i biblioteka w Pythonie.

**Koszt zapytań do Google.** Zapytania są płatne według cennika Google Maps Platform, więc sprawdź go przed
wdrożeniem. Nowy budynek to jedno zapytanie o dach (dwa, gdy nie ma zdjęć wysokiej jakości) i jedno o warstwy mapy
plus trzy pobrania plików (cztery z własnym układem paneli, który potrzebuje jeszcze mapy wysokości). Kolejne
zapytania o ten sam budynek idą z dysku przez 30 dni.

## Jak zbudowane jest repozytorium

Analiza dachu to jeden ciąg kroków, który spina funkcja `analyze()` w `solary/service.py`:

```text
adres -> geocode.py -> solar_api.py -> layout.py* -> panels.py -> production.py -> economics.py
         (adres na     (dach i warstwy  (własny       (wybór N     (kWh rocznie     (koszt, zwrot)
          współrzędne)  mapy z Google)   układ)        paneli)      i miesięcznie)
                             |
                             +-> render.py (obrazki PNG dachu)

* tylko dla layout="own" i "pro"; przy "google" lista paneli pochodzi wprost z Google.
```

`analyze()` wołają `solary/__main__.py` (terminal) i `solary/api.py` (serwer HTTP), a serwer podaje stronę
`web/index.html`. Magazyn energii to osobny pakiet `solary/battery/`: bierze z analizy dachu moc, nachylenie
i kierunek paneli, a ceny i pogodę pobiera sam.

| Ścieżka | Zawartość |
|---|---|
| `solary/config.py` | wszystkie założenia w jednym miejscu (`Config`) |
| `solary/geocode.py` | adres → współrzędne (OpenStreetMap Nominatim) |
| `solary/solar_api.py` | klient Google Solar API, cache, usuwanie danych po 30 dniach |
| `solary/panels.py` | połacie dachu i wybór paneli dla zadanej mocy |
| `solary/layout.py` | własne algorytmy rozmieszczania paneli na dachu (`own` i `pro`) |
| `solary/production.py` | szacunek produkcji: rocznie, miesięcznie, PVGIS |
| `solary/economics.py` | opłacalność: koszt instalacji, oszczędności, okres zwrotu |
| `solary/render.py` | obrazki PNG: „czy to Twój dom?” i układ paneli |
| `solary/service.py` | `analyze()`: cała analiza dachu w jednym wywołaniu |
| `solary/__main__.py` | polecenie `python -m solary` |
| `solary/api.py` | serwer FastAPI: końcówki `/api/...` i strona demo |
| `solary/env.py`, `solary/errors.py` | wczytywanie `.env`, klasa błędów |
| `solary/battery/` | magazyn energii, opis plików niżej |
| `web/index.html` | strona demo (czysty HTML i JavaScript, bez budowania) |
| `tests/` | testy bez dostępu do sieci; `tests/roofs.py` buduje sztuczne dachy |
| `pyproject.toml`, `uv.lock` | zależności; grupa `dev` to testy, grupa `rl` to nauka agenta |
| `.env.example` | wzór pliku `.env` z kluczem i opcjonalnymi ustawieniami |
| `Dockerfile`, `.dockerignore` | obraz z serwerem (bez testów i bez `.env`) |
| `render.yaml` | wdrożenie na Render.com |
| `run_demo_tunnel.bat` | publiczny adres HTTPS dla lokalnego serwera (cloudflared) |
| `data/` | cache, powstaje przy pierwszym uruchomieniu i nie trafia do repozytorium: dane Google w `data/roof/`, ceny i pogoda w `data/battery/` |

Pakiet `solary/battery/`:

| Plik | Zawartość |
|---|---|
| `data.py` | ceny RCE (PSE), pogoda (NASA POWER, Open-Meteo), produkcja paneli, profil zużycia domu |
| `model.py` | fizyka baterii, falownika i sieci, taryfy, prognozy, obserwacja agenta |
| `strategies.py` | sterowania do porównania: bez magazynu, zwykły falownik, MPC liniowe i nieliniowe, optimum |
| `policy.py`, `policy.npz` | nauczony agent: sieć neuronowa liczona w numpy i jej wagi |
| `plan.py` | rachunek za rok i plan od teraz do końca jutra (`battery_report()`) |
| `evaluate.py`, `evaluation.json` | ocena wszystkich strategii na roku testowym i jej wynik |
| `env.py`, `train.py` | środowisko i nauka agenta (imitacja + PPO); wymagają grupy `rl` |
| `__main__.py` | polecenie `python -m solary.battery` |

## Jak działa analiza dachu

1. **Adres.** Nominatim szuka adresu tylko w Polsce. Przedrostek „ul.” jest usuwany, bo z nim Nominatim nic nie
   znajduje. Jeśli tekst pasuje do kilku miejsc, pierwsze idzie do analizy, a pozostałe wracają w polu
   `alternatives`. Gdy udało się dopasować tylko ulicę, bez numeru domu, wynik ma ostrzeżenie `street_only`.
2. **Dach.** Google Solar API zwraca dla najbliższego budynku połacie dachu (nachylenie, kierunek, powierzchnia)
   i listę wszystkich paneli, które się mieszczą. Każdy panel ma położenie i roczną energię, w której są już
   cienie drzew, sąsiednich budynków i samego dachu.
3. **Układ paneli.** Domyślnie dla N paneli bierzemy pierwsze N z listy Google. To kolejność ich algorytmu
   układania, więc panele tworzą zwarte pola. Alternatywa `panel_order="yield"` bierze panele ściśle od
   najlepszego: daje 0–1% więcej energii, ale panele są porozrzucane. Zamiast układu Google można użyć
   własnego (`layout="own"` albo `"pro"`, opis niżej). Strona demo domyślnie używa `pro`.
4. **Produkcja roczna.** Suma rocznej energii DC wybranych paneli × (1 − straty systemu 14%).
5. **Miesiące.** Roczną produkcję każdej połaci dzielimy profilem miesięcznym z PVGIS, policzonym dla
   współrzędnych tego dachu oraz nachylenia i kierunku tej połaci. Ten sam wynik PVGIS daje niezależny szacunek
   bez lokalnych cieni. Pokazujemy go obok jako kontrolę (`pvgis_kwh_per_kwp`).
6. **Obrazki.** Z warstw mapy Google powstają trzy pliki PNG: budynek z czerwoną ramką i kropką w miejscu
   adresu, wszystkie panele oraz wybrane panele na mapie nasłonecznienia dachu.

Gdy Google nie ma danych o dachu, wynik ma `"roof_available": false` i szacunek z samego PVGIS dla podanej mocy,
nachylenia i kierunku (domyślnie 35° na południe), bez rozmieszczenia paneli.

### Własny układ paneli (`layout="own"` i `"pro"`)

Google podaje gotową listę paneli, ale według własnych, nieopublikowanych reguł: panel 400 W, zero odstępu od
krawędzi, kalenicy i kominów. `solary/layout.py` rozmieszcza panele sam, na warstwach mapy z Solar API (maska dachu,
mapa nasłonecznienia i mapa wysokości co 10 cm), więc reguły ustawiamy my:

1. **Wolna powierzchnia.** Każda połać to płaszczyzna (środek, wysokość, nachylenie, kierunek). Płaszczyznę Google
   dopasowujemy do mapy wysokości metodą najmniejszych kwadratów. Piksel dachu należy do połaci, na której
   płaszczyźnie leży (±15 cm). Piksel, który nie leży na żadnej, to przeszkoda: komin, lukarna, attyka, drzewo
   nad dachem albo inny poziom dachu. Tam paneli nie ma.
2. **Pola.** Połać skośna to jedno pole. Połacie płaskie na jednej wysokości (Google często dzieli jeden płaski dach
   na kilka) i połacie leżące na jednej płaszczyźnie tworzą wspólne pole. Dach na innej wysokości jest osobno,
   więc żaden panel nie wisi nad uskokiem. Połacie bardziej strome niż 60° pomijamy.
3. **Kandydaci.** Panele leżą w siatce: na dachu skośnym równolegle do okapu, na płaskim równolegle do obrysu
   dachu (najmniejszy prostokąt wokół niego). Sprawdzamy oba ustawienia panelu i każde przesunięcie siatki co
   10 cm. Panel się liczy, gdy on i odstęp wokół niego leżą w całości na wolnym dachu. Rząd może się przesunąć o
   pół panelu, jeśli zmieści wtedy jeden panel więcej. Wybieramy siatkę z największą energią; gdy kilka jest
   równych, tę pośrodku dachu.
4. **Energia.** Roczna energia DC panelu to średnie nasłonecznienie pod nim (kWh na kW rocznie, z cieniami)
   razy moc panelu. Tak samo Google liczy energię swoich paneli.
5. **Kolejność.** Panele są ułożone od najlepszego, z premią 8% za każdy sąsiedni panel już wybrany. Układ dla
   N paneli to pierwsze N z listy, więc suwak na stronie dokłada panele do zwartego pola wokół najlepszego
   miejsca.

Wynik ma ten sam format co lista Google, więc produkcja, tabele i obrazki działają bez zmian. Pole `layout`
w odpowiedzi podaje dla porównania, ile dałby układ Google dla tej samej liczby paneli. Gdy mapy wysokości nie da
się pobrać, wynik pokazuje układ Google z ostrzeżeniem `layout_fallback`.

Algorytm to przeszukiwanie z twardymi ograniczeniami geometrycznymi, a nie model uczony na danych: problem jest
mały, a wynik da się sprawdzić. Działa tylko tam, gdzie Google ma dane o dachu, bo korzysta z jego warstw mapy.

Wariant `layout="pro"` (na stronie „Solari PRO”) używa tej samej wolnej powierzchni i tych samych kandydatów, ale
bez przesuwania rzędów o pół panelu, a kolejność dobiera tak, żeby panele tworzyły równe prostokątne pola: zaczyna
od najlepszej połaci i dokłada panel, który najmniej powiększa obrys pola.

### Opłacalność

`solary/economics.py` liczy prosty, orientacyjny raport dla wybranej mocy i jej rocznej produkcji:

- **Koszt instalacji** (z montażem, 8% VAT) zależy od mocy: do 4 kWp 4 800 zł za kWp, do 7 kWp 4 200 zł, do 12 kWp
  3 700 zł, do 20 kWp 3 300 zł, powyżej 3 000 zł. Ulgę termomodernizacyjną liczymy jako 12% tego kosztu.
- **Roczne oszczędności.** Część produkcji zużywana na bieżąco (domyślnie 25%) jest warta cenę prądu z sieci
  (1,10 zł/kWh), a reszta cenę sprzedaży nadwyżek (0,40 zł/kWh). Od sumy odejmujemy koszty utrzymania: 0,8% kosztu
  instalacji rocznie, co najmniej 150 zł.
- **Okres zwrotu** to koszt instalacji (z ulgą i bez) podzielony przez roczne oszczędności po kosztach utrzymania.
- **Zysk po 25 latach** sumuje oszczędności malejące o 0,5% rocznie (starzenie paneli) i odejmuje koszt instalacji
  bez ulgi.

Ceny i autokonsumpcję zmienia się suwakami na stronie albo parametrami `retail_price`, `feed_in_price`
i `self_consumption`. To prostszy model niż rachunek w części o magazynie energii, więc liczb z obu części nie da
się ze sobą porównać.

### Przykładowe wyniki (3.10.2026)

| Adres | Układ | Produkcja | Z 1 kWp | PVGIS dla tej orientacji |
|---|---|---|---|---|
| Mariacka 1, Katowice | 15 paneli, 6,0 kWp | 5 729 kWh/rok | 955 kWh | 921 kWh |
| Świdnicka 10, Wrocław | 15 paneli, 6,0 kWp | 5 457 kWh/rok | 910 kWh | 879 kWh |
| Rynek Wielki 1, Zamość | brak danych dachu, 5 kWp, 35° płd. | 5 267 kWh/rok | 1 053 kWh | to jest PVGIS |

## Magazyn energii sterowany przez AI

Dom ma panele (te z analizy dachu), baterię, umowę z ceną zależną od rynku i lokalną sieć, która w słoneczne południe
bywa przeładowana. Co 15 minut sterownik wybiera jedną z 11 decyzji: autokonsumpcja, bez ruchu, ładuj tylko
z nadwyżki PV, ładuj tylko szczyt ponad limit sieci, pokrywaj zużycie (a nadwyżkę sprzedaj), ładuj z sieci albo
sprzedawaj z baterii z mocą 25, 50 lub 100%. Decyzję podejmuje **agent nauczony metodą uczenia ze wzmocnieniem**
(sieć neuronowa 226 → 256 → 256 → 11).

```bash
uv run python -m solary.battery plan "Mariacka 1, Katowice" --battery-kwh 10 --annual-kwh 4000 --tariff g11
uv run python -m solary.battery plan --lat 50.26 --lon 19.02 --kwp 6 --tariff dynamic --export-limit 3
uv run python -m solary.battery evaluate        # wszystkie strategie na roku testowym -> solary/battery/evaluation.json
uv sync --group rl                              # torch i reszta pakietów do nauki
uv run python -m solary.battery train           # nauka agenta od nowa (ok. 40 min na 4 rdzeniach)
```

Wynik `plan`: rachunek za rok testowy bez magazynu, ze zwykłym falownikiem, z agentem i w optimum oraz plan od teraz
do końca jutra (co robi bateria w każdym kwadransie). `--export-limit 3` oznacza słabą sieć, w której falownik
wyłącza się, gdy oddaje ponad 3 kW. Na stronie demo to sekcja „Magazyn energii” pod wynikiem dachu.

Opcje `python -m solary.battery plan`:

| Opcja | Domyślnie | Opis |
|---|---|---|
| `address` | – | adres: panele pochodzą wtedy z analizy dachu |
| `--lat`, `--lon` | – | współrzędne zamiast adresu |
| `--panels` | – | liczba paneli w analizie dachu |
| `--layout` | `google` | układ paneli w analizie dachu: `google` albo `own` |
| `--kwp`, `--tilt`, `--azimuth` | 6, 35, 180 | instalacja, gdy nie ma danych o dachu albo podano same współrzędne |
| `--battery-kwh` | 10 | pojemność baterii |
| `--battery-kw` | połowa pojemności na godzinę | moc baterii |
| `--annual-kwh` | 4000 | roczne zużycie domu |
| `--tariff` | `g11` | `g11` albo `dynamic` |
| `--soc` | 0,5 | naładowanie baterii teraz (0–1) |
| `--export-limit` | – | słaba sieć: falownik wyłącza się powyżej tylu kW oddawanych do sieci |
| `--no-plan` | – | tylko rachunek za rok, bez planu |
| `--json` | – | pełny wynik jako JSON |

`evaluate --no-mpc` pomija oba MPC (najwolniejsze, kilka minut na dom). `train` przyjmuje `--steps` (domyślnie
3 000 000 kroków PPO), `--imitation-weeks` (800), `--envs` (8), `--seed` (0) i `--out` (plik z agentem).

### Fizyka: dlaczego to nie jest zadanie liniowe

Prawdziwa instalacja nie jest liniowa, więc symulator też nie jest (`solary/battery/model.py`):

- **Straty falownika zależą od mocy.** Falownik hybrydowy zużywa ok. 40 W, gdy przesyła energię z lub do baterii,
  a do tego traci moc proporcjonalnie do jej kwadratu. Pokrywanie nocnego zużycia 200 W z baterii traci ok. 20%
  energii, ładowanie z mocą 2,5 kW ok. 3%.
- **Zużycie baterii.** Szybkie ładowanie i rozładowanie zużywa więcej na kWh, a trzymanie baterii powyżej 90%
  przyspiesza starzenie.
- **Wyłączenia falownika przez napięcie sieci.** W słoneczne południe lokalna sieć jest pełna prądu z PV (cena RCE
  jest wtedy niska), a każde oddane kW podnosi napięcie. Powyżej limitu zabezpieczenie (253 V) wyłącza falownik:
  produkcja z tego kwadransu przepada, a dom bierze prąd z sieci. To częsty problem na polskich wsiach i osiedlach
  z wieloma instalacjami. Modelujemy to jako limit mocy oddawanej w kwadransach z RCE ≤ 0,15 zł/kWh.
- **Niepewna pogoda.** Błąd prognozy produkcji zależy od pogody: dni bezchmurne i pochmurne są łatwe, a dni
  z przelotnymi chmurami trudne (błąd do ok. 50% na jutro). Sterownik zna niepewność każdego dnia, jak z prognozy
  zespołowej, ale nie zna samego błędu.
- **Zakup i sprzedaż nigdy w tym samym kwadransie**, nawet gdy RCE jest wyższe od ceny zakupu.

### Skąd wiemy, kiedy prąd jest drogi

- **Ceny.** PSE publikuje RCE (rynkową cenę energii) na każdy kwadrans następnego dnia, zwykle około 14:00
  (`api.raporty.pse.pl/api/rce-pln`, bez klucza). Do 14:00 znamy ceny do północy, potem do końca jutra. Dalej
  sterownik zakłada ceny jak dzień wcześniej. Typowy dzień: najtaniej w południe (słońce zalewa sieć, bywa poniżej
  zera), najdrożej około 19:00.
- **Pogoda.** Historia: NASA POWER (godzinowe nasłonecznienie, światło rozproszone, temperatura). Prognoza:
  Open-Meteo. Nasłonecznienie przeliczamy na każdą połać dachu (model izotropowy) i na moc paneli (temperatura,
  straty 14%). Z analizą dachu produkcję skalujemy do rocznego wyniku Google, który zna lokalne cienie.
- **Zużycie.** Typowy dom bez ogrzewania elektrycznego (profil G11: szczyt rano i wieczorem, więcej zimą),
  przeskalowany do podanego rocznego zużycia, z losowymi wahaniami (czajnik, piekarnik, pralka).

### Pieniądze: net-billing

Prąd z sieci kosztuje część „energia” plus dystrybucję. Prąd oddany do sieci jest wart RCE z danego kwadransu
(0 zł przy ujemnej cenie) i trafia do depozytu prosumenckiego, który pokrywa tylko część „energia” późniejszych
rachunków. Niewykorzystany depozyt po 12 miesiącach wraca najwyżej w 30%. Dlatego wartość jednej sprzedanej kWh
zależy od całego roku domu: póki depozyt się zużywa, jest warta pełne RCE; gdy depozytu jest dużo więcej niż
kupowanej energii, tylko 30% RCE (a kupowana kWh kosztuje wtedy samą dystrybucję). Parametr `theta` (0–1) ustawia
dom między tymi skrajnościami; dobieramy go tak, żeby roczny rachunek był najniższy. Rachunek roczny liczymy dokładnie
według tych zasad. Domyślne ceny 2026 r. (G11: energia 0,62 zł + dystrybucja 0,38 zł za kWh brutto; dynamiczna:
(RCE + 0,05 zł) × 1,23 + 0,38 zł), straty falownika i koszty zużycia baterii to założenia w
`solary/battery/model.py`: porównaj je ze swoim rachunkiem i kartą katalogową falownika.

### Strategie, z którymi porównujemy agenta

| Strategia | Co wie | Opis |
|---|---|---|
| bez magazynu | – | rachunek z samymi panelami |
| zwykły falownik | bieżący kwadrans | ładuje z nadwyżki, oddaje, gdy dom potrzebuje: tak działa większość falowników |
| agent RL | ceny opublikowane, prognozy i ich niepewność | sieć neuronowa wybiera jedną z 11 decyzji w 0,3 ms (z przygotowaniem danych) |
| MPC liniowe | to samo co agent | co godzinę najtańszy plan na 36 h jako program liniowy: stała sprawność, liniowe zużycie, limit sieci jako ograniczenie; tak działa większość przemysłowych sterowników |
| MPC nieliniowe | to samo co agent | co godzinę programowanie dynamiczne na 36 h z dokładną fizyką i tymi samymi 11 decyzjami, ale ufa prognozie; mocny, wolniejszy planista |
| optimum | całą przyszłość dokładnie | programowanie dynamiczne z dokładną fizyką na całym roku; najlepszy możliwy wynik przy tych decyzjach, służy za miarę |

### Jak uczy się agent

1. **Symulator.** Tydzień prawdziwych cen RCE i pogody z okresu 1.07.2024–30.06.2025 w jednym z czterech miast
   (Katowice, Warszawa, Gdańsk, Wrocław) i losowy dom: 3–12 kWp w 7 orientacjach, bateria 5–20 kWh z losowymi
   stratami i kosztami zużycia, zużycie 2000–7000 kWh, G11 albo taryfa dynamiczna, różne ceny i `theta`, mocna sieć
   albo słaba (limit 30–80% mocy PV).
2. **Co widzi agent.** Naładowanie baterii, porę dnia i roku, ceny zakupu i sprzedaży, prognozę PV i zużycia na
   36 h (pierwsze 4 h co kwadrans, dalej co godzinę), kwadranse zagrożone wyłączeniem w najbliższych 4 h, limit
   sieci, niepewność prognozy na dziś i jutro, ile godzin cen jest już opublikowanych oraz parametry baterii.
3. **Nagroda.** Złotówki zaoszczędzone w danym kwadransie względem tego samego domu bez baterii, z dokładną fizyką.
4. **Imitacja.** Sieć najpierw uczy się decyzji MPC nieliniowego z 800 losowych tygodni (538 tys. decyzji); zgadza
   się z nim w 93% kwadransów.
5. **PPO** (stable-baselines3): osobne sieci decyzji i wartości 256 × 256. Agent sam prowadzi tydzień za
   tygodniem i poprawia to, czego się nauczył. Żeby PPO nie zepsuło imitacji: normalizacja wejść zostaje
   zamrożona (sieć nauczyła się na niej), przez pierwsze 300 tys. kroków uczy się tylko krytyk (wartość stanu),
   bez losowej eksploracji (entropia 0), z małym, malejącym krokiem i limitem zmiany polityki (target KL). Co
   200 tys. kroków agent dostaje ocenę na 24 stałych tygodniach walidacyjnych; zapisujemy najlepszą wersję.
6. **Eksport.** Wagi sieci i normalizacja wejść trafiają do `solary/battery/policy.npz`. Aplikacja liczy decyzje
   w czystym numpy, bez torcha.
7. **Test.** `evaluate` sprawdza wszystkie strategie na roku 1.07.2025–30.06.2026, którego agent nie widział.

<!-- battery-results -->
### Wyniki na roku testowym

Rok 2025-07-01 – 2026-06-30 w Katowicach: prawdziwe ceny RCE i pogoda, typowe zużycie, fizyka nieliniowa. Agent nie widział tego roku podczas nauki. Rachunek roczny według zasad net-billingu, ze stratami falownika i zużyciem baterii; w nawiasie, jaką część możliwej oszczędności (optimum) daje każda strategia, a pod spodem, ile razy falownik wyłączył się przez napięcie sieci. Pełny raport: `solary/battery/evaluation.json`.

| Dom | Bez magazynu | Zwykły falownik | Agent RL | MPC liniowe | MPC nieliniowe | Optimum |
|---|---|---|---|---|---|---|
| 6 kWp na południe, bateria 10 kWh, G11, mocna sieć, 4 000 kWh/rok | 1 174 zł | 460 zł (69%) | 175 zł (96%) | 196 zł (94%) | 163 zł (98%) | 138 zł |
| 6 kWp na południe, bateria 10 kWh, G11, słaba sieć (wyłącza powyżej 3 kW), 4 000 kWh/rok | 1 284 zł<br>991 wyłączeń | 560 zł (63%)<br>916 wyłączeń | 174 zł (97%)<br>18 wyłączeń | 195 zł (95%)<br>16 wyłączeń | 164 zł (98%)<br>18 wyłączeń | 138 zł<br>0 wyłączeń |
| 6 kWp na południe, bateria 10 kWh, taryfa dynamiczna, słaba sieć, 4 000 kWh/rok | 1 589 zł<br>991 wyłączeń | 582 zł (69%)<br>916 wyłączeń | 174 zł (98%)<br>18 wyłączeń | 195 zł (96%)<br>16 wyłączeń | 164 zł (98%)<br>18 wyłączeń | 138 zł<br>0 wyłączeń |
| 8 kWp wschód-zachód, bateria 5 kWh, G11, słaba sieć (4 kW), 5 000 kWh/rok | 1 644 zł<br>263 wyłączeń | 1 161 zł (56%)<br>263 wyłączeń | 861 zł (91%)<br>21 wyłączeń | 904 zł (86%)<br>8 wyłączeń | 836 zł (94%)<br>6 wyłączeń | 788 zł<br>0 wyłączeń |
| 4 kWp na południe, bateria 15 kWh, taryfa dynamiczna, mocna sieć, 6 000 kWh/rok | 4 059 zł | 2 746 zł (62%) | 2 173 zł (90%) | 2 110 zł (93%) | 2 040 zł (96%) | 1 955 zł |

**Co z tego wynika.**

- **Zwykły falownik** zostawia 30–45% możliwej oszczędności. Przy słabej sieci wyłącza się ok. 900 razy w roku:
  rano ładuje baterię do pełna, a w południe nie ma już gdzie schować nadwyżki, więc wysyła ją do sieci, która
  jest przeciążona.
- **Agent RL** daje 90–98% optimum i na czterech z pięciu domów jest lepszy od MPC liniowego, choć wie dokładnie to
  samo. Tam, gdzie liczy się nieliniowość (straty rosnące z mocą, szybsze zużycie przy dużym prądzie, wyłączenie
  falownika „wszystko albo nic”), liniowy plan podejmuje gorsze decyzje. Wyjątek to dom z dużą baterią (15 kWh)
  i taryfą dynamiczną: tam liczy się głównie arbitraż cenowy, który program liniowy planuje dobrze.
- **MPC nieliniowe** (programowanie dynamiczne co godzinę) jest jeszcze o 1–6 punktów lepsze, ale liczy ok.
  17 razy dłużej (4,3 ms wobec 0,26 ms na kwadrans) i potrzebuje dokładnego modelu fizyki konkretnego domu. Agent
  ma tę wiedzę w wagach, nauczoną na tysiącach losowych domów, i działa na słabym sprzęcie bez solvera.

Agent uczy się od MPC nieliniowego (imitacja), a PPO dokłada niewiele (+0,1 zł na tydzień walidacyjny): planista
jest już blisko optimum, więc nauka metodą prób i błędów ma mało do poprawienia. Większy krok uczenia (2e-4 zamiast
5e-5) przez chwilę daje tyle samo, a po milionie kroków psuje politykę (31,7 → 31,1 zł na tydzień), dlatego PPO
ma tu mały krok i limit KL. Zysk z RL to przede wszystkim szybkość i brak solvera przy jakości bliskiej planisty,
a nie przebicie go.
<!-- /battery-results -->

### Ograniczenia magazynu

- Zużycie domu jest typowe, nie Twoje: odczyty z licznika dałyby dokładniejszy wynik.
- Błąd prognozy PV w nauce i ocenie jest symulowany (jego wielkość zależy od pogody); prawdziwe archiwalne prognozy
  dałyby uczciwszy test.
- Wyłączenia przez napięcie modelujemy prosto: limit mocy oddawanej przy niskiej cenie RCE. Prawdziwe napięcie
  zależy od sieci i sąsiadów.
- Depozyt rozliczamy w skali roku, a nie miesiąc po miesiącu; nie ma opłat stałych.
- Agent jest oceniany w symulacji. Do sterowania prawdziwą baterią trzeba połączyć go z falownikiem (np. Modbus).

## Założenia

Wszystkie są w `solary/config.py`. API zwraca je w polu `assumptions`.

| Pole | Wartość | Znaczenie |
|---|---|---|
| `system_loss_pct` | 14 | straty od paneli do gniazdka: falownik, kable, zabrudzenie (domyślna wartość PVGIS) |
| `panel_watts` | brak, czyli 400 W | panel, który układa Google: 400 W, 1,879 × 1,045 m. Inna moc przelicza moc i energię proporcjonalnie; ma sens tylko dla paneli o podobnych wymiarach |
| `panel_order` | `google` | kolejność wybierania paneli (`google` albo `yield`) |
| `layout` | `google` (zmienna `SOLARY_LAYOUT`) | kto rozmieszcza panele: `google`, `own` albo `pro` (nasze algorytmy) |
| `layout_margin_m` | 0,2 | własny układ: wolny pas wokół panelu od krawędzi, kalenicy i przeszkód |
| `layout_gap_m` | 0,02 | własny układ: szczelina między panelami (klemy) |
| `layout_height_tol_m` | 0,15 | piksel dalej od płaszczyzny połaci to przeszkoda |
| `layout_max_pitch_deg` | 60 | na bardziej strome połacie nie kładziemy paneli |
| `layout_compactness` | 0,08 | premia za sąsiedni panel przy wyborze kolejnego (0 = ściśle od najlepszego) |
| `layout_shift_rows` | tak | rząd może się przesunąć o pół panelu, gdy zmieści jeden więcej |
| `panel_size_m` | brak, czyli 1,879 × 1,045 m | wymiary panelu we własnym układzie; ustaw razem z `panel_watts` |
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
uv run python -m solary "Mariacka 1, Katowice" --panels 20 --layout own --margin 0.3
uv run python -m solary "Mariacka 1, Katowice" --layout own --panel-size 2.278 1.134 --panel-watts 550
```

| Opcja | Domyślnie | Opis |
|---|---|---|
| `address` | – | adres w cudzysłowie, np. `"Mariacka 1, Katowice"` |
| `--lat`, `--lon` | – | współrzędne dachu zamiast adresu |
| `--kwp` | 6 | moc instalacji |
| `--panels` | – | liczba paneli zamiast mocy |
| `--panel-watts` | 400 | moc jednego panelu |
| `--order` | `google` | `google` (kolejność układu) albo `yield` |
| `--layout` | `google` | kto rozmieszcza panele: `google`, `own` albo `pro` |
| `--margin` | 0,2 | własny układ: odstęp od krawędzi i przeszkód w metrach |
| `--gap` | 0,02 | własny układ: szczelina między panelami w metrach |
| `--panel-size` | 1,879 1,045 | własny układ: wysokość i szerokość panelu w metrach |
| `--tilt`, `--azimuth` | 35, 180 | tylko dla szacunku bez danych dachu; azymut kompasowy, 180 = południe |
| `--no-images` | – | bez pobierania warstw mapy i bez obrazków |
| `--data` | `./data` | folder cache |
| `--json` | – | pełny wynik jako JSON |

## Użycie z Pythona

```python
from solary import analyze

res = analyze(address="Mariacka 1, Katowice", kwp=6)
if res["roof_available"]:
    chosen = res["selected"]
    print(chosen["kwh_year"], chosen["monthly_kwh"], chosen["economics"]["payback_with_relief_years"])
else:
    print(res["generic"]["kwh_year"])
```

Własny układ paneli i inne założenia ustawia się przez `Config`:

```python
import dataclasses
from solary import CONFIG, analyze

res = analyze(address="Mariacka 1, Katowice", panels=20, cfg=dataclasses.replace(CONFIG, layout="pro"))
```

Magazyn energii dla domu z instalacją 6 kWp, 35° na południe:

```python
from solary.battery.model import Battery
from solary.battery.plan import battery_report

report = battery_report(50.26, 19.02, [(6.0, 35, 180)], annual_kwh=4000, battery=Battery(10, 5))
print(report["year"]["rows"], report["plan"]["now"])
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
| `GET /api/roof?address=…&layout=own&margin=0.2` | panele rozmieszcza nasz algorytm |
| `GET /api/roof/image/{nazwa}` | obrazek PNG z pola `images` |
| `GET /api/economics?sizes=6:5729,3.2:3060` | opłacalność instalacji podanych jako `kWp:kWh rocznie` |
| `GET /api/battery?lat=…&lon=…&planes=6:35:180&battery_kwh=10&annual_kwh=4000&tariff=g11&soc=0.5` | magazyn: rachunek za rok i plan od teraz do końca jutra |
| `GET /api/battery/evaluation` | wszystkie strategie na roku testowym dla domów referencyjnych (`solary/battery/evaluation.json`) |

Parametry `/api/roof`: `address` albo `lat` + `lon`; `kwp` albo `panels`; `images=false` wyłącza obrazki;
`tilt` i `azimuth` działają tylko w szacunku bez danych dachu; `layout` (`google`, `own` albo `pro`) wybiera, kto
rozmieszcza panele. We własnych układach `margin` (0–2 m) to odstęp od krawędzi, `gap` (0–0,5 m) szczelina między
panelami, a `panel_height` i `panel_width` (podawane razem) wymiary panelu. `panel_watts` to moc jednego panelu,
`order=yield` bierze panele ściśle od najlepszego. `retail_price` (cena prądu z sieci, domyślnie 1,10 zł/kWh),
`feed_in_price` (cena sprzedaży nadwyżek, 0,40 zł/kWh) i `self_consumption` (procent energii zużywanej na bieżąco,
25) ustawiają opłacalność; te same trzy parametry przyjmuje `/api/economics`.

Parametry `/api/battery`: `lat`, `lon`; `planes` (`kWp:nachylenie:azymut`, kilka po przecinku); `kwh_year` skaluje
produkcję do wyniku analizy dachu; `battery_kwh` i `battery_kw` (domyślnie połowa pojemności na godzinę);
`annual_kwh` (zużycie domu); `tariff` (`g11` albo `dynamic`); `soc` (naładowanie teraz, 0–1); `export_limit`
(słaba sieć: falownik wyłącza się powyżej tylu kW); `plan=false` pomija plan na dziś i jutro.

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
| `warnings` | `street_only`, `far_from_address`, `monthly_fallback`, `images_unavailable`, `layout_fallback` |
| `sizes` | tabela mocy: `kwp`, `panels`, `kwh_year`, `kwh_per_kwp`, `economics` |
| `images` | adresy obrazków: `confirm`, `all`, `selected` |
| `assumptions`, `attribution` | założenia i tekst o źródłach danych |

Gdy `roof_available` jest `true`:

| Pole | Opis |
|---|---|
| `building` | `lat`, `lon`, `distance_m` od adresu, `roof_area_m2`, `max_panels`, `max_kwp`, `imagery_quality`, `imagery_date` |
| `panel` | `watts`, `google_watts`, `height_m`, `width_m` |
| `segments` | połacie: `segment`, `facing` (N, NE, …), `pitch_deg`, `azimuth_deg`, `area_m2`, `max_panels`, `kwh_dc_per_panel` |
| `selected` | wybrany układ: `panels`, `kwp`, `kwh_year`, `kwh_per_kwp`, `monthly_kwh` (12 liczb), `monthly_source`, `pvgis_kwh_year`, `per_segment`, `economics` |
| `selected.per_segment` | `segment`, `facing`, `pitch_deg`, `azimuth_deg`, `panels`, `kwp`, `kwh_year`, `kwh_per_kwp`, `pvgis_kwh_per_kwp`, `vs_pvgis` |
| `layout` | `algorithm` (`google`, `own` albo `pro`); dla `own` i `pro` także `margin_m`, `gap_m`, `google_max_panels` i `google_kwh_year`: produkcja układu Google dla tej samej liczby paneli (`null`, gdy Google mieści ich mniej) |

Gdy `roof_available` jest `false`:

| Pole | Opis |
|---|---|
| `reason` | `no_roof_data` albo `no_panels_fit` |
| `generic` | `kwp`, `kwh_year`, `kwh_per_kwp`, `monthly_kwh`, `monthly_source`, `tilt_deg`, `azimuth_deg`, `facing`, `economics` |

`monthly_source` ma wartość `pvgis` albo `typical_poland`. Ta druga oznacza, że PVGIS nie odpowiedział i miesiące
pochodzą z typowego profilu dla środkowej Polski.

Pole `economics` (w `selected`, w `generic`, w każdym wierszu `sizes` i w odpowiedzi `/api/economics`):

| Pole | Opis |
|---|---|
| `capex_gross_pln`, `pit_relief_pln`, `capex_net_pln` | koszt instalacji, ulga termomodernizacyjna (12%) i koszt po uldze |
| `annual_opex_pln` | roczne koszty utrzymania (0,8% kosztu instalacji, co najmniej 150 zł) |
| `self_kwh`, `export_kwh` | energia zużyta na bieżąco i oddana do sieci |
| `self_savings_pln`, `export_income_pln` | uniknięty zakup prądu i przychód ze sprzedaży nadwyżek |
| `gross_savings_annual_pln`, `net_savings_annual_pln` | roczne oszczędności przed kosztami utrzymania i po nich |
| `payback_years`, `payback_with_relief_years` | okres zwrotu bez ulgi i z ulgą (`null`, gdy oszczędności nie są dodatnie) |
| `profit_25y_pln` | zysk po 25 latach, z degradacją paneli 0,5% rocznie |
| `retail_price_pln`, `feed_in_price_pln`, `self_consumption_pct` | przyjęte ceny i autokonsumpcja |

### Strona demo

`web/index.html` korzysta ze wszystkich funkcji serwera:

- **Dach:** wybór algorytmu (Solari PRO, Solari Classic, Google), odstęp od krawędzi, szczelina między panelami,
  typ panelu (moc i wymiary), kolejność paneli, suwak liczby paneli, trzy widoki dachu, tabela połaci.
- **Opłacalność:** raport i tabela mocy pokazują liczby z `solary/economics.py`. Suwaki cen i autokonsumpcji oraz
  podgląd przy przesuwaniu suwaka paneli pytają `/api/economics`, więc wzór jest w jednym miejscu.
- **Magazyn energii:** działa także dla adresu bez danych o dachu (wtedy dla podanej mocy, nachylenia i kierunku).
  Pokazuje plan agenta do końca jutra z kosztem, rachunek za rok dla każdego sterowania, informację o nauczonym
  agencie oraz tabelę z `/api/battery/evaluation`: agent na tle MPC i optimum.

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
- **Dachy płaskie.** Google i nasz algorytm kładą panele na płasko, zgodnie z połacią. W praktyce na płaskim
  dachu stawia się stelaże pod kątem, więc realny układ i uzysk będą inne.
- **Przeszkody we własnym układzie.** Widzimy tylko to, co wystaje ponad połać o więcej niż 15 cm na mapie
  wysokości co 10 cm. Okna dachowe równo z połacią i cienkie rury wentylacyjne mogą zostać przykryte panelem.
  Google nie podaje dokładnego obrysu połaci, tylko prostokątną ramkę. W zabudowie szeregowej dach sąsiada leżący
  w tej samej płaszczyźnie może więc w rogach ramki zostać uznany za nasz: sprawdź obrazek z wybranymi panelami.
- **Cienie.** Są wliczone w sumę roczną, ale podział na miesiące ich nie zna.
- **To szacunek.** Wynik opiera się na danych wieloletnich. Pojedynczy rok może się różnić o kilka procent,
  a strat 14% nikt nie zmierzył dla konkretnej instalacji.
- **Dwa modele finansów.** Raport opłacalności dachu (`solary/economics.py`) jest uproszczony: stawki za kWp, jedna
  cena zakupu, jedna cena sprzedaży i stały procent autokonsumpcji. Część o magazynie liczy rachunek dokładniej,
  kwadrans po kwadransie według zasad net-billingu. Liczby z obu części nie są ze sobą porównywalne.

## Źródła danych i ich warunki

- **Google Solar API.** Dane wolno przechowywać najwyżej 30 dni: moduł usuwa starsze pliki z `data/roof/` przy
  każdej analizie i przy starcie serwera. Własny układ paneli (`layout_*.json`) powstaje z danych Google, więc leży
  w tym samym folderze i jest usuwany razem z nimi. Przy wynikach trzeba pokazać, że pochodzą od Google. Obrazki powstają
  z warstw Solar API, a nie ze zrzutów ekranu Google Maps.
- **OpenStreetMap Nominatim.** Publiczny serwer pozwala na jedno zapytanie na sekundę (moduł tego pilnuje)
  i wymaga nagłówka User-Agent z nazwą aplikacji: ustaw `SOLARY_USER_AGENT` w `.env`. Przy wynikach trzeba
  podać „© autorzy OpenStreetMap”. Przy większym ruchu postaw własny serwer albo użyj płatnego geokodera.
- **PVGIS** (Komisja Europejska, JRC). Bezpłatny, bez klucza. Wyniki są cache'owane w `data/pvgis_cache.json`.
- **PSE** (ceny RCE), **NASA POWER** (historia pogody) i **Open-Meteo** (prognoza; darmowy dostęp do użytku
  niekomercyjnego, z podaniem źródła). Bez kluczy. Cache w `data/battery/`.

## Testy

`uv run pytest` uruchamia 188 testów. Nie łączą się z siecią: odpowiedzi Google, PVGIS i Nominatim są podstawione,
a rysowanie obrazków działa na małych, sztucznych plikach GeoTIFF. Własny układ paneli jest sprawdzany na sztucznych
dachach z `tests/roofs.py` (dwuspadowy z kominem, kopertowy, płaski z attyką i klimatyzatorem, dwa płaskie na różnych
wysokościach, mansardowy): odstęp od krawędzi i przeszkód, brak nakładania się paneli i paneli nad uskokiem,
odporność na szum mapy wysokości. Sprawdzają też wybór paneli i zaokrąglanie mocy,
przeliczenia produkcji, zamianę kierunków między Google i PVGIS, geometrię paneli, cache i jego wygasanie,
to, że klucz nie trafia do komunikatów błędów, oraz API razem z ochroną ścieżek do obrazków. Magazyn energii
(`tests/test_battery.py`) jest sprawdzany na sztucznych dniach: fizyka baterii, taryfy i rozliczenie depozytu, to,
co sterownik wie o cenach przed i po 14:00, kolejność strategii (optimum nigdy gorsze od reguły), dołączony agent
i plan na dziś. Test środowiska do nauki wymaga grupy `rl`.

Test na żywych danych to samo polecenie z „Szybkiego startu”.
