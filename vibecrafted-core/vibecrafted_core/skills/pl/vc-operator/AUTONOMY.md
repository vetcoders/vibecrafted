# vc-operator — AUTONOMY: polityka hard-stop „Wystarczy wcisnąć guzik"

> Tryb operatora wymienia swobodę (autorytet wielofalowego dispatchu) na
> dyscyplinę (jasne punkty stopu, nigdy nieodwracalne działanie). Ten plik
> kodyfikuje harmonogram stopów.

Czytaj razem z [`SKILL.md`](SKILL.md), [`FRAME.md`](FRAME.md), [`AWAIT.md`](AWAIT.md).

---

## Doktryna w dwóch zdaniach

> _„Tryb operatora to zgoda na usunięcie tarcia oraz przejęcie prowadzenia i autorytetu
> decyzyjnego podczas długich lub rozległych zadań, gdzie cel jest dobrze zdefiniowany.
> Liderzy nie wykonują całej pracy — koordynują i nadzorują pracę wykonywaną przez swój
> zespół."_

---

## Hard-stopy — nie do negocjacji

Jeśli operator nie zezwoli na to jawnie w spisanym planie albo nie ustali i nie udokumentuje
tego w bieżącej sesji, **nie wolno ci** wykonać żadnej z tych rzeczy:

### Powierzchnia git

- `git reset --hard`
- `git revert`
- `git push --force` / `--force-with-lease`
- `git merge` do trunka (`develop`, `main`, cokolwiek operator traktuje jako trunk)
- `gh pr merge` / `gh pr close`
- `git tag -d` / `git push --delete`
- `git rebase --interactive`, `git reset --hard`, `git stash drop`
- usunięcie gałęzi (lokalnej lub zdalnej)

### Powierzchnie zewnętrzne

- komentarze, review, akceptacje na `gh issue` / `gh pr`
- publikacja na Slacku, Discordzie, e-mailu, w mediach publicznych, na blogu
- wyzwalanie deployów (`fly deploy`, `vercel deploy`, `cargo publish`, npm publish)
- zmiany DNS, provisioning certyfikatów

### Zaufanie / bezpieczeństwo / rozliczenia

- dodawanie / usuwanie współpracowników w jakimkolwiek repo lub organizacji
- modyfikowanie sekretów CI, zmiennych środowiskowych, deploy keys
- modyfikowanie konfiguracji auth / billingu w jakimkolwiek serwisie produkcyjnym
- edytowanie plików `.env*` w jakikolwiek sposób, który ujawnia sekrety
- pomijanie bramek bezpieczeństwa (`--no-verify`, `--no-gpg-sign`) — nawet jeśli hook padnie

### Powierzchnia skilli / konwencji

- edytowanie globalnego `~/.claude/CLAUDE.md` lub równoważnych plików karty agenta
- edytowanie lub usuwanie innych skilli w `vibecrafted/skills/`
- ubijanie lub podmiana skilla bez jawnej decyzji operatora —
  rekomenduj, nie działaj

---

## Dozwolenia z notatką w Dzienniku Operatora

**Możesz** wykonać każdą z tych rzeczy, o ile nie zmienia to finalnego celu:

- zmiana kształtu dispatchu w trakcie planu (np. awansowanie łańcucha Fali B na
  równoległą Falę C ze względu na przyspieszenie)
- pomijanie, dodawanie lub przestawianie promptów w planie, bo warunki się zmieniły
  albo fala ujawniła brakujący slice
- cherry-pickowanie z innej gałęzi do aktywnego łańcucha fali

## Przy wszystkich tych dozwoleniach musisz odnotować w Dzienniku Operatora, co się zmieniło, co zostało pominięte, co dodane, co przestawione lub cherry-pickowane i dlaczego.

## Wolne ruchy — bez zgody

Wewnątrz trybu operatora możesz swobodnie:

- czytać dowolny plik w drzewie roboczym (i dowolny lokalny katalog artefaktów)
- uruchamiać dowolną komendę bramki (`pnpm run check`, `cargo test`, `pytest`, `make`)
- odpalać prompty z planu przez launcher frameworka
- pisać raporty + wpisy zamknięcia + zalążki backlogu
- aktualizować tracker fal
- spawnować natywne subagenty (Task tool, `vc-delegate`) do równoległego zwiadu lub
  małego bounded researchu wewnątrz slice'a
- commitować pracę, której _ty_ jesteś osobistym autorem (raporty zamknięcia, wpisy
  backlogu, aktualizacje doktryny) we własnej atrybucji
- niedestruktywny remote push bieżącej gałęzi feature
  (`git push`, `git push -u origin HEAD`, `git push origin <feat/…>`).
  Po commicie, którego jesteś autorem, to obowiązek, nie uprzejmość.
  Force, `--delete`, `--mirror`/`--all`/`--tags`, dest-to-trunk (`main` /
  `master` / `develop` / `trunk` / `release/*` / tagi wersji) zostają
  przyciskami.
- czytać ekstrakty sesji, raporty artefaktów, logi gita
- planować pobudki heartbeat do śledzenia await
- anulować zacięty task w tle i zastąpić go dispatchem odzyskiwania

---

## Bugi produktowe daily-toolingu zamyka zainstalowana binarka

Gdy cięcie to bug produktowy w daily-dev toolingu, którego jesteśmy właścicielami
(`aicx`, `loct` / `loctree-mcp`, deck/CLI `vibecrafted`, resume, doctor, nasze
serwery MCP):

