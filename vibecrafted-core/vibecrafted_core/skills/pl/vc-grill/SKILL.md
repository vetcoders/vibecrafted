---
name: vc-grill
version: 1.0.0
description: >
  Interactive design and intent interview protocol before code execution or planning.
  Walks down each branch of the decision tree one question at a time, resolving
  dependencies, stating explicit recommendations, and verifying ground truth in the
  codebase before asking. Trigger: "grill me", "vc-grill", "/grill-me", "zrób mi wywiad",
  "przemagluj mnie", "przemagluj plan", "interview me", "rozbij decyzje".
loctree_value: "code truth exploration before asking questions that the codebase already answers"
aicx_value: "historical decisions, past intents, and recorded preferences"
dogfooding: "recommended before complex architectural changes or ambiguous tasks"
---

# vc-grill — Interaktywny wywiad o intencji i projekcie

## Wywołanie

Wywołaj `/vc-grill` lub poproś agenta o użycie `vc-grill` w bieżącej rozmowie.
Prowadź wywiad interaktywnie z Founderem w tej sesji. Ten skill
nie ma launchera CLI, aliasu powłoki ani trybu workera headless.

Używaj go na prośbę lub gdy nieustalona intencja, wybory projektowe albo kompromisy
architektoniczne wymagają wspólnej decyzji. To nie jest obowiązkowy etap każdego
zadania. Znany błąd z jasną naprawą i kryteriami akceptacji można naprawić w ramach
istniejącego upoważnienia bez wywiadu.

## Cel

Osiągnij wspólne, jednoznaczne zrozumienie wyborów architektonicznych,
funkcjonalnych i UX zadania. Przejdź przez każdą istotną gałąź drzewa projektu,
rozwiązując zależności między decyzjami pojedynczo. Fakty zbadaj samodzielnie;
Foundera pytaj o intencje i kompromisy.

## Canonical Orientation Gate

Przy pracy w repozytorium wykonaj `vc-init` lub wykorzystaj jego aktualne dowody
przed pierwszym pytaniem. Użyj `Loctree:loctree`, by zbadać
Code-Derived Application Map: `context`, `focus`, `slice`, `impact`, `find`
i `follow`. Przez AICX odzyskaj wcześniejsze decyzje, intencje i preferencje;
twierdzenia historyczne zweryfikuj w aktualnym kodzie. Wykorzystuj świeże dowody
i odświeżaj konkretną powierzchnię, która uległa zmianie.

Przeczytaj konfigurację, schematy, miejsca wywołań i istotną historię commitów,
zanim zapytasz o obecne zachowanie. Nigdy nie pytaj Foundera, gdzie leży plik
lub jak obecnie działa funkcja, jeżeli repozytorium daje odpowiedź. Przy zadaniu
bez repozytorium zbadaj dostarczone materiały i wskaż brakującą intencję.

## Zasady wywiadu

1. **Najpierw Loctree & AICX.** Ustal prawdę w kodzie przed pytaniem. Pytania
   dotyczą intencji biznesowej, preferencji projektowych, kompromisów
   architektonicznych i nieustalonych zachowań brzegowych. Ujawniaj konflikt
   między zapisaną intencją a dowodami z runtime'u zamiast wybierać po cichu.
2. **Dokładnie jedno pytanie na raz.** Pytaj o jeden węzeł decyzyjny i czekaj
   na odpowiedź przed wejściem w zależną gałąź. Nie wysyłaj kwestionariusza ani
   kilku pytań w jednym wywołaniu narzędzia. Brak odpowiedzi nie oznacza zgody.
3. **Zawsze z rekomendacją.** Każde pytanie zawiera opcje i ich koszty,
   a następnie jawną rekomendację agenta z konkretnym uzasadnieniem.
   Podpisuj własne wnioski; wyłącznie Founder podejmuje decyzje Foundera.
