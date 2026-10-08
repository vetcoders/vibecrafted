---
name: forensics
version: 1.1.0
description: >
  Investigate product failures through real execution paths, falsify root-cause
  hypotheses and coordinate independently verified repairs. Produces a portable
  evidence notebook joining Loctree, PRView and manual findings.
aliases:
  - vc-forensics
compatibility:
  tools:
    - loctree-mcp
    - aicx-mcp
    - loct(cli)
    - aicx(cli)
    - vc-git(cli)
    - run_command
    - view_file
    - replace_file_content
    - write_to_file
requires:
  - vc-init
  - loctree
  - aicx
loctree_value: "primary anatomical map for structural inspection, blast radius, and absence falsification"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
metadata:
  short-description: Execution-path investigation and independently verified repairs.
  trigger phrases:
    - "run forensics"
    - "vc-forensics"
    - "forensic preflight"
    - "zbadaj i napraw bugi"
    - "znajdź wyścigi i wycieki"
    - "wyeliminuj wielowładzę"
---

# vc-forensics — śledztwo z niezależnym dowodem

## Trasy i zakres

- `/vc-forensics` działa w tej sesji.
- `vibecrafted workflow <agent> --file <brief.md>` wysyła ograniczony brief,
  który jawnie ładuje ten skill. Dedykowanej komendy `vibecrafted forensics`
  nie ma; instalacja skilla nie rejestruje nowego verba CLI.
- Przestrzegaj bieżącego repo, runtime, wskazanych ścieżek i uprawnień.
  Sama diagnoza nie daje zgody na naprawę, capture, instalację ani deployment.
  Gdy zlecono śledztwo i naprawę, doprowadź upoważnioną pracę do niezależnej
  weryfikacji i integracji. Zachowaj jawną regułę grupowania dispatchy Foundera;
  nie zamieniaj jej w domyślne wymaganie dla każdego użycia skilla.

## Canonical Orientation Gate

Wykorzystaj świeże dowody `vc-init` dla badanego repo; odśwież tylko to, co się
zmieniło. `Loctree:loctree` daje Code-Derived Application Map: wejścia runtime,
zależności, właścicieli i blast radius. Slice przed edycją, impact przed usuwaniem,
find przed nowym symbolem. Zero konsumentów to kandydat, nie dowód do usunięcia:
sprawdź pokrycie skanu i rzeczywiste wywołania. Przy niepełnej mapie zapisz ograniczenie
i zejdź do ograniczonego odczytu źródeł. AICX odtwarza intencje; rozróżniaj decyzje
Foundera od propozycji agentów i konfrontuj historię z aktualnym kodem i runtime.

## Śledź awarię, nie zestaw testów

1. Przypnij root, branch, pełny SHA, własność dirty changes oraz wersję zgłoszonego
   artefaktu lub procesu. Źródło, build, instalka i proces mogą być z różnych generacji.
2. Zapisz trigger, oczekiwane zachowanie i objaw. Przejdź przez prawdziwych callerów:
   wejście → mutacja stanu → zatwierdzony wynik → oddanie → cleanup. Sprawdź spóźnione
   odpowiedzi, stop/drain, zmiany trybu i współbieżność. Podgląd nie dowodzi zatwierdzenia
   ani oddania danych; zachowaj relację pochodzenia przez każdą granicę. Receipt nie
   dowodzi poprawnego tekstu u odbiorcy.
3. Spróbuj obalić diagnozę najmocniejszym alternatywnym wyjaśnieniem: legalny podział
   trybów, stara instalka, inne wejście, brak fixtury, błąd instrumentacji lub brak
   pokrycia skanu. Zachowaj obalone hipotezy, nie licz ich jako kolejne bugi.
   Same zduplikowane symbole nie dowodzą wielowładzy.
4. Nazwij najmniejszy udowodniony mechanizm. Oddziel hipotezy, potwierdzone błędy,
   naprawy źródłowe, weryfikację, integrację i akceptację na żywo.
   Szczegóły: [kontrakt dowodowy](references/forensics-evidence-spec.md).

Dla wydajności licz rzeczywistą kosztowną pracę i ogranicz zakres dotknięty zmianą;
osobno zmierz opóźnienie i zasoby na wskazanym urządzeniu i workloadzie. Mniej wywołań
lub zielone testy nie dowodzą niższej temperatury. Porównuj identyczne wejścia i dodaj
kontrole pozytywne: naprawa musi zachować legalną edycję, późne dowody, oddanie końca
i pochodzenie danych, nie tylko wyciszyć wadliwe zdarzenie.

