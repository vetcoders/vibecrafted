# vc-ship — przykładowy trigger

## Fraza wyzwalająca

> "lecisz z taskiem vc-ship dla vibecrafted-server adaptation — dopilnuj tego
> aż do release z właściwymi korektami po drodze"

## Oczekiwane zachowanie agenta

1. Przebieg vc-init w docelowym repo: atlas kontekstu Loctree przeczytany do
   końca, odzyskane intencje AICX, ocenione ryzyko i prawda z gita.
2. Ułożenie misji jako trwałego pliku pod
   `~/.vibecrafted/artifacts/<org>/<repo>/<date>/plans/…_prompt.md` —
   deliverables, twarde ograniczenia, nazwane bramki — i start:
   `vibecrafted ship codex --file <mission.md>`.
3. Nadzór nad sztafetą pałeczki etap po etapie: watcher na raporcie etapu +
   żywotność z `ship status --json`; weryfikacja commitów/bramek przed każdym
   `approve`; odzyskiwanie martwych workerów przez `interrupt → fallback → approve`;
   trasowanie świadomych luk przez `accept-dou`.
4. Dostarczenie końcowego raportu z lotu: przelecione etapy, korekty, commity,
   kolory bramek, `dou_index` oraz to, czego release uczciwie NIE zweryfikował.

## Evidence akceptacyjne

Co operator powinien zobaczyć w końcowym raporcie agenta:

- Id runu cyklu życia (`life-ship-…`) z 11/11 etapów otrasowanych w
  `operator_actions` (albo stop decyzją operatora, nazwany wprost).
- Ścieżki raportów per etap + hashe commitów z etapów WRITE + wyniki bramek
  (np. zielony core suite, zielony `make server-test`).
- `dou_index: 0` — albo każda pozostała luka jako jawny wpis `accept-dou` z
  nazwanym followupem.

## Uwagi

- Prawdziwy precedens: loty `life-ship-260702-123238-24000` (v3.3.0) i
  `life-ship-260702-202338-58000` (lifecycle.schema.v1) — oba nadzorowane
  end-to-end, z każdym czasownikiem użytym na serio.