4. **Przechodź po zależnościach.** Zacznij od fundamentów, potem przejdź do
   szczegółów. Śledź rozstrzygnięte i otwarte węzły oraz ich zależności. Wróć
   do wcześniejszego węzła, gdy późniejsza odpowiedź zmieni jego założenia;
   wyjaśnij tę zależność.
5. **Używaj języka odkrywania Foundera.** Polski jest mile widziany. Ustal
   tezę i wymagane dowody przed tłumaczeniem na język materiałów wdrożeniowych.

## Drzewo projektu

Odwiedź istotne gałęzie w tej kolejności, pomijając decyzje już rozstrzygnięte
aktualnymi dowodami lub jawną odpowiedzią Foundera:

1. **Zakres i granice:** efekt dla użytkownika, najmniejszy użyteczny zakres
   zmiany oraz jawne wyłączenia.
2. **Architektura i model danych:** własność stanu, kontrakty, trwałość danych
   i istniejące rozwiązania warte ponownego użycia.
3. **Runtime i integracja:** punkty wejścia, wywołujący, środowisko wykonania,
   odpowiedzialność za integrację i propagacja błędów.
4. **Przypadki brzegowe i awarie:** niedostępne usługi, uprawnienia, wyścigi,
   przerwana praca i sposób odzyskiwania.
5. **Weryfikacja i DoU:** obserwowalna akceptacja, sensowne testy oraz dowody
   produktowe potrzebne, by rezultat dało się znaleźć, wypróbować, zrozumieć,
   obdarzyć zaufaniem i dostarczyć.

Nie wymyślaj niepewności, by wypełnić drzewo. Wybierz następny nierozstrzygnięty
węzeł, którego odpowiedź odblokuje najwięcej zależnych decyzji.

## Protokół kolejnych tur

Przed pierwszym pytaniem zbadaj dostępne dowody i wskaż główną niejednoznaczność.
W każdej turze:

- Nazwij bieżący węzeł i krótko wyjaśnij, dlaczego wybór ma znaczenie.
- Przedstaw istotne opcje, po jednej korzyści i jednym koszcie każdej.
- Zapisz **Moja rekomendacja (agent):** wybraną opcję i uzasadnienie.
- Zadaj jedno bezpośrednie pytanie o tę decyzję, a potem czekaj.
- Zapisz odpowiedź z jej źródłem i zaktualizuj pozostałe drzewo.

Jeśli jest dostępne narzędzie do pytań, może przedstawić tę jedną decyzję.
W przeciwnym razie zapytaj na czacie. Dostępność narzędzi nie uzasadnia łączenia
pytań ani przenoszenia wywiadu do zadania w tle.

## Warunek zakończenia i Design Contract

Zakończ, gdy istotne gałęzie nie mają nierozstrzygniętych zależności blokujących
ani ukrytych założeń implementacyjnych. Jeśli Founder zakończy wywiad wcześniej,
oznacz kontrakt jako częściowy i ujawnij otwarte decyzje; nie wymyślaj odpowiedzi.

Zwróć w rozmowie zwięzły **Design Contract**, gotowy do wykorzystania w planie
implementacji lub `vc-dou`:

- **Cel i zakres:** oczekiwany rezultat i jawne granice.
- **Decyzje:** uzgodnione wybory, odrzucone alternatywy, powody oraz wskazanie
  źródła: odpowiedź Foundera, dowód z kodu albo propozycja agenta.
- **Architektura i runtime:** własność stanu i kontraktów, punkty wejścia,
  ścieżka integracji oraz zachowanie przy awarii i odzyskiwaniu.
- **Akceptacja i DoU:** konkretne obserwowalne rezultaty, kroki weryfikacji
  i wymagane dowody produktowe.
- **Otwarte kwestie:** jawnie odroczone pytania, ich wpływ i właściciel;
  pytania blokujące utrzymują częściowy status kontraktu.

Wywiad daje kontrakt. Implementacja wynika z zakresu upoważnienia od użytkownika;
samo zakończenie wywiadu nie uprawnia do edycji ani publikacji.

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
