# Research: pomysły na zadanie „Artificial Intelligence” (HackYeah 2026)

> Stan wiedzy: 3 października 2026. Źródła na końcu dokumentu.
> Oceny punktowe pomysłów to subiektywne szacunki — mają pomóc w wyborze, nie są wyrocznią.

---

## TL;DR

- **Wagi oceny:** Idea & Innovation **30%**, Relation to Category **20%**, Practical Applicability / Usability **20%**, Design **20%**, Completeness **10%**.
  Połowa punktów to pomysł i wygląd, a kompletność waży tylko 10%. Lepiej zrobić jeden dopracowany przepływ niż pięć połowicznych funkcji.
- **Treść zadania wprost wymaga pokazania:** roli AI, współpracy komponentów, korzyści, decyzji technicznych, możliwości i ograniczeń oraz tego, **jak użytkownik weryfikuje wyniki i zachowuje kontrolę**, na jednym konkretnym use case.
  Wniosek: warstwa zaufania (cytaty, kontrola faktów, niepewność, akceptacja przez człowieka) powinna być **widoczną funkcją produktu**, a nie przypisem w prezentacji.
- **Rekomendowana trójka:**
  1. **„Łatwo”**: generator tekstów łatwych do czytania (ETR) z piktogramami dla instytucji publicznych. Odpowiada na realny obowiązek ustawowy, jest bardzo wykonalny i ma naturalną warstwę weryfikacji. → [A1](#a1-łatwo-tekst-łatwy-do-czytania-etr-z-piktogramami)
  2. **„Logobajki”**: bajki do terapii logopedycznej, generowane pod konkretne głoski i sprawdzane deterministycznym weryfikatorem. Najbardziej kreatywny i „wzruszający” w demo. → [A6](#a6-logobajki-spersonalizowane-bajki-do-terapii-logopedycznej)
  3. **„Próg”**: audyt dostępności wejścia do budynku ze zdjęcia (modele wizyjne) z zapisem do OpenStreetMap. Najbardziej nietypowy, z efektem „wow” na żywo, ale z wyższym ryzykiem technicznym. → [B1](#b1-próg-audyt-dostępności-wejścia-ze-zdjęcia)
  - Mocne alternatywy: **„Klauzula”** (umowy vs. rejestr klauzul niedozwolonych UOKiK) oraz **„Co ten list ode mnie chce?”** (pisma urzędowe po ludzku).

---

## 1. Co dokładnie jest oceniane (z dokumentów zadania)

**Zadanie (skrót):** stwórzcie rozwiązanie, w którym AI odgrywa istotną rolę i odpowiada na konkretną potrzebę użytkownika. Można korzystać z gotowych modeli, API i narzędzi albo budować własne. Kierunki podane przez organizatorów:
upraszczanie złożonej informacji · wspieranie nauki i dopasowanie sposobu podania wiedzy · dostępność treści i usług · wspieranie twórczości i rozwijania pomysłów · automatyzacja etapów pracy · analiza sytuacji i porównywanie możliwych działań.

**Pula nagród:** 8 000 PLN.

| Kryterium | Waga | Co to oznacza w praktyce |
|---|---|---|
| Idea & Innovation | 30% | Unikajcie „kolejnego chatbota do PDF-ów”. Wyróżnia niszowy, konkretny użytkownik, nieoczywiste dane, nietypowa modalność albo sprytny mechanizm weryfikacji. |
| Relation to Category | 20% | AI musi być sercem rozwiązania, a nie dodatkiem. Trzeba wprost pokazać rolę AI, przepływ komponentów i kontrolę użytkownika, bo tego wymaga treść zadania. |
| Practical Applicability / Usability | 20% | Realny użytkownik, realny problem, prosta obsługa. Konkretna persona i scenariusz. |
| Design | 20% | Dopracowany UI jednego głównego przepływu. Zaplanujcie na design minimum 20% czasu zespołu. |
| Completeness & Implementation Value | 10% | Nie trzeba pełnego produktu, ale demo musi działać, a ścieżka wdrożenia musi być wiarygodna. |

**Proces oceny:**
- **Faza 1:** komisja mentorów ocenia zgłoszenia. Do nagrody potrzeba co najmniej 50% punktów.
- **Faza 2:** finaliści pitchują na żywo przed jury.

**Zgłoszenie musi zawierać:** tytuł projektu, nazwę zespołu, członków (1–6 osób), opis i **prezentację PDF o długości maksymalnie 10 slajdów**. Opcjonalnie: repozytorium, link do demo, zrzuty ekranu, grafiki.
Opis zadania wskazuje platformę Challenge Rocket, a regulamin HackTribe, więc trzeba sprawdzić na Discordzie, która obowiązuje. Język: polski lub angielski.

**Ramy czasowe:** według regulaminu kategorii zgłoszenie trzeba oddać do 4.10, godz. 23:00. Godzina startu w PDF to „11:00 PM on October 3rd”. Godziny potwierdźcie na Discordzie, bo w PDF-ach bywają literówki.

**Polityka używania AI (ważne przy pitchu):**
- Narzędzia AI wolno stosować na każdym etapie pracy.
- Trzeba **ujawnić istotne użycie narzędzi AI, zewnętrznych modeli, API, zbiorów danych i bibliotek**.
- Zespół musi umieć wyjaśnić i obronić każdą decyzję techniczną, także w kodzie wygenerowanym przez AI.
- Trzeba odróżnić pracę wykonaną na hackathonie od rzeczy przygotowanych wcześniej.
- Jury ocenia złożoność, architekturę, integracje i zrozumienie systemu, a nie liczbę linii kodu.

→ Zaplanujcie slajd „Użyte modele, API, dane i narzędzia AI”.

---

## 2. Wzorce „AI, któremu można zaufać”: jak pokazać weryfikację i kontrolę

To nie jest sekcja „na koniec”. Te wzorce dają punkty w trzech kryteriach naraz: kategoria, użyteczność i innowacja.

| Wzorzec | Jak to zrobić | Przykład w UI |
|---|---|---|
| **Ugruntowanie i cytaty** | Każde twierdzenie AI ma klikalny fragment źródła. Claude API ma funkcję *Citations*, która zwraca `cited_text` i pozycję (`char_location` dla tekstu, `page_location` dla PDF). Alternatywa: dopasowanie zdań embeddingami. | Najechanie na zdanie wyniku podświetla fragment oryginału. |
| **Deterministycznie tam, gdzie się da** | Liczby, daty, terminy, kwoty i wymiary liczy kod, a nie LLM. LLM tylko interpretuje i tłumaczy. | „Termin: 17.10 (doręczenie 3.10 + 14 dni)” z pokazanym wzorem. |
| **Strażnik faktów** | Liczby, daty, nazwy własne i adresy wyciągnięte ze źródła (regex/NER) muszą występować w wyniku. Dodatkowo sprawdzenie, czy wynik „nie dodał” faktów (LLM-sędzia lub NLI). | Czerwona flaga „w uproszczeniu zginęła kwota 1 200 zł”. |
| **Pętla generuj → sprawdź → popraw** | Wynik LLM przechodzi przez weryfikator regułowy. Jeśli nie spełnia warunków, idzie z powrotem do modelu z informacją o błędzie. | „Wersja 3/3 spełnia wszystkie warunki ✔”. |
| **Kalibrowana niepewność** | Przedziały zamiast punktów: kwantyle z TabPFN/Chronos-2, przedział pomiaru w wizji, poziom pewności pola i odpowiedź „nie wiem” zamiast zgadywania. | „Wysokość stopnia: 14–19 cm”. |
| **Człowiek zatwierdza** | AI proponuje, człowiek akceptuje, edytuje albo odrzuca każdy element. Nic nie jest publikowane ani wysyłane bez akceptacji. | Przyciski ✔ / ✎ / ✖ przy każdym elemencie. |
| **Edytowalne założenia** | Parametry jako suwaki, wynik przelicza się na żywo („co jeśli”). | Suwak „maks. próg dla mojego wózka: 2 cm”. |
| **Proweniencja** | Przy każdej informacji: źródło, data, status wiarygodności (potwierdzone / zgłoszenie użytkownika / wygenerowane przez AI). | Etykiety-kolory przy danych. |
| **Panel „Jak to powstało”** | Kroki pipeline'u, użyte modele, źródła i wersja promptu. | Rozwijana sekcja pod wynikiem. |
| **Pętla feedbacku** | „Zgłoś błąd”, a zgłoszenie trafia do zbioru ewaluacyjnego. | Licznik zgłoszonych poprawek. |
| **Mini-ewaluacja** | Zbiór 20–30 przykładów testowych i metryka na slajdzie. Jury lubi liczby. | „Termin poprawnie wyliczony w 28/30 pism”. |

> Uwaga techniczna (Claude API): *Citations* nie działa razem ze structured outputs (`output_config.format`), bo API zwraca błąd 400. Typowy układ to dwa wywołania: ekstrakcja do JSON przez structured outputs, potem osobne wywołanie z cytatami, albo własne mapowanie zdań na źródło.

---

## 3. Katalog pomysłów

Każdy pomysł opisuje: **dla kogo**, **scenariusz demo**, **rolę AI**, **dane**, **weryfikację i kontrolę**, **ograniczenia** oraz ocenę (★ 1–5): *Innowacja / Wykonalność w 24 h / Efekt w demo*.

### A. Tekst i język (LLM, RAG, agenci)

#### A1. „Łatwo”: tekst łatwy do czytania (ETR) z piktogramami

- **Dla kogo:** urzędy, szkoły, szpitale i NGO. Podmioty publiczne mają ustawowy obowiązek publikowania informacji o swojej działalności w tekście łatwym do czytania (ustawa o zapewnianiu dostępności osobom ze szczególnymi potrzebami z 19.07.2019, art. 6). Odbiorcy końcowi to osoby z niepełnosprawnością intelektualną, seniorzy, cudzoziemcy i osoby z dysleksją.
- **Scenariusz demo:**
  1. Redaktorka strony gminy wkleja urzędowy tekst „Jak złożyć wniosek o Kartę Dużej Rodziny”.
  2. Po ok. 30 s dostaje wersję ETR: krótkie zdania (jedna myśl = jedno zdanie), słowniczek trudnych słów, piktogram przy każdym akapicie i wersję audio.
  3. Poprawia dwa zdania i akceptuje.
  4. Eksportuje dostępny HTML (WCAG) i PDF do druku.
- **Rola AI:**
  1. LLM upraszcza tekst według reguł ETR (wytyczne Inclusion Europe w system prompcie, kilka przykładów).
  2. Embeddingi i LLM dobierają piktogramy do kluczowych pojęć (ARASAAC ma polskie etykiety).
  3. TTS czyta tekst z podświetlaniem słów.
  4. LLM-sędzia sprawdza zgodność z checklistą ETR.
- **Dane:** dowolne teksty z BIP-ów i stron urzędów (publiczne), piktogramy ARASAAC.
- **Weryfikacja i kontrola:**
  - Widok dwukolumnowy: każde zdanie ETR jest powiązane ze zdaniem źródłowym (najechanie podświetla oryginał).
  - **Strażnik faktów:** liczby, daty, kwoty, nazwy urzędów i adresy ze źródła muszą wystąpić w wersji ETR, a ich brak daje czerwoną flagę.
  - Metryki przed i po: średnia długość zdania, odsetek trudnych słów, FOG-PL / indeks Pisarka, porównywalne ze skalą Jasnopisu 1–7.
  - Każdy piktogram ma trzy alternatywy do wyboru.
  - Nic nie trafia na stronę bez akceptacji redaktora. Wynik dostaje znacznik „przygotowane z pomocą AI, sprawdzone przez …”.
- **Ograniczenia:**
  - ETR powinien być testowany z odbiorcami. Narzędzie przyspiesza pracę, ale tego testu nie zastępuje.
  - ARASAAC ma licencję **CC BY-NC-SA**, więc przy komercjalizacji potrzebny jest inny zestaw symboli albo umowa.
  - Uproszczenie może zmienić sens prawny. Zabezpieczeniem są strażnik faktów i akceptacja redaktora.
- **Prior art:** w Niemczech działa SUMM AI (Leichte Sprache), co pokazuje, że rynek istnieje. Polskiego odpowiednika z taką warstwą weryfikacji nie znalazłem.
- **Ocena:** Innowacja ★★★★☆ · Wykonalność ★★★★★ · Demo ★★★★☆

#### A2. „Co ten list ode mnie chce?”: pisma urzędowe po ludzku

- **Dla kogo:** seniorzy, cudzoziemcy (UA/EN) i każdy, kto dostał pismo z ZUS, US, sądu albo spółdzielni.
- **Scenariusz demo:** zdjęcie wezwania z urzędu skarbowego zamienia się w kartę:
  - **kto** pisze i **czego** chce,
  - **do kiedy**: termin wyliczony kodem z daty doręczenia, z regułą „dzień wolny → następny dzień roboczy”,
  - **co się stanie**, jeśli nic nie zrobię,
  - **co zrobić** krok po kroku,
  - szkic odpowiedzi i plik `.ics` do kalendarza.
- **Rola AI:**
  - VLM/OCR i ekstrakcja do struktury (structured outputs).
  - LLM objaśnia treść i tłumaczy.
  - RAG po przepisach przywołanych w piśmie: API ELI Sejmu pobiera treść aktu i wskazany artykuł.
  - Lokalna anonimizacja danych osobowych (PESEL, adres) przed wysłaniem do LLM.
- **Weryfikacja i kontrola:**
  - Każde pole karty wskazuje podświetlony fragment skanu.
  - Termin jest liczony kodem z pokazanym wzorem.
  - Przywołany artykuł jest wyświetlany w oryginale z linkiem do ISAP.
  - Przy niskiej pewności system mówi „nie jestem pewien, sprawdź” zamiast zgadywać.
  - Tryb „Czy dobrze zrozumiałem?” zadaje 3 pytania sprawdzające.
- **Ograniczenia:** to nie jest porada prawna. Datę doręczenia podaje użytkownik. Formaty pism są bardzo różne.
- **Ocena:** Innowacja ★★★☆☆ (popularny koncept, wyróżnia go warstwa weryfikacji) · Wykonalność ★★★★☆ · Demo ★★★★★ (każdy juror dostał kiedyś takie pismo)

#### A3. „Klauzula”: czy ta umowa ma zakazane zapisy?

- **Dla kogo:** konsumenci podpisujący umowy najmu, z siłownią, z deweloperem lub z operatorem telekomunikacyjnym. Także rzecznicy konsumentów.
- **Scenariusz demo:** upload PDF umowy deweloperskiej daje listę ryzykownych postanowień oznaczonych kolorami. Przy każdym postanowieniu:
  - najbardziej podobny wpis z **rejestru klauzul niedozwolonych UOKiK** (7 786 postanowień; od 18.04.2026 rejestr działa w wersji zanonimizowanej),
  - wyjaśnienie prostym językiem,
  - podpowiedź, o co zapytać i co renegocjować.
- **Rola AI:**
  - Podział umowy na klauzule.
  - Wyszukiwanie podobnych wpisów: embeddingi (BGE-M3 lub polskie modele `mmlw-*`) i reranker.
  - LLM ocenia, czy podobieństwo jest merytoryczne, a nie tylko leksykalne, i tłumaczy wynik.
  - Klasyfikacja kategorii ryzyka.
- **Weryfikacja i kontrola:**
  - Fragment umowy i wpis z rejestru stoją obok siebie, z wynikiem podobieństwa.
  - Twarde rozróżnienie: **„znaleziono w rejestrze”** (dowód) vs **„AI uważa za ryzykowne”** (opinia, inny kolor).
  - Użytkownik może oznaczyć fałszywy alarm.
- **Ograniczenia:**
  - Wpis w rejestrze dotyczył konkretnej sprawy, więc podobieństwo nie przesądza o abuzywności.
  - Rejestr stoi za ochroną antybotową (Incapsula), a z serwera chmurowego dostaliśmy blokadę. **Dane trzeba pobrać przez przeglądarkę na samym początku.**
  - To nie jest porada prawna.
- **Ocena:** Innowacja ★★★★☆ · Wykonalność ★★★★☆ (ryzykiem jest pozyskanie danych) · Demo ★★★★☆

#### A4. „Sprawdzam”: weryfikator liczb w debacie publicznej

- **Dla kogo:** dziennikarze, nauczyciele WOS, wyborcy.
- **Scenariusz demo:** wypowiedź „bezrobocie w Małopolsce spadło o połowę” albo fragment wypowiedzi z API Sejmu przechodzi przez cztery kroki:
  1. ekstrakcja twierdzeń liczbowych,
  2. dopasowanie do wskaźnika w **GUS BDL**,
  3. wykres serii z naniesionym twierdzeniem,
  4. werdykt: zgodne / częściowo / niezgodne / nieweryfikowalne.
- **Rola AI:**
  - Wykrywanie twierdzeń (claim detection).
  - Agent z narzędziami (szukaj zmiennej BDL, pobierz serię) mapuje twierdzenie na dane.
  - Porównanie liczbowe robi kod, a LLM pisze uzasadnienie.
- **Weryfikacja i kontrola:**
  - Werdykt zawsze ma wykres, ID zmiennej BDL i zapytanie API do skopiowania.
  - Użytkownik może zmienić dopasowaną zmienną albo okres, a werdykt przelicza się na żywo.
- **Ograniczenia:**
  - Tylko twierdzenia liczbowe.
  - Spory definicyjne (np. bezrobocie rejestrowane vs BAEL) wymagają pokazania alternatyw.
  - Temat polityczny wymaga neutralności.
- **Ocena:** Innowacja ★★★★☆ · Wykonalność ★★★☆☆ · Demo ★★★★☆

#### A5. „Wytłumacz mi to”: tutor metodą Feynmana

- **Dla kogo:** studenci i licealiści przed egzaminem.
- **Scenariusz demo:**
  1. Student wgrywa slajdy z wykładu, a aplikacja buduje mapę pojęć.
  2. Student tłumaczy pojęcie głosem „jak pięciolatkowi”.
  3. AI porównuje wyjaśnienie z materiałem, zaznacza na mapie luki i błędne przekonania, dopytuje.
  4. Słabe pojęcia trafiają do harmonogramu powtórek.
- **Rola AI:** ASR (Whisper/Voxtral), LLM budujący graf pojęć z cytatami ze slajdów, ocena wyjaśnienia według rubryki. Powtórki planuje algorytm FSRS, a nie LLM.
- **Weryfikacja i kontrola:**
  - Każda uwaga tutora ma cytat ze slajdu z numerem strony.
  - Student może zakwestionować ocenę („mam rację, bo…”) i dostaje ponowną ocenę z uzasadnieniem.
  - Prowadzący może zatwierdzić mapę pojęć.
- **Ograniczenia:** jakość zależy od materiału. Ocena wyjaśnień jest częściowo subiektywna. Koncept „AI-tutora” jest popularny.
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★★☆ · Demo ★★★★☆

#### A6. „Logobajki”: spersonalizowane bajki do terapii logopedycznej

- **Dla kogo:** logopedzi i rodzice dzieci ćwiczących konkretne głoski (np. „sz”, „r”). Pomysł łączy trzy kierunki z zadania: twórczość, naukę i dostępność.
- **Scenariusz demo:**
  1. Logopedka ustawia parametry: głoska „sz” w nagłosie i śródgłosie, unikać „s” (para opozycyjna), dziecko 5 lat, lubi dinozaury.
  2. Po ok. 20 s dostaje bajkę, ilustracje, nagranie audio i listę wyrazów do powtarzania.
  3. Zatwierdza materiał i wysyła rodzicom link.
- **Rola AI:**
  - LLM generuje tekst pod twarde ograniczenia.
  - **Deterministyczny weryfikator fonetyczny** liczy nasycenie docelową głoską i wykrywa głoski zakazane. Polska ortografia jest w dużej mierze fonetyczna, więc wystarczą reguły grafem → głoska z listą wyjątków, np. „rz” w „marznąć”.
  - Pętla **generuj → sprawdź → popraw** działa, dopóki tekst nie spełni progów.
  - Opcjonalnie ilustracje z generatora obrazów oraz TTS.
- **Weryfikacja i kontrola:**
  - „Mapa głosek”: każde wystąpienie docelowej głoski jest podświetlone na zielono, a zakazanej na czerwono.
  - Licznik, np. „42 × sz, 0 × s”.
  - Wyrazy, których klasyfikacja jest niepewna, są oznaczone do sprawdzenia.
  - Logopeda edytuje i zatwierdza materiał, zanim dostanie go rodzic.
- **Ograniczenia:**
  - Reguły G2P mają wyjątki (upodobnienia, „rz” jako r+z), więc mogą się mylić na brzegach.
  - Obrazy wymagają przeglądu.
  - Narzędzie nie zastępuje terapii, tylko daje materiał do ćwiczeń domowych.
- **Ocena:** Innowacja ★★★★★ · Wykonalność ★★★★☆ · Demo ★★★★★

### B. Wizja komputerowa (CV, VLM)

#### B1. „Próg”: audyt dostępności wejścia ze zdjęcia

- **Dla kogo:** osoby na wózkach, rodzice z wózkami, wolontariusze mapujący OSM, hotele i organizatorzy wydarzeń.
- **Scenariusz demo:**
  1. Ktoś robi dwa zdjęcia wejścia do kawiarni, z kartą płatniczą albo kartką A4 w kadrze jako wzorcem skali.
  2. Aplikacja zwraca wynik: „3 stopnie, wysokość stopnia 14–19 cm, brak podjazdu, drzwi ok. 80–90 cm, nawierzchnia: kostka”.
  3. Aplikacja proponuje tagi OSM (`wheelchair=*`, `step_count=*`, `ramp=*`, `width=*` na węźle `entrance=*`).
  4. Po zatwierdzeniu dane są zapisywane.
  5. Widok dla użytkownika końcowego jest spersonalizowany: „Dla Twojego wózka (szer. 70 cm, maks. próg 2 cm): ❌ 3 stopnie”.
- **Rola AI:**
  - **SAM 3** segmentuje obiekty po opisie tekstowym (np. „stairs”, „ramp”, „door”, „handrail”).
  - **Depth Anything** (głębia z jednego zdjęcia) razem z obiektem referencyjnym daje szacunek wymiarów.
  - VLM opisuje nawierzchnię i przeszkody.
  - Werdykt pod profil potrzeb wydają proste reguły.
  - Twarze i tablice rejestracyjne są automatycznie rozmywane.
- **Weryfikacja i kontrola:**
  - Nakładka segmentacji na zdjęciu pokazuje, co model uznał za stopień.
  - Każdy pomiar ma przedział niepewności.
  - Użytkownik koryguje wartości przed zapisem.
  - Każda informacja ma metadane: źródło (zdjęcie, AI, potwierdzenie przez człowieka), datę i status wiarygodności. Tego samego wymaga partnerskie zadanie „Kraków bez barier”.
- **Ograniczenia:** pomiar z jednego zdjęcia jest przybliżony (± kilka cm), więc trzeba komunikować przedziały, a nie wartości. Na wynik wpływają oświetlenie i kąt.
- **Prior art:** Wheelmap (crowdsourcing dostępności na OSM). „Próg” dodaje do tego pomiar i automatyczne tagowanie.
- **Ocena:** Innowacja ★★★★★ · Wykonalność ★★★☆☆ · Demo ★★★★★ (pomiar na żywo przy wejściu do hali)

#### B2. „Metryka”: czytnik dawnych ksiąg metrykalnych dla genealogów

- **Dla kogo:** osoby szukające przodków (genealogia to duże hobby) i archiwa.
- **Scenariusz demo:**
  1. Skan aktu urodzenia z 1890 r. pobrany z szukajwarchiwach.gov.pl.
  2. Transkrypcja i ekstrakcja pól: dziecko, rodzice, chrzestni, data, miejscowość.
  3. Utworzenie węzła drzewa genealogicznego.
  4. Podpowiedź tej samej osoby w innych aktach.
- **Rola AI:**
  - VLM czyta pismo odręczne. Lepiej ograniczyć się do jednego typu formularza tabelarycznego.
  - Normalizacja wariantów pisowni nazwisk.
  - Dopasowanie osób między aktami (entity resolution).
- **Weryfikacja i kontrola:**
  - Każde pole stoi obok wycinka skanu i ma poziom pewności.
  - Tryb podwójnej transkrypcji (model + człowiek).
- **Ograniczenia:** rękopisy łacińskie i cyrylicą są trudne, a bez wycinków nie ma zaufania do wyniku. W 24 h to duże ryzyko.
- **Ocena:** Innowacja ★★★★★ · Wykonalność ★★☆☆☆ · Demo ★★★★☆

#### B3. „Druga para oczu”: sprawdzanie odręcznych rozwiązań według rubryki

- **Dla kogo:** nauczyciele matematyki i fizyki.
- **Scenariusz demo:** z 25 zdjęć kartkówek aplikacja robi dla każdego ucznia:
  - odczyt kroków rozwiązania,
  - zaznaczenie **pierwszego błędnego kroku**,
  - punkty według rubryki nauczyciela.
  Do tego analiza zbiorcza, np. „60% klasy myli wzór skróconego mnożenia”, i propozycja powtórki.
- **Rola AI:** VLM odczytuje i ocenia kroki. Błędy są grupowane (klasteryzacja).
- **Weryfikacja i kontrola:** nauczyciel zatwierdza każdą ocenę, a błąd jest podświetlony na zdjęciu. AI nigdy samodzielnie nie wystawia oceny końcowej.
- **Ograniczenia:** charakter pisma i zdjęcia w słabym świetle. Ocenianie uczniów jest wrażliwe.
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★☆☆ · Demo ★★★★☆

#### B4. Audytor dostępności cyfrowej z VLM

- **Dla kogo:** podmioty publiczne (obowiązek dostępności cyfrowej, deklaracja dostępności) i agencje webowe.
- **Scenariusz demo:** z adresu URL strony gminy powstaje raport, w którym obok błędów wykrytych przez **axe-core** są problemy, których automaty nie widzą:
  - alt nieopisujący obrazka,
  - link „kliknij tutaj”,
  - wykres bez opisu,
  - tekst wtopiony w grafikę,
  - zbyt trudny język.
  Do każdego problemu jest gotowa poprawka.
- **Rola AI:** Playwright robi zrzuty i pobiera DOM, VLM ocenia zgodność alt z obrazem, LLM generuje poprawki.
- **Weryfikacja i kontrola:** dwie oddzielne sekcje, „wykryte regułą” (pewne) i „sugestie AI” (do oceny). Każdy element ma zrzut z zaznaczeniem i kryterium WCAG. Każdą poprawkę trzeba zaakceptować.
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★★☆ · Demo ★★★☆☆

### C. Dane tabelaryczne i szeregi czasowe

#### C1. „Uczciwa cena”: czy ta cena mieszkania jest rynkowa?

- **Dla kogo:** osoby kupujące mieszkanie na rynku pierwotnym.
- **Scenariusz demo:** z danych oferty (lokalizacja, metraż, piętro, pokoje) aplikacja wylicza:
  - **przedział** ceny rynkowej (np. 13,9–15,6 tys. zł/m²) i miejsce oferty w tym przedziale,
  - 5 najbardziej podobnych ofert na mapie,
  - co podnosi, a co obniża cenę (SHAP),
  - historię ceny tej inwestycji.
- **Rola AI:**
  - **TabPFN-2.5**: model bazowy dla danych tabelarycznych, bez trenowania, do ok. 50 tys. wierszy i 2 tys. cech. Daje regresję i kwantyle.
  - LLM parsuje ogłoszenia z tekstu i wyjaśnia wynik.
- **Dane:** od 2025 r. deweloperzy na mocy ustawy o jawności cen mieszkań codziennie raportują ceny na dane.gov.pl, razem z historią cen.
- **Weryfikacja i kontrola:**
  - Przedział zamiast jednej liczby.
  - Podobne oferty do samodzielnego sprawdzenia.
  - Błąd modelu (MAE) na zbiorze testowym pokazany w UI.
  - Suwaki „co jeśli”.
- **Ograniczenia:**
  - To ceny ofertowe, a nie transakcyjne.
  - Pliki od wielu deweloperów mają różne formaty, więc **ETL jest głównym kosztem**.
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★☆☆ · Demo ★★★★☆

#### C2. „Kiedy wyjść?”: planer aktywności dla osób wrażliwych na smog

- **Dla kogo:** astmatycy, rodzice małych dzieci, biegacze, seniorzy.
- **Scenariusz demo:** zapytanie „1,5 h spaceru z dzieckiem dziś albo jutro, okolice Nowej Huty” zwraca ranking okien czasowych z prognozą PM2.5/PM10 (z pasmem niepewności) i propozycję trasy przez parki.
- **Rola AI:**
  - **Chronos-2** prognozuje zero-shot na danych z API GIOŚ, z kowariatami pogodowymi i kwantylami.
  - LLM jest interfejsem językowym.
  - Progi zdrowotne (np. wytyczne WHO) wyznaczają proste reguły.
- **Weryfikacja i kontrola:**
  - Wykres z pasmem niepewności.
  - Backtest: „wczoraj przewidzieliśmy X, było Y”.
  - Lista stacji źródłowych.
  - Użytkownik ustawia własne progi.
- **Ograniczenia:** stacji jest mało, a lokalna prognoza jest niepewna. Rynek ma już konkurencję (Airly, aplikacja GIOŚ).
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★★☆ · Demo ★★★★☆

#### C3. „Szybciej do specjalisty”: agent kolejek NFZ

- **Dla kogo:** pacjenci ze skierowaniem.
- **Scenariusz demo:** zapytanie „skierowanie do endokrynologa, mieszkam w Bochni, dojadę pociągiem do 1 h” zwraca:
  - ranking placówek według pierwszego wolnego terminu, czasu dojazdu i liczby oczekujących,
  - telefon i godziny rejestracji,
  - podpowiedź, co powiedzieć przy rejestracji.
- **Rola AI:**
  - LLM mapuje opis potrzeby na słownik świadczeń NFZ.
  - Agent wywołuje API terminów leczenia i łączy wyniki z GTFS.
  - LLM wyjaśnia kompromisy.
- **Weryfikacja i kontrola:** każda pozycja ma datę aktualizacji danych NFZ i link do źródła. Użytkownik zmienia wagi (termin vs dojazd). System nie daje rekomendacji medycznych.
- **Ograniczenia:**
  - Placówki raportują dane z opóźnieniem.
  - **API NFZ zablokowało nasze zapytanie z IP chmurowego (Incapsula).** Testujcie z laptopa i cache'ujcie odpowiedzi do demo.
  - Wyszukiwarki terminów już istnieją. Wyróżnikiem byłby ranking wielokryterialny z dojazdem.
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★☆☆ · Demo ★★★☆☆

### D. Mowa i multimodalność

#### D1. „Przećwicz rozmowę”: głosowy trener realnych sytuacji

- **Dla kogo:** cudzoziemcy uczący się polskiego (urząd, lekarz, wynajem) i osoby przed rozmową o pracę.
- **Scenariusz demo:**
  1. Użytkownik wybiera scenariusz „Urząd wojewódzki: przedłużenie karty pobytu”.
  2. Prowadzi rozmowę głosową z wirtualnym urzędnikiem.
  3. Dostaje raport: zrozumiałość, słownictwo, czego zabrakło, wzorcowa wersja rozmowy.
- **Rola AI:** ASR, LLM w roli (persona + ukryty scenariusz z celami) i TTS. Ocena według jawnej rubryki (np. poziomy CEFR).
- **Weryfikacja i kontrola:** transkrypcja z zaznaczonymi fragmentami, których dotyczy feedback. Rubryka jest jawna. Użytkownik może odsłuchać swoje nagranie.
- **Ograniczenia:** opóźnienia w rozmowie na żywo i jakość polskiego TTS.
- **Ocena:** Innowacja ★★★☆☆ · Wykonalność ★★★☆☆ · Demo ★★★★☆

#### D2. „Przed apteką”: leki seniora pod kontrolą

- **Dla kogo:** seniorzy przyjmujący wiele leków (polipragmazja) i ich opiekunowie.
- **Scenariusz demo:**
  1. Zdjęcia 6 opakowań.
  2. Rozpoznanie nazwy (OCR) i kodu EAN.
  3. Dopasowanie do **Rejestru Produktów Leczniczych**.
  4. Wynik: plan dawkowania na dzień, potencjalne interakcje wyciągnięte z ChPL (sekcja 4.5) z cytatem oraz lista pytań do farmaceuty.
- **Rola AI:** VLM/OCR i dekoder kodu kreskowego, RAG po ChPL z cytatami, LLM upraszcza język.
- **Weryfikacja i kontrola:**
  - Każde ostrzeżenie to cytat z ChPL z numerem sekcji.
  - Użytkownik zatwierdza dopasowanie leku (zdjęcie ↔ wpis w rejestrze).
  - System nigdy nie mówi „odstaw lek”, zawsze kieruje do konsultacji.
- **Ograniczenia:**
  - Wysoka stawka (zdrowie), więc trzeba ograniczyć się do „przygotowania do rozmowy”.
  - Rejestr nie ma oficjalnego API: są eksporty XML i nieoficjalne RPL.API.
  - ChPL są w PDF.
- **Ocena:** Innowacja ★★★★☆ · Wykonalność ★★★☆☆ · Demo ★★★★☆

### E. Dzikie karty (krótko)

- **„Tryb cichy”:** mapa obciążenia sensorycznego miejsc (hałas, tłok, światło) dla osób neuroatypowych, szacowana z recenzji, zdjęć i zgłoszeń.
- **„Zapytaj miasto”:** pytania w języku naturalnym do danych miejskich (text-to-SQL / text-to-Overpass). Wygenerowane zapytanie jest zawsze widoczne, a wynik trafia na mapę.
- **„Wypis po ludzku”:** wypis ze szpitala zamieniony w plan zaleceń, słowniczek i przypomnienia, każdy punkt z cytatem z wypisu.
- **„Wniosek bez stresu”:** asystent wypełniania wniosku (np. dofinansowanie PFRON) z checklistą kompletności i listą brakujących załączników.
- **„Dostępna podróż”:** planer tras komunikacją w Krakowie z uwzględnieniem pojazdów niskopodłogowych (GTFS) i dostępności przystanków.
- **„Protokół z zebrania”:** z nagrania zebrania wspólnoty lub rady powstają uchwały, zadania i terminy z linkami do konkretnych momentów nagrania.

---

## 4. Porównanie: szacowany wynik według wag jury

Skala 1–5. Wynik ważony = 0,3·Innowacja + 0,2·Kategoria + 0,2·Użyteczność + 0,2·Design + 0,1·Kompletność. „Design” oznacza potencjał na atrakcyjny UI, a „Kompletność” — szansę na działające demo w 24 h.

| # | Pomysł | Innowacja (30) | Kategoria (20) | Użyteczność (20) | Design (20) | Kompletność (10) | **Wynik** |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| A1 | Łatwo (ETR + piktogramy) | 4 | 5 | 5 | 4 | 5 | **4,5** |
| A6 | Logobajki | 5 | 4 | 4 | 5 | 4 | **4,5** |
| A3 | Klauzula (UOKiK) | 4 | 5 | 4 | 4 | 4 | **4,2** |
| B1 | Próg (wizja → OSM) | 5 | 4 | 4 | 4 | 3 | **4,2** |
| A2 | Co ten list ode mnie chce? | 3 | 5 | 5 | 4 | 4 | **4,1** |
| A5 | Wytłumacz mi to (Feynman) | 3 | 5 | 4 | 4 | 4 | **3,9** |
| B2 | Metryka (genealogia) | 5 | 4 | 3 | 4 | 2 | **3,9** |
| A4 | Sprawdzam (GUS BDL) | 4 | 4 | 3 | 4 | 3 | **3,7** |
| D2 | Przed apteką | 4 | 4 | 4 | 3 | 3 | **3,7** |
| C1 | Uczciwa cena (TabPFN) | 3 | 4 | 4 | 4 | 3 | **3,6** |
| C3 | Szybciej do specjalisty (NFZ) | 3 | 4 | 5 | 3 | 3 | **3,6** |
| D1 | Przećwicz rozmowę | 3 | 4 | 4 | 4 | 3 | **3,6** |
| C2 | Kiedy wyjść? (Chronos-2) | 3 | 3 | 4 | 4 | 4 | **3,5** |
| B4 | Audytor WCAG z VLM | 3 | 4 | 4 | 3 | 4 | **3,5** |
| B3 | Druga para oczu (kartkówki) | 3 | 4 | 4 | 3 | 3 | **3,4** |

### Jak wybrać

- **Najbezpieczniejszy wybór z wysokim sufitem:** **A1 „Łatwo”**. Realny obowiązek prawny, jasny klient (instytucje), jasny odbiorca (osoby z trudnościami w czytaniu). Weryfikacja jest naturalna i bardzo dobrze się pokazuje.
- **Największe „wow” i kreatywność:** **A6 „Logobajki”**. Pętla „LLM generuje, deterministyczny weryfikator sprawdza” to dokładnie ta odpowiedź na pytanie „jak użytkownik weryfikuje wynik”, której szuka jury.
- **Wizja i nietypowość:** **B1 „Próg”**, jeśli w zespole jest osoba od CV. Zbieżność z zadaniem „Kraków bez barier” daje gotowy kontekst i wymagania.
- **Jeśli w zespole jest ktoś z prawa lub ekonomii:** **A3 „Klauzula”**. Twarde dane i najmocniejsze „dowody” przy każdym wyniku.
- A1 i A2 dzielą rdzeń: „silnik upraszczania z kontrolą faktów”. Można go opowiedzieć jako platformę, ale **demo powinno mieć jedną personę i jeden przepływ**.

### Czego unikać

- Ogólnego chatbota „zadaj pytanie dokumentom”. Jury zobaczy takich dziesiątki, więc innowacja wyniesie ok. 1/5.
- Diagnozowania medycznego i porad prawnych podawanych jako wiążące.
- Demo zależnego od scrapowania stron z ochroną antybotową albo od wolnego modelu. Wyniki trzeba **cache'ować** i mieć nagrany fallback.
- Generowania obrazów jako głównej funkcji: jakość, licencje i brak „roli AI w rozwiązaniu problemu”.
- Rozproszenia na wiele funkcji kosztem jednego dopracowanego przepływu, bo Completeness waży tylko 10%.

---

## 5. Modele i narzędzia: ściąga (październik 2026)

| Obszar | Opcje | Uwagi |
|---|---|---|
| **LLM (API)** | Claude Opus 5.5 (`claude-opus-5-5`) jako główny; Claude Sonnet 5.5 (`claude-sonnet-5-5`); Claude Haiku 4.5 (`claude-haiku-4-5`) do taniej klasyfikacji | Wejście PDF i obrazów, *Citations* (pozycja znakowa lub strona), structured outputs (`output_config.format`), tool use do agentów. |
| **Polskie LLM open-weights** | **Bielik** v3 (np. 11B), **PLLuM** (8–70B, wydania 2512) | Argument „suwerenności danych” i pracy offline. Sprawdźcie licencje: część wariantów PLLuM ma licencję CC-BY-NC. |
| **Embeddingi** | BGE-M3 (wielojęzyczny), polskie `mmlw-*` | Do wyszukiwania, dopasowania klauzul i piktogramów. |
| **Wizja** | **SAM 3** (segmentacja po tekście, obrazy i wideo), Depth Anything (głębia z jednego obrazu), YOLO (szybka detekcja), VLM (Claude, Qwen-VL) | SAM 3 pozwala segmentować bez trenowania, np. po opisie „stairs”. |
| **OCR** | VLM; PaddleOCR / Tesseract jako tani fallback | Pisma, opakowania, skany. |
| **Mowa** | ASR: Whisper large-v3 (turbo), Voxtral (Mistral, open); TTS: Piper (offline, ma polskie głosy) lub usługi chmurowe | Rozmowa głosowa na żywo zwiększa ryzyko w demo. |
| **Dane tabelaryczne** | **TabPFN-2.5** (bez trenowania, do ~50 tys. wierszy / 2 tys. cech); baseline LightGBM; SHAP | Szybkie, mocne wyniki i kwantyle. |
| **Szeregi czasowe** | **Chronos-2** (120M, zero-shot, kowariaty, kwantyle), TimesFM 2.5 | Prognoza bez trenowania. |
| **Czytelność PL** | Jasnopis (skala 1–7, aplikacja webowa); lokalnie FOG-PL i indeks Pisarka | Jasnopis nie ma publicznego API, ale wzory FOG/Pisarka łatwo policzyć w kodzie. |
| **Frontend** | React/Next.js + Tailwind + shadcn/ui; mapy: MapLibre + OSM | Streamlit jest szybszy, ale Design waży 20%. |
| **Backend** | FastAPI (Python, bo modele są w Pythonie) | Cache wyników (SQLite/JSON) pod demo. |

---

## 6. Polskie źródła danych przydatne w tym zadaniu

| Źródło | Co zawiera | Dostęp | Uwagi z testu |
|---|---|---|---|
| **dane.gov.pl** | Ponad 40 tys. zbiorów (różne kategorie) | API + SPARQL, bez klucza | Tu są m.in. codzienne raporty cen mieszkań deweloperów. |
| **API ELI Sejmu** (`api.sejm.gov.pl/eli`) | Akty prawne Dz.U. i M.P. od 1918 r., metadane i treść (HTML/PDF) | REST, bez klucza | ✔ Działa z serwera (sprawdzone). |
| **API Sejmu** (`api.sejm.gov.pl`) | Posłowie, posiedzenia, głosowania, wypowiedzi | REST, bez klucza | Do pomysłu A4. |
| **GUS BDL** (`bdl.stat.gov.pl`) | Statystyki regionalne (tysiące zmiennych) | REST | Do pomysłu A4. |
| **NFZ – Terminy leczenia** (`api.nfz.gov.pl/app-itl-api`) | Kolejki, pierwsze wolne terminy, placówki | REST, bez klucza | ⚠ Z IP chmurowego blokada Incapsula. Testujcie z laptopa i cache'ujcie. |
| **Rejestr klauzul niedozwolonych UOKiK** (`rejestr.uokik.gov.pl`) | 7 786 postanowień (od 18.04.2026 zanonimizowane) | Strona WWW | ⚠ Ochrona antybotowa. Pobierzcie dane ręcznie przez przeglądarkę. |
| **Rejestr Produktów Leczniczych** (`rejestrymedyczne.ezdrowie.gov.pl`) | Leki dopuszczone w PL, ChPL, ulotki | Eksporty XML; nieoficjalne RPL.API | Do pomysłu D2. |
| **API GIOŚ** | Pomiary jakości powietrza ze stacji | REST | Do pomysłu C2. |
| **GTFS Kraków (ZTP)** | Rozkłady i pozycje pojazdów | Pliki GTFS / GTFS-RT | Agregator: mkuran.pl/gtfs. |
| **OpenStreetMap / Overpass** | Obiekty, wejścia, tagi dostępności | Overpass API | Do pomysłów B1 i E. |
| **ARASAAC** | Piktogramy AAC z polskimi etykietami | API / pobranie | Licencja CC BY-NC-SA 4.0 (niekomercyjna). |
| **szukajwarchiwach.gov.pl** | Skany akt archiwalnych, w tym metrykalnych | WWW | Do pomysłu B2. |

---

## 7. Plan 24 h i podział ról (do 6 osób)

| Czas | Co robimy |
|---|---|
| 0–1 h | Wybór pomysłu, **jedna persona, jeden scenariusz demo**, zakres MVP spisany w 5 punktach. |
| 1–3 h | Pozyskanie danych (najpierw te z ochroną antybotową!), szkielet frontu i backendu, pierwszy „przechodzący” przepływ end-to-end. |
| 3–12 h | Rdzeń AI (pipeline, prompty, weryfikatory) i główne ekrany UI. |
| 12–16 h | **Warstwa zaufania:** cytaty, strażnik faktów, niepewność, akceptacja. Zbiór 20–30 przykładów testowych i metryka. |
| 16–20 h | Dopracowanie designu, dostępność UI (klawiatura, kontrast), scenariusz demo, cache wyników. |
| 20–22 h | Slajdy (max 10), nagranie demo jako fallback, README z ujawnieniem użytych modeli i danych. |
| 22–24 h | Bufor, testy na czysto, wysłanie zgłoszenia z zapasem. |

**Role:**
- PM / pitch / slajdy
- Design + frontend (×1–2)
- Pipeline AI i prompty
- Dane / backend
- Ewaluacja i QA (zbiór testowy, metryki, sprawdzanie demo)

---

## 8. Szkielet prezentacji (10 slajdów) zmapowany na wymagania zadania

1. **Problem i persona:** konkretna osoba, konkretna sytuacja, skala problemu.
2. **Rozwiązanie w jednym zdaniu** + zrzut ekranu.
3. **Demo w 3 krokach:** wejście → co robi AI → wynik.
4. **Rola AI:** co robi AI, a co kod deterministyczny i dlaczego.
5. **Architektura:** komponenty i przepływ danych.
6. **Decyzje techniczne:** wybór modeli i danych, alternatywy, które odrzuciliśmy.
7. **Weryfikacja i kontrola użytkownika:** zrzuty warstwy zaufania.
8. **Możliwości i ograniczenia + mini-ewaluacja** (liczby!).
9. **Wdrożenie i skalowanie:** kto płaci, jak rozszerzyć na inne miasta lub instytucje.
10. **Ujawnienie użycia AI i zasobów zewnętrznych** + zespół.

---

## 9. Inne zadania HackYeah 2026 powiązane z AI (kontekst i inspiracja)

- **„Kraków bez barier”** (zadanie partnerskie, 5 000 PLN): narzędzie do oceny dostępności miejsc i tras. Wymaga źródła, daty i poziomu wiarygodności każdej informacji, celu WCAG 2.2 AA, filmu do 3 minut i modelu biznesowego. Pokrywa się z pomysłem **B1**.
- **HubMI.pl / ROPS Kraków** (15 000 PLN): platforma Małopolskiego Hubu Innowacji Społecznych. Obowiązkowa funkcja to **AI-owy matchmaking** problemów społecznych z istniejącymi rozwiązaniami.
- **AI Control Layer** (15 000 PLN): warstwa bezpieczeństwa i polityk dla agentów AI (MCP, LLM, API).
- Nie znalazłem informacji, czy jeden projekt można zgłosić do kilku zadań. **Zapytajcie organizatorów na Discordzie, zanim na tym zbudujecie strategię.**

---

## Źródła

- Opis zadania „Artificial Intelligence” i regulamin konkursu (PDF-y z [hackyeah.pl/tasks-prizes](https://hackyeah.pl/tasks-prizes))
- [HackYeah — Wikipedia](https://en.wikipedia.org/wiki/HackYeah)
- [PLLuM: A Family of Polish Large Language Models (arXiv)](https://arxiv.org/pdf/2511.03823) · [PLLuM na Hugging Face](https://huggingface.co/CYFRAGOVPL/Llama-PLLuM-70B-instruct-2512) · [Bielik v3 — tokenizer (arXiv)](https://arxiv.org/pdf/2604.10799) · [Polish LLMs — Cyfronet](https://www.cyfronet.pl/en/projects/initiatives/llm)
- [TabPFN-2.5 (arXiv)](https://arxiv.org/pdf/2511.08667) · [Prior Labs docs](https://docs.priorlabs.ai/models)
- [Chronos-2 (arXiv)](https://arxiv.org/pdf/2510.15821) · [Amazon Science: Chronos-2](https://www.amazon.science/blog/introducing-chronos-2-from-univariate-to-universal-forecasting) · [TimesFM 2.5](https://themenonlab.blog/blog/timesfm-google-time-series-foundation-model)
- [SAM 3: Segment Anything with Concepts](https://cdn.jsdelivr.net/gh/facebookresearch/sam3@main/README.md) · [SAM 3 (arXiv)](https://arxiv.org/html/2511.16719v1)
- [Open-source speech-to-text (Gladia)](https://www.gladia.io/blog/best-open-source-speech-to-text-models) · [Voxtral vs Whisper](https://whispernotes.app/en/blog/introducing-mistral-voxtral-models)
- [dane.gov.pl — data.europa.eu](https://data.europa.eu/en/news-events/news/open-data-portals-around-europe-poland)
- [API ELI Sejmu](https://api.sejm.gov.pl/eli.html) · [API Sejmu](https://api.sejm.gov.pl/API_pl.html)
- [NFZ API Terminy Leczenia](https://www.nfz.gov.pl/o-nfz/programy-i-projekty/projekt-otwarte-dane-dostep-standard-edukacja-api-terminy-leczenia,10.html)
- [Rejestr klauzul niedozwolonych od 18.04.2026 (prawo.pl)](https://www.prawo.pl/biznes/rejestr-klauzul-niedozwolonych-od-18-kwietnia-2026-r,1543119.html)
- [Ustawa o jawności cen mieszkań (PAP)](https://www.pap.pl/aktualnosci/prezydent-podpisal-ustawe-o-jawnosci-cen-mieszkan) · [Obowiązki deweloperów (ongeo)](https://www.blog.ongeo.pl/news-ustawa-o-jawnosci-cen-mieszkan-nowe-obowiazki-deweloperow)
- [ETR i ustawa o dostępności (prawo.pl)](https://www.prawo.pl/samorzad/jak-zapewnic-dostepnosc-osobom-ze-szczegolnymi-potrzebami,504313.html) · [Tekst ustawy (eli.gov.pl)](https://eli.gov.pl/eli/DU/2019/1696/ogl/pol/pdf) · [Prosty język — gov.pl](https://www.gov.pl/web/cyfryzacja/prosty-jezyk)
- [Jasnopis — RMF24](https://www.rmf24.pl/fakty/polska/news-prof-gruszczynski-jasnopis-nie-jest-mozgiem-elektronowym-ale,nId,1884839)
- [ARASAAC — free symbol sets (Commtap)](https://en.commtap.org/additional-resource/free-symbol-sets) · [ARASAAC w Global Symbols](https://globalsymbols.com/symbolsets/arasaac/symbols/46866?locale=en)
- [RPL.API (nieoficjalne API rejestru leków)](https://github.com/SaDa1337/RPL.API)
- [Rozpoznawanie PJM — sign-lang@LREC 2026](https://aclanthology.org/2026.signlang-1.37/)
- [GTFS dla polskich miast (mkuran.pl)](https://mkuran.pl/gtfs/) · [Otwarte dane ZTP Kraków](https://convention.krakow.pl/english/news/284578,2357,komunikat,_bus_stops__bicycle_infrastructure_and_p+r_sites___open_data_published_by_the_public_transport_authority.html)
