# Dziennik `vc-operator`

Dziennik Operatora to stały, repozytoryjny zapis decyzji append-only dla orkiestracji.

Zapisuje materialne działania, decyzje, evidence, ryzyka, odzyskiwania,
integracje i wymagane luki akceptacji. Uzupełnia datowane evidence runów bez
jego dublowania.

## Ścieżka

```text
<repo-root>/.vibecrafted/JOURNAL.md
```

To jeden kanoniczny dziennik na repozytorium. Jest celowo śledzony przez Git.
Pozostałe pliki pod repozytoryjnym `.vibecrafted/` pozostają ignorowanym stanem
runtime'u.

Artefakt powiązany:

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/<run>/tracker.md
```

## Dziennik vs tracker

| Artefakt                   | Cel                                                             |
| -------------------------- | --------------------------------------------------------------- |
| datowany tracker/raport    | stan runu, run ID, gałęzie, SHA, bramki, transkrypty i metadane |
| repozytoryjny `JOURNAL.md` | materialne decyzje Operatora i historia misji repozytorium      |

Tracker odpowiada na pytanie „co wylądowało?".
Dziennik odpowiada na pytanie „dlaczego operator zrobił to dalej?".

## Reguły

- Tylko dopisywanie; korekty są nowymi wpisami.
- Tylko aktywny Operator pisze do tego dziennika.
- Zapisuj materialne dispatche, decyzje, evidence, ryzyka, odzyskiwania,
  integracje, guardraile bezpieczeństwa i wymagane luki akceptacji.
- Zapisuj każde materialne odchylenie od bieżącego ITP lub TD: dodane,
  pominięte lub przestawione cięcie; zmianę substratu; kształt odzyskiwania;
  cherry-pick lub integrację; oraz powód.
- Uzasadnione cięcie odzyskiwania/poprawki może wyjść poza bieżący ITP lub TD,
  gdy wspiera je kontekst repozytorium/runtime'u, a finalny cel pozostaje spójny.
- Istniejące punkty stopu na granicach zaufania nadal obowiązują.
- Datowane raporty artefaktów, trackery, transkrypty i metadane runu to
  projekcje/evidence, nie alternatywne dzienniki.
- Agenci downstream dopisują zredagowane findingi frameworka do
  `~/.vibecrafted/vibecrafted/vibecrafted-fail.md`.
- Zdispatchowany Worker pozostaje w briefie, przekazuje aktywnemu Operatorowi
  falsyfikowalny finding, nie poprawia sąsiedniego scope'u i nie pisze do tego
  dziennika.
- Operator ocenia finding, zapisuje decyzję, tworzy bounded brief, aktywnie
  dispatchuje poprawkę do dedykowanego worktree, weryfikuje ją i integruje.
  Operator nie implementuje osobiście odkrytej poprawki.
- Nie zapisuj rutynowych twierdzeń o pracy niewykonanej. Git, metadane runtime'u,
  receipty i raporty już ich dowodzą.
- Klamry zamykające workera nie pojawiają się we wpisach dziennika operatora.
- Nie zwijaj osobnych stanów workerów w mglisty status fali.

## Pierwszy wpis

````md
## <timestamp> - operator mode active

```yaml
operator_run:
  plan_name: ""
  repository: ""
  source_plan: ""
  init_evidence: ""
  stop_point: "operator button"
```

- State:
- Wave atlas:
- Next:
````

## Wpis dispatchu

```md
## <timestamp> - fire wave <n>

- Wave:
- Briefs:
- Agents:
- Run IDs:
- Dependency state:
- Await path:
```

## Wpis odzyskiwania

```md
## <timestamp> - recovery dispatch

- Stalled run:
- Failure class:
- Evidence:
- Recovery brief:
- Recovery agent:
- Expected close condition:
```

## Wpis mutacji planu

```md
## <timestamp> - plan mutation

- Changed:
- Why:
- Final goal unchanged because:
- Evidence:
- Next:
```

Użyj tego kształtu dla dodanych, pominiętych lub przestawionych cięć, zmian
substratu, kształtu odzyskiwania oraz decyzji cherry-pick/integracja.

## Wpis odkrytej poprawki

```md
## <timestamp> - decyzja o odkrytej poprawce

- Finding Workera:
- Decyzja Operatora i powód:
- Bounded brief:
- Dispatch do dedykowanego worktree:
- Evidence weryfikacji i integracji:
- Ryzyko lub luka akceptacji:
```

## Wpis guardrailu bezpieczeństwa

```md
## <timestamp> - security guardrail

- Surface: prompt | commit
- Detected:
- Action taken:
- Recommit SHA, if applicable:
- Next:
```

## Wpis zamknięcia

```md
## <timestamp> - wave <n> close-out

- Landed:
- Branches:
- SHAs:
- Gates:
- Risks:
- Next wave or stop reason:
```

## Wpis punktu stopu

```md
## <timestamp> - stop at operator button

- Completed waves:
- Remaining human button:
- Evidence:
- Risks:
- Recommended next action:
```
