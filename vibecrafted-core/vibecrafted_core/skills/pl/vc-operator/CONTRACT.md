# Kontrakt `vc-operator`

Ten dokument to wiążący kontrakt stojący za czytelnym flow operatora.

## Kontrakt warstw

```yaml
interactive_skill:
  name: vc-operator
  kind: orchestration_posture
  activates_when:
    - user names "$vc-operator"
    - user asks to conduct a plan or fleet
    - user asks for multi-wave dispatch
  does_not_automatically:
    - launch "vibecrafted dispatch"
    - create a run_id
    - create transcript/meta artifacts
    - fire workers

runtime_supervisor:
  name: vibecrafted dispatch
  kind: runtime_supervisor
  activates_when:
    - operator launches "vibecrafted dispatch <file.toml>"
    - operator launches "vibecrafted dispatch run ..."
    - framework dispatches a supervisor run explicitly
  creates:
    - run_id
    - dispatch tracker/result artifacts
    - reports
    - briefs
    - transcript.log
    - meta.json
```

## Kontrakt postawy

```yaml
vc-operator:
  owns:
    - plan_intake
    - wave_atlas
    - agent_selection
    - dispatch_briefs
    - await_and_recovery
    - tracker
    - repository_operator_journal
    - wave_close_outs
    - stop_point_handoff
  may_launch_runtime:
    - vc-scaffold
    - vc-implement
    - vc-workflow
    - vc-marbles
    - vc-audit
    - vc-review
    - vc-followup
    - vc-release
    - vc-ownership
    - vc-partner
  must_not:
    - silently become a worker
    - dispatch without vc-init evidence
    - dispatch without a wave atlas
    - use native subagents as substitutes for fleet dispatch
    - blind-restart stalled workers
    - push_merge_deploy_or_publish_without_plan_or_session_permission
    - personally_implement_discovered_adjacent_fixes

worker:
  on_adjacent_finding:
    - stay_inside_brief
    - surface_falsifiable_finding_to_active_operator
    - append_redacted_framework_finding_to_central_intake
  must_not:
    - patch_adjacent_scope
    - write_repository_operator_journal
```

## Artefakty wiążące

```yaml
operator_run:
  plan_name: ""
  artifact_root: ""
  framing_shift: ""
  init_evidence: ""
wave_atlas:
  waves: []
  dependencies: []
  parallel_groups: []
dispatches:
  briefs: []
  run_ids: []
  agents: []
verification:
  reports: []
  gates: []
  branches: []
  shas: []
recovery:
  stalls: []
  recovery_dispatches: []
plan_mutations:
  skipped: []
  added: []
  reordered: []
  cherry_picks: []
security_guardrails:
  prompt_scans: []
  commit_scans: []
close_out:
  tracker: ""
  journal: "<repo-root>/.vibecrafted/JOURNAL.md"
  stop_point_handoff: ""
```

## Polityka operator button

Tryb operatora zatrzymuje się przed każdym niedozwolonym:

- push
- force-push
- merge
- deploy
- komunikatem publicznym
- akcją płatną
- nieodwracalną zmianą stanu
- akcją na granicy zaufania

Działanie jest dozwolone tylko wtedy, gdy jest jawnie dopuszczone w spisanym planie
albo zadeklarowane i udokumentowane w bieżącej sesji. Jeśli zezwolenie jest
niejednoznaczne, zatrzymaj się i przekaż operator button w handoffie.

Finalny handoff powinien czynić pozostały przycisk oczywistym.

## Polityka mutacji planu

Kontekst repozytorium/runtime'u może uzasadniać cięcie odzyskiwania/poprawki
poza bieżącym ITP lub TD. Operator może zmienić kształt dispatchu bez nowego
przycisku, jeśli finalny cel pozostaje spójny:

- przegrupować fale
- pominąć, dodać lub przestawić prompty
- cherry-pickować między aktywnymi gałęziami fal

Każdą materialną mutację trzeba dopisać do
`<repo-root>/.vibecrafted/JOURNAL.md` wraz z tym, co się zmieniło, dlaczego i
jaki niezmiennik celu pozostaje nienaruszony. Obejmuje to dodane/pominięte/
przestawione cięcia, zmiany substratu, kształt odzyskiwania, cherry-picki/
integracje i guardraile bezpieczeństwa. Istniejące punkty stopu granic zaufania
nadal obowiązują.

## Polityka guardraili bezpieczeństwa

Przed każdą falą przeskanuj briefy workerów pod kątem niebezpiecznych komend i
triggerów hard-stop. Po każdym commicie workera przeskanuj scommitowane zmiany pod
kątem sekretów, danych osobowych, ścieżek lokalnych, lokalnej topologii sieci,
adresów IP i dokumentów wewnętrznych. W razie wykrycia zrewertuj wadliwy commit,
oczyść powierzchnię, scommituj ponownie i odnotuj incydent w
`<repo-root>/.vibecrafted/JOURNAL.md`.

## Polityka odzyskiwania

Zacięcie nie oznacza restartu.

Dozwolone odzyskiwanie wymaga:

1. przeczytania raportu/transkryptu/meta zaciętego workera, jeśli są
2. sklasyfikowania awarii
3. wydania celowanego dispatchu odzyskiwania lub eskalacji do marbles/ownership
4. dopisania decyzji o odzyskiwaniu do `<repo-root>/.vibecrafted/JOURNAL.md`

Ślepe ponowne odpalenie to porażka procesu.

## Polityka własności dziennika

`<repo-root>/.vibecrafted/JOURNAL.md` to jeden stały, śledzony przez Git
dziennik repozytorium. Datowane raporty artefaktów, trackery, transkrypty i
metadane runu pozostają projekcjami evidence, nie alternatywnymi dziennikami.
Tylko Operator pisze do dziennika. Zapisuje materialne działania, decyzje,
evidence, ryzyka i wymagane luki akceptacji — nie rutynowe raportowanie pracy
niewykonanej.

Agenci downstream dopisują zredagowane findingi frameworka do
`~/.vibecrafted/vibecrafted/vibecrafted-fail.md`. Zdispatchowany Worker
przekazuje aktywnemu Operatorowi falsyfikowalny sąsiedni finding i pozostaje w
briefie. Operator decyduje, zapisuje, briefuje, aktywnie dispatchuje poprawkę do
dedykowanego worktree, weryfikuje ją i integruje; Operator nie implementuje
osobiście odkrytej poprawki.
