---
name: what_is_vibecrafted_pl
version: 2.0.0
description: >
  A convergence framework for AI-native software development.
  Architecture manifesto (Polish version).
---

# Vibecrafted. — Manifest Architektury

## Definicja

**Vibecrafted.** to framework konwergencji dla AI-assisted software development, zbudowany przez Vetcoders.
Nie służy jedynie do generowania kodu. To **system**, w którym kod pisany przez agentów AI jest systematycznie doprowadzany do jakości produkcyjnej — poprzez naprzemienne kroki **percepcji** i **akcji**, strukturalne narzędzia analityczne i multi-agentową orkiestrację.

## Rdzeń Filozoficzny

Największym ograniczeniem AI w programowaniu nie jest brak sztucznej "inteligencji", lecz rosnąca wraz ze wzrostem wielkości projektu entropia i halucynacje.

Zamiast żądać od modeli bezbłędnego kodu przy pierwszym podejściu, architektura Vibecrafted. opiera się na **procesie**. Zadaje agentom bezlitosne pytanie: _"Co jest jeszcze źle?!"_ — a następnie zmusza ich do rygorystycznej weryfikacji własnej pracy w pętli opierającej się na kontrprzykładach (counterexample convergence), aż do ostatecznego zamknięcia pętli jakościowej.

Counterexample convergence nie jest jednym krokiem ani jednym narzędziem. Jest **emergent property** całego pipeline'u — kolejne narzędzia są ułożone w rytm: percepcja, akcja, percepcja, akcja. Żaden krok WRITE nie odbywa się bez poprzedzającego go kroku READ-ONLY. Żaden krok READ-ONLY nie kończy się bez przekazania verdykty następnemu krokowi WRITE. To jest rdzeń.

## Geneza (Origin Story)

Wszystko zaczęło się od dwojga lekarzy weterynarii z Polski, biorących udział w kursie na Harvard Medical School ("AI in Health Care: From Strategies to Implementation"). Postanowili zbudować zaawansowaną aplikację wspierającą lekarzy weterynarii w ulepszaniu i interpretowaniu obrazów USG przy użyciu AI. Nie mieli doświadczenia w programowaniu. Zamiast uczyć się kodować od zera, zaczęli po prostu delegować zadania do agentów AI.

Baza kodu z czasem rozrosła się na tyle, że ci agenci przestali rozumieć szerszy kontekst. Zamiast rozwijać produkt, niszczyli go — dopisując zbędne łatki po to, by ratować już raz zaimplementowane funkcjonalności. Wtedy czysta upartość wymusiła na nich sformułowanie nowej, rygorystycznej metodologii.

Gdy bezwzględny system zasad iteracyjnej weryfikacji zaczął sprawnie wykorzystywać modele AI, nowa metoda wymknęła się ramom "zwykłego narzędzia". Stała się spójnym frameworkiem.

**Frameworkiem, który z czasem ugruntował się na tyle mocno, że w końcu napisał, ulepszył i zbudował samego siebie.**

## Pipeline (Sculpting Pattern)

Vibecrafted. działa jak rzeźbiarz pracujący w kamieniu: najpierw zarysowuje bryłę, potem przykłada dłuto raz po raz, sprawdzając kąt po każdym uderzeniu. Każdy ruch dłuta jest **akcją** (WRITE). Każde przyłożenie oka do bryły jest **percepcją** (READ-ONLY). Bez obu kroków rzeźba nie powstaje — z samych ruchów dłuta wychodzi gruz, z samego patrzenia wychodzi blok niezmienionego kamienia.

```
[scaffold]            WRITE    stwórz ramę i dokumentację projektową
    ↓
[workflow / implement] WRITE    zaimplementuj (workflow = zorganizowany;
                                implement = daily-driver, jeden agent-złota-rączka)
    ↓
[followup]            READ      oceń trajektorię — czy idzie w dobrym kierunku
    ↓
[review]              READ      oceniaj każdą z implementacji — findings-max,
                                żadnych modyfikacji kodu
    ↓
[marbles]             WRITE     meta-implementacja swarmem — tynk na wszystkie
                                pęknięcia w totalnym nadmiarze, celowe over-write
    ↓
[audit]               READ      zweryfikuj że wybrana prawda faktycznie wylądowała
                                — falsyfikacja, default UNVERIFIED, PASS wyrobiony
    ↓
[polarize]            WRITE     wybierz jedną prawdę, odrzuć resztę — decisive cut
                                strząsa nadmiar marbles, zostaje jedna oś
    ↓
[dou]                 READ      zmierz dystans do shipowalnego stanu
    ↓
[hydrate]             WRITE     wypoleruj surface'y
    ↓
[decorate]            WRITE     pomaluj — wizualna warstwa marki
    ↓
[release]             WRITE     pakuj stragan i jedź na bazar sprzedać
```