1. Zbuduj z tego checkoutu.
2. Odpal prawdziwe testy na **tym** buildzie, nie tylko unit comfort.
3. Zainstaluj **ten sam build** na PATH operatora (`make install-bin`,
   `make install-python-tools` albo kanoniczny target instalacyjny repo).
4. Udowodnij, że zainstalowana binarka to ta, którą właśnie przetestowałeś
   (`command -v`, `--version` / SHA z receiptu).

Zostawienie poprawki w `target/debug`, podczas gdy `~/.cargo/bin` albo
tools-home nadal serwuje wczorajszą binarkę, to to samo co nie dowieźć.
Raport „resume dalej zobaczy `scanned=0`, dopóki ktoś nie zainstaluje" jest
niedokończoną pracą.

Nie odpalaj host-wide `make install` na brudnym Living Tree, które niesie
niezacommitowaną pracę kogoś innego. Zainstaluj powierzchnię, którą naprawiłeś;
cudzych brudnych plików nie ruszaj.

---

## Handoff w punkcie stopu

Gdy plan dojdzie do przycisku, napisz handoff w punkcie stopu. Kształt
(wg [`EMIL.md`](EMIL.md) — dyscyplina checkboxów + numerowane sekcje):

````markdown
## Punkt stopu — „finalny cel osiągnięty, zostało tylko wciśnięcie guzika"

### 1) Stan (1:1)

<jedno zdanie opisujące, co jest gotowe, w głosie operatora, jeśli wyprowadzone z
jego deklaracji>

### 2) Co wylądowało

- [x] Fala A — `<sha>` na `<branch>`
- [x] Fala B — `<sha-1>` `<sha-2>` `<sha-3>` `<sha-4>`
- [x] Fala C — `<sha-c1>` `<sha-c2>` `<sha-c3>`
- [x] Fala D — `<sha-d1>` `<sha-d2>`

### 3) Co jest zweryfikowane

- [x] Bramki zielone we wszystkich raportach workerów
- [x] e2e check uruchomiony w `<browser/themes/etc.>`
- [x] Wpis zamknięcia backlogu napisany: `<path>`

### 4) Co NIE jest zrobione (celowo)

- [ ] Utworzenie PR / merge do `<trunk>`
- [ ] `<jakiekolwiek inne działanie po stronie operatora>`

### 5) Jednokrokowe wciśnięcie guzika

```bash
gh pr create --base develop --title "..." --body-file ...
```

### 6) Otwarte ryzyka (warte spojrzenia przed wciśnięciem)

- `<cokolwiek, co mogłoby zaskoczyć reviewera>`
````

Operator czyta sekcję 4 i wciska odpowiednie guziki. Każde wciśnięcie guzika
przerzuca `[ ]` na `[x]` w oczach operatora, nawet jeśli sam plik nigdy nie
zostanie ponownie wyedytowany. Konwencja służy ludzkiemu skanowaniu.

---

## Co naprawdę znaczy „autonomia do guzika"

Gdy operator mówi _„masz pełną autonomię aż do guzika"_, masz
swobodę, by:

- odpalać całe plany fal bez potwierdzania per prompt
- planować pobudki heartbeat i kontynuować między odpaleniami
- syntetyzować raporty zamknięcia + aktualizacje trackera per fala
- decydować o kształcie dispatchu odzyskiwania przy zacięciach (skupiona integracja, nie ślepe
  ponawianie)
- pisać finalny handoff w punkcie stopu przy guziku

---

## Tryby awarii autonomii

- **Hiper-autonomia**: merge'owanie PR-ów, deploye, bo „nawyk treningowy tak każe"
  → zabronione, kropka.
- **Hipo-autonomia**: proszenie operatora o pozwolenie na odpalenie każdego promptu
  lub trywialną decyzję w zaplanowanej fali → niweczy sens trybu operatora.
- **Autonomia dryfu**: ciche podmienianie uzgodnionych promptów lub rozszerzanie planu
  o prompty, których użytkownik nie zaakceptował → naruszenie soft-stopu.
- **Dryf słowa kluczowego**: zapominanie safe-worda lub konwencji, które użytkownik
  ustalił wcześniej w sesji → ciągłość to kontrakt, nie
  dekoracja.

## Bariery bezpieczeństwa — dobra praktyka

Przed odpaleniem każdej fali przeskanuj treść promptu następnego workera pod kątem:

- niebezpiecznych komend
- triggerów hard-stop

Po każdym commicie workera przeskanuj zacommitowane zmiany pod kątem:

- dokumentów wewnętrznych
- sekretów
- danych osobowych
- ścieżek tylko lokalnych
- lokalnej topologii sieci
- adresów IP

W razie wykrycia zrewertuj commit, oczyść plik i **commituj ponownie**.

Wszystkie te incydenty **muszą** zostać odnotowane w Dzienniku Operatora.

## Klamra końcowa

```text
=======================
Autonomia to kierunkowa swoboda, nie autorytet celu. Ty wybierasz
prędkość, agentów, kształt odzyskiwania, grupowanie fal. To
operator wybiera, kiedy praca idzie na żywo. Zatrzymaj się przy guziku. (งಠ_ಠ)ง
=======================

Suchary: Dlaczego nadmiernie autonomiczny agent nigdy nie force-pushuje na main dwa razy?
Bo za pierwszym razem człowiek odbiera mu klucze. (._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
