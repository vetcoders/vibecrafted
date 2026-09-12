# Compile embargo — odpowiedzialność integratora W2

Embargo oddziela budowę architektury od wykonywalnej weryfikacji. Wynika z fazy
pracy, nie z osobistej zgody ani decyzji konkretnej osoby.

## Workerzy W1/W2

Worker mapuje zależności, implementuje kontrakt cuta, przez inspekcję sprawdza
interfejsy i połączenia oraz zapisuje spójne lokalne checkpointy. Nie uruchamia
kompilacji, buildów, formatterów, lintów, type-checków, testów ani wybiórczych
bramek. Może pisać i czytać testy bez ich wykonywania. Raportuje niewiadome,
ryzyka, brakujące połączenia i potrzebne później sprawdzenia.

Checkpoint może użyć `git commit --no-verify`, gdy hooki uruchamiałyby bramki.
Nie wymaga wcześniejszego uruchomienia wybranych kontroli ani zbudowania polityki
hooków. Obejście pomija całe wejście hooków Gita: trzeba zapisać wszystkie
faktycznie pominięte kontrole, również bezpieczeństwa. Atrybucja i zakres własnych
zmian pozostają obowiązkowe. Checkpoint zachowuje pracę, nie potwierdza jakości ani
bezpieczeństwa i nie uprawnia do pushu, publikacji ani wydania.

## Integrator W2

Integrator weryfikuje dokładne commity i zakresy, składa cuty, sprawdza zależności
oraz kontrakty i usuwa luki strukturalne. Integracja strukturalna może poprzedzać
bramki wykonywalne; pozostaje jawnie niezweryfikowana.

Dopiero integrator zapisuje `W2_STRUCTURALLY_CLOSED` dla dokładnego złożonego SHA.
To znaczy „system złożony i gotowy do sprawdzenia”, nie „system działa”. Następnie
przywraca i uruchamia pełne właściwe bramki, w tym kontrolę bezpieczeństwa i sekretów
pominiętą przez checkpointy, oraz rozdziela konkretne naprawy na podstawie wyników.
Worker sam nie dobiera bramek i nie zdejmuje embarga.

Nieudana bramka po closure wymaga naprawy implementacji, nie osłabienia asercji
ani automatycznego wznowienia embarga. Kolejna faza strukturalna wymaga jawnego
zapisu integratora: zakresu oraz warunku jej zamknięcia.

## Dowody i adaptery repozytoriów

Plan i handoff wskazują fazę, integratora W2, granice cutów, dowody strukturalne,
odroczoną weryfikację i warunek closure. Checkpoint ma SHA, zakres, ryzyka i wykaz
uruchomionych/pominiętych kontroli. Integrator zapisuje złożony SHA, atestację,
wyniki bramek oraz pozostałe scenariusze odbioru. Używamy istniejącego planu,
trackera, dispatchu i journala; w Vibecrafted jest to `.vibecrafted/THE_JOURNAL.md`.

Polityka obejmuje wszystkie języki; repo podaje dokładne komendy. Cztery bramki
historycznego profilu Codescribe nie ograniczają jej zakresu. Marker TOML i hooki
Codescribe są adapterem tego repo, nie potwierdzonym mechanizmem Vibecrafted.
Błędny marker nie rozszerza uprawnień. Po closure i poza strukturalnym W1/W2
obowiązują normalne bramki. Sam zachowany receipt closure nie przedłuża embarga.

## Dostarczenie

Przed wydaniem integrator rozlicza wszystkie wymagane bramki i rzeczywiste
scenariusze produktu dla dokładnej dostarczanej generacji. Podpis, notaryzacja,
instalacja, zachowanie sesji i interakcje użytkownika wymagają właściwych dowodów.
Checkpoint, integracja strukturalna, atestacja ani zielony unit test osobno nie
potwierdzają dostarczenia. Embargo nie rozszerza uprawnień publikacji i release.