**Reguła rytmu:** każdy WRITE jest okolony READ-ONLY krokami przed i po. Agent nie pisze na ślepo, agent nie patrzy bez akcji. Naruszenie rytmu (skip READ przed WRITE, skip WRITE po READ) jest naruszeniem rdzenia.

**Carve-from-marble pattern w środku pipeline'u:** marbles celowo over-applyuje fixy (nadmiar tynku w każdym pęknięciu, nawet tym które może nie powinno być wypełnione), audit weryfikuje co wylądowało, polarize decisive-cut strząsa nadmiar i wybiera jedną prawdę. Nadmiar marbles **jest celowy** — pojawia się żeby było co strząsać.

## Ontologia Narzędziowa

| Warstwa                   | Narzędzie                              | Tryb      | Zasada Działania                                                                                                                                          |
| ------------------------- | -------------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Zmysły**                | Loctree                                | READ      | Strukturalna analiza codebase — martwy kod, cykle, zależności, blast radius. Agent nie zgaduje architektury — czyta ją.                                   |
| **Orientacja**            | vc-init                                | READ      | Trzy bazy przed działaniem: pamięć (co zrobiono), wzrok (stan kodu teraz), prawda (czy kompilator i linty działają).                                      |
| **Kreacja**               | vc-scaffold, vc-workflow, vc-implement | WRITE     | Postawienie ramy projektu, ułożenie pipeline'u implementacyjnego, codzienna implementacja jednym agentem-złotą-rączką.                                    |
| **Percepcja (Jakość)**    | vc-followup, vc-review                 | READ-ONLY | Followup ocenia trajektorię całej linii. Review ocenia każdą indywidualną implementację — findings-max, zero modyfikacji.                                 |
| **Eksploracja**           | vc-marbles                             | WRITE     | Meta-implementacja swarmem. Tynk na wszystkie pęknięcia w **totalnym nadmiarze**. Nadmiar jest celowy — pojawia się żeby było co strząsać.                |
| **Falsyfikacja (Jakość)** | vc-audit                               | READ-ONLY | Per-task verdict matrix po marbles. Default UNVERIFIED, PASS wyrobiony evidencją. Sprawdza czy wybrana prawda faktycznie wylądowała w kodzie.             |
| **Konwergencja**          | vc-polarize                            | WRITE     | Decisive cut. Strząsa nadmiar marbles, wybiera jedną oś prawdy, odrzuca konkurujące surface'y. Emituje DoU/release handoff.                               |
| **Pomiar gotowości**      | vc-dou                                 | READ-ONLY | Definition of Undone — mierzy dystans dzielący projekt od wejścia na produkcję. Audit gotowości shippingowej.                                             |
| **Dystrybucja**           | vc-hydrate, vc-decorate, vc-release    | WRITE     | Polerowanie surface'ów, warstwa wizualna marki, pakowanie i wypchnięcie na bazar.                                                                         |
| **Orkiestracja**          | vc-agents, vc-operator                 | meta      | Wywoływanie agentów w tle (vc-agents) lub prowadzenie fleet'u w trybie operatora (vc-operator), z zachowaniem raportowania, transkryptów i logiki `aicx`. |
| **Bezpieczeństwo**        | rust-ai-locker                         | infra     | Rozdzielenie procesów budowania — gwarantuje, że agenci asynchroniczni nie crashują maszyny jednoczesną ciężką kompilacją.                                |

**Warstwa "Jakość" jest podzielona na percepcję (review + followup, READ) i falsyfikację (audit, READ).** Oba są READ-ONLY. Oba produkują verdict + findings + report. Żaden z nich nie modyfikuje kodu. Modyfikacja kodu jest zarezerwowana dla marbles (over-write) i polarize (decisive cut).

## Dowód Koncepcji (Proof of Concept)

Vibecrafted. jest żywym dowodem na własną poprawność. Pełen framework zaprojektował dla siebie skille, mechanizmy działania, CI (Continuous Integration), installer oraz landing page. Każda zmiana realizowana za pomocą tego systemu posiada konkretny "evidence chain" — agent najpierw znajduje powód odrzucenia (counterexample), a potem potrafi empirycznie udowodnić spójność.

Framework w wersji 2.0.0 sam przeszedł własny pipeline: marbles wcisnęły nadmiar pomysłów w taksonomię skilli, audit zweryfikował co realnie wylądowało, polarize wybrał jedną oś (READ-ONLY vs WRITE) i odrzucił konkurujące framing'i. Manifest który czytasz jest output'em tego cyklu.

_Produced with ⚒🅅·🄸·🄱·🄴·🄲·🅡·🄰·🄵·🅃·🄴·🄳·_