## Badacz, worker i integrator

Przestrzegaj ról repo. Przy source-only workerach i embargo kompilacji badacz mapuje
i zawęża wady, worker pisze tylko swój cut źródłowy, a integrator posiada niezależne
testy odbiorcze, buildy i integrację. Worker nie uruchamia bramek i nie czyta prywatnych
fixtur. Nie wdrażaj osobiście wykrytej naprawy, gdy aktualny kontrakt Operatora wymaga
dispatchu. Poza tym kontraktem upoważniona naprawa przez jednego agenta jest poprawna.

- Integrator projektuje testy z kontraktu produktu i surowych dowodów, nie z kodu workera.
  Brief określa zachowanie, bez prywatnych testów. Brak zależności i błąd kompilacji
  nie są zamierzonym behawioralnym RED; liczba wybranych testów musi być niezerowa.
- Ten sam przypadek uruchom na przypiętym baseline i kandydacie. Dodaj kontrole legalnego
  sąsiedniego zachowania. Zapisz logi, liczby wybranych testów i SHA.
- Raport końcowy workera lub sukces providera dowodzi dostarczenia, nie integracji.
  Zapisz klasę runtime, parent/effective roots, baseline branch/SHA, worker branch,
  terminal tip i raport. Sprawdź drzewo docelowe niezależnie przez dokładne ancestry,
  merge parent lub jawną równoważność patcha.
- Timeout await/observe nie oznacza końca workera. Wznów ten sam run; zweryfikuj
  tożsamość przed ponowieniem, restartem lub uruchomieniem zastępstwa.

## Naprawa i odbiór

Wybierz spójną naprawę przyczyny. Przy dwóch żywych władzach usuń lub przepnij
nieuprawnionego właściciela zamiast dokładać synchronizację. Martwe tracked files
usuwaj przez `git rm` po sprawdzeniu impact i callerów. Nie wymuszaj przepisywania,
usuwania lub zakazów słownictwa, gdy wystarczy ograniczona naprawa. Gdy wybór wymaga
nierozstrzygniętej decyzji produktowej, pokaż dowody i poproś o tę decyzję, kontynuując
niezależną pracę.

Właściwe bramki bezpieczeństwa, lint i testy uruchamia wyznaczony integrator.
Rozróżnij nowe porażki, błędy czystego baseline i cudze równoległe zmiany.
Stage tylko własnych plików/hunków. Commit oznacza postęp źródłowy; integracja,
instalacja i rzeczywisty odbiór mają osobne receipty. Właściwy kontrakt instalacji
repo stosuj w ramach obecnego upoważnienia, zachowaj aktywne sesje i sprawdź tożsamość
artefaktu/procesu przed dźwiękiem sukcesu. Zapisz wymagany odbiór, który pozostaje otwarty;
nie ogłaszaj ukończenia na podstawie samych testów źródłowych.

## Trwały handoff i notebook

Operator dopisuje istotne decyzje do `<repo-root>/.vibecrafted/THE_JOURNAL.md`
(prywatny, ignorowany przez Git). Worker oddaje raport Operatorowi, nie tworzy
kolejnego dziennika. Użyj [szablonu wpisu](references/forensics-journal-template.md).
Przed zmianą właściciela zapisz root/SHA/status, własne zmiany, bramki, znane błędy,
aktywne handle runów, pozostały odbiór i dokładną następną instrukcję.

Zbuduj przenośny HTML łączący rzeczywiste JSON Loctree/PRView i ręcznie rozstrzygnięte
findings. Przeczytaj [kontrakt notebooka](references/report-notebook.md) i uruchom
`scripts/render_report.py` z katalogu skilla. Brak wejścia pozostaje NOT_ASSESSED;
sygnał narzędzia nie staje się automatycznie potwierdzonym błędem. HTML jest projekcją
importowanych receiptów, nie kolejnym control plane ani silnikiem weryfikacji.
Ręczne zmiany wymagają eksportu JSON; regeneracja nie zachowuje sama stanu przeglądarki.

Raport końcowy krótko wskazuje trigger, przyczynę i dowody, SHA naprawy/integracji,
faktyczną weryfikację, pozostały odbiór instalacji/runtime oraz linki do artefaktów.
