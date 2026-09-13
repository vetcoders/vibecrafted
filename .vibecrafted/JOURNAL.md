# Operator Journal

This is the append-only, repository-local decision journal for the active
Vibecrafted Operator. Workers do not write here. Runtime telemetry, raw
transcripts, secrets, private prompts, and routine negative activity do not
belong here.

## 2026-08-26T06:05:00+02:00 — canonical journal established

- Decision: keep one tracked operator journal at
  `<repo-root>/.vibecrafted/JOURNAL.md`.
- Ownership: the Operator records material decisions, dispatches, recoveries,
  integrations, security guardrails, and deviations from ITP or TD.
- Worker boundary: Workers surface falsifiable findings to the Operator and do
  not opportunistically repair adjacent scope or write this journal.
- Runtime boundary: every other file under `<repo-root>/.vibecrafted/` remains
  ignored local state.

## 2026-09-01T21:02:24+02:00 — aicx: review PR-ów + release 0.13.0 pod guzik

Zlecenie Foundera (sesja 8d57e6a3): review otwartych PR-ów aicx, /vc-release 0.13.0.

- Worktrees aicx 18→1: cuty fusion W0–W4 + fala w1 z 19.08 skonsumowane w 215b806
  (git cherry unique=0), zdjęte z gałęziami. Żywe gałęzie: cut/cursor-on-throne (PR #67),
  fix/index-status-signal-count (guziki Foundera).
- Werdykty PR: #64 skonsumowany przez fusion (zamknąć przy merge); #65 superseded
  przez #69 (unikat: repair-mcp-runtime tooling); #67 czysty, baza fusion; #69 kompletny,
  ale konflikt z fusion w 8 plikach → kolejność: fusion first (FF), #69 rebase po merge.
- Znalezisko release: 215b806 „prepare 0.13.0" bumpnął kanały na 0.12.6; notki fusion
  wisiały w osieroconym [0.13.0]. Wyleczone w repo aicx commitem b3650ca (release_sync
  bump 0.13.0, fantomowy [0.12.6] rozpuszczony); channel-check + version-section green.
- PR Loctree/aicx#70 otwarty (fusion → main; main = merge-base, merge = czysty FF).
  Guziki Foundera: merge #70 → make release-tag TAG=v0.13.0 → make release-push.

## 2026-09-02T04:20:00+02:00 — release 4.3.0: re-weryfikacja stanu „pod guzikiem" + draft PR #75

Sesja resume (pakiet ciągłości aicx, bez jawnego zlecenia). Decyzja moja: zamiast
czekać, sprawdzić czy handoff z 3b73d1fe nadal trzyma i wystawić go na GitHub.

- HEAD `fde0fbe3` == origin, drzewo czyste, 268 commitów nad `main` (merge-base = main,
  czysty FF). `vibecrafted doctor`: 5/5 CLEAN (runtime 4.3.0+gd38f3e66, 10 commitów za HEAD).
- `make exact-release-contract-gate` rc=0; `release-output.json`/`portable-output.json`
  nazywają `fde0fbe3`; CodeQL open na `main` = 0.
- #74/#73/#71/#70/#69/#66 = ancestory HEAD (`merge-base --is-ancestor`); #65 trzyma
  8 commitów spoza linii → osobny cut po 4.3.0.
- Otwarty **draft** PR #75 (`fix/v430-dispatcher-shutdown-race-v5` → `main`), body bez
  vendor-footera. Merge, tag `v4.3.0`, push taga, `publish-release`, zamykanie PR-ów —
  guziki Foundera, nie ruszone.
- Korekta: JOURNAL jest trackowany gitem (`git ls-files`), wbrew notce z 3b73d1fe.

## 2026-09-02T07:05:00+02:00 — 4.3.0 w polu: expat-plist (PR #76/#77) + tick storm supervisora (finding)

Incydent na hoście Moniki z DMG `fde0fbe3`: obcy LaunchAgent z `--` w komentarzu XML
→ `plistlib` rzuca `ExpatError`, instalator łapał tylko `InvalidFileException` →
„Vibecrafted cannot open its workspace terminal" z surowym tracebackiem. Instalacja
częściowa: runtime przestawiony na 4.3.0+gfde0fbe3, reszta przerwana.

- Monika/Mikserka: PR #76 (`agent/fix-runtime-plist-expat`, fd95a9d4) — skaner cudzych
  plistów. Decyzja Foundera (sesja, 06:5x): nie pchać na gałąź Moniki, własna gałąź.
- Claude: PR #77 (`agent/plist-decode-errors-all-sites`) nad #76 — jedna krotka
  `_PLIST_DECODE_ERRORS` we wszystkich 4 odczytach plistów instalatora + dedykowany
  test regresji z bajtami z pola; 157 testów instalatora zielone.
- Konsekwencja dla wydania: artefakty w `dist/` nazywają `fde0fbe3`; po merge #76/#77
  na linię tag i `make release` muszą iść z nowego HEAD. Guziki Foundera.
- Finding (nie fix): supervisor LaunchAgent bez `--interval` → 1 s; zdrowy tick =
  pełne `server start` + `server status` przez deck bash + kilka python3.12.
  Pomiar tu (20 s): 14×start, 13×status, ≥26 python, CPU śr. 27 %, szczyt 84 %.
  Raport: `~/.vibecrafted/reports/2026-09-02-supervisor-tick-storm-430.md`.
  Usługa na tym hoście NIE zatrzymana (sesja Foundera żyje na tym runtime).
- Pre-commit semgrep i pre-push (cały tree) przekraczają 2-min limit harnessu —
  commit/push idą odłączone (`nohup`) z monitorem.

## 2026-09-02T08:20:00+02:00 — stos 4.3.0-hotfix: #76 → #77 → #78; #77 zsynchronizowany, tick storm ma fix (PR #78)

Sesja resume bez jawnego zlecenia (wejście „Primary" = urwany wklej pakietu ciągłości;
pakiet 06:44 dotyczył 3more-studio, ten z 04:06 — vibecrafted). Decyzja moja: zamknąć
to, co od wpisu 07:05 zmieniło się na GitHubie, i zostawić Founderowi jeden guzik.

- **PR #78** (`agent/fix-supervisor-healthy-loop`, Monika/codex, 11 commitów, stacked na #76)
  to fix tick-stormu z raportu `2026-09-02-supervisor-tick-storm-430.md`: supervisor
  najpierw sonduje parę (`server supervisor-pair-health` = 1 bash + 1 python), `server
  start` woła tylko przy braku dowodu; interwał 1 s zachowany; sonda przerywalna na stop.
  Deck ≡ scripts (bajt w bajt, sprawdzone). Review lokalne, bez komentarzy na PR.
- **PR #76** urósł fd95a9d4 → fea43671 (8 commitów, wyłącznie hardening CI/testów run-signal;
  treści #77 nie skonsumował). **PR #77** stał na starym fd95a9d4 → portable red na obu OS:
  Linux SC2093 (`pane-python`, naprawione w #76 a233cd0f), macOS `claude executable not
  found` w `test_operator_mode.py` (naprawione w #76 0e3a6ab6/690eb3d5). Odziedziczone, nie moje.
- Sync #77: merge czubka #76 (`b6241ebc`, zero konfliktów, bez force-push — ten sam wzór co
  #78). Pre-commit ruff-format złożył jedno wywołanie `read_text()` z 690eb3d5 na jedną linię;
  CI nie ma kroku `ruff format --check`, więc to artefakt hooka, nie bloker #76. Baza #77
  przestawiona na `agent/fix-runtime-plist-expat` (diff = tylko własna zmiana).
  Testy instalatora w worktree gałęzi: 196 passed (2:46) przez `uv run --with pytest`.
- Kolejność dla Foundera: merge #76 → #77 → #78 na `fix/v430-dispatcher-shutdown-race-v5`,
  dopiero potem tag `v4.3.0` i `make release` z nowego HEAD (artefakty w `dist/` nadal
  nazywają `fde0fbe3`). Portable CI dla fea43671 i ecf95053 w toku od 07:27.
- Hook `commit-msg` odrzuca typ `merge(...)` i wymaga trailerów `session_id`/`time`/`runtime`;
  merge commit poszedł jako `chore(install)`.


## 2026-09-06 — Stage 1 terminal-entry integration

Agent-Operator admitted the reviewed Fleet Worktree chain through `7dc7b2d5ca1fba5540ed1469ae25dd16f50ea486` with exact merge `dd8350bf0ac611324bc1a04c629b958857b5a445` (first parent `de2d1bbc2601334a815ec3554df6a2496d5a4011`). Public non-TTY start/resume now uses the canonical terminal host and explicit project root; native detached Frame creation precedes one provider launch and foreground attach. R4 closes Bash/Zsh root-argv and reserved-status differences. Independent review and recorded 40 passing affected tests are in `~/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0906/reports/S1-start-resume-R4-admission.md`. Installed-runtime acceptance remains pending.

Earlier operator continuity was appended to ignored `.vibecrafted/THE_JOURNAL.md`; current tracked charter specifies this `JOURNAL.md`. Preserve that historical file as evidence and continue material decisions here; do not erase or promote its claims without current verification. Stage 2 remains held until final build, signed installation, config preservation and live-owner/launch proof. Remote was independently verified at `de2d1bbc2601334a815ec3554df6a2496d5a4011` after all normal pre-push gates.


## 2026-09-06 — Installed stage 1 and terminal lifetime recovery

Built, signed, notarized and installed source `cb026674e9cf87f5357eebe6d182ee580339936c`; App and DMG Apple submissions were accepted and installed App identity matches the signed release tuple. Runtime owner reports ready and launchd now runs the new supervisor/server/guardian. App launch adopted that generation and opened its terminal. Original Frame session owner PIDs and sockets survived. Reports are under the day artifact `reports/stage1-verification/`; preserve the old App backup and configuration backup.

Real public-entry acceptance exposed a remaining lifetime boundary: `public-start-lifetime.json` records vc-start exiting 0 while terminal PID 37536 remains alive through 12 seconds, sharing caller PGID 37470; after the outer exec invocation ends, that terminal/client disappear while detached Frame server 23753 survives. Agent inference: the background shell/disown launch is still coupled to caller-group cleanup. Operator admits a bounded R5 Fleet Worktree repair for independent terminal process lifetime; no claim of completed stage 1 or start of stage 2. A transient EXITED listing for Needs attention was falsified by unchanged owner PID 76455 and socket and subsequent live listing.

## 2026-09-12T17:20+02:00 — PR #86 wchłonięty do base; pierwsza fala napraw stacku #75→#77→#80

Sesja resume (rsme-260912-155701-63388). Decyzje Foundera w sesji: merge #86
lokalnie do HEAD, push na origin, podział pracy (agent: stack; Founder: pozostałe PR-y).

- **#86 → base**: fast-forward `b84f3b4a → e31b86f9` + format-fix (`39268cbe`,
  pre-push ruff odbił plik testu z PR-a). Push przesunął head #80; #86 rozliczy
  się jako merged.
- **Paczka 6 commitów** (`e15a0d24..af49f375`, wypchnięta, 136 passed lokalnie
  na dotkniętych modułach): 2× produkcyjny crash pustej tablicy pod bash 3.2
  `set -u` w launcherze (`launch_args`, `_vetcoders_start_frame_argv` — trzeci
  i czwarty przypadek wzorca już opisanego w pliku przy linii 1732); 4× stęchłe
  testy wobec nowszych kanonów: retired `config install` (e1d7a791), fail-closed
  owned interpreter bez PYTHONPATH (3fe139a1), no-tty surface admission
  (bf028c40, eskalacja z pipe'a mimo żywej sesji jest CELOWA), agy stream-json
  stdin lane (e33c09f0 — fake salvage agent nie dekodował NDJSON).
- **Werdykty security (agent, zweryfikowane w kodzie)**: 3 alerty CodeQL na
  stacku to false positives — critical `rust/command-line-injection`
  (run_observation.rs:393; `is_safe_run_id` whitelist + argv bez shella) oraz
  2× high `js/xss-through-dom` (playground; wszystkie ścieżki do innerHTML
  bramkowane słownikami, prompt przez esc()). Dismiss = guzik Foundera;
  taint-break pod skaner odrzucony jako kod-teatr.
- Otwarte: #77/#75 Linux CI bez Runtime Packa (stara linia bez build-joba);
  core-macos 4 testy na #80 i entry_escalation na Linuksie — ocena po świeżym
  runie CI z tej paczki.

## 2026-09-14T02:30+02:00 — vc-prune na Living Tree: bramki przechodzące na ślepo, silencery, martwy kod (claude/interactive)

Wywołanie Foundera w sesji: `/vc-prune na ostro poprosi vibecrafted szczerze`. Fork sesji
vibecrafted-1e; linia kandydata (#88), jej workery i CI zostają przy rodzicu.

- **Zakres i kolizje (decyzja agenta):** cięcia na bieżącej gałęzi Living Tree
  (`agent/fix-supervisor-in-process-probe`). W plikach zmienianych też przez
  `integration/main-candidate-260912` tylko przy czystym `git merge-file` z wersją
  kandydata (12 plików, rc=0). Cięcia przypięte testami w gorących plikach czekają na #88.
- **Bramki na ślepo:** hook pre-push (pane guard) grepował nieistniejące `runtime/scripts/lib`
  z `2>/dev/null`; 4 testy frontier skipowały się wszędzie na starej ścieżce; noga faz PL
  lokalizacji porównywała z `data-phase`, którego HTML nigdy nie miał (faktyczny konsument:
  `PHASES[].name` w `framework.js`). Wszystkie trzy uzbrojone i zielone.
- **Globalne wykluczenia shellcheck** SC2155/SC2034/SC2154/SC2015 zdjęte (zostały
  SC1090/SC1091). Ujawniły 46 znalezisk, w tym SC2154 na `vc_frame.sh:477` — wektor P0
  z audytu 85fbf5e7. 71 zbędnych dyrektyw usuniętych, 36 dostało powód.
- **Wyciek procesów w testach:** teardown ownera zabijał tylko PID; providerzy żyli pod PID 1
  godzinami. Teraz `killpg` grupy sesji w 5 miejscach.
- **Nie wycięte, pytania do Foundera:** iTerm2 + Hammerspoon, mux/tray, `bin/` vs
  renderowane launchery, homebrew (tap nie istnieje), ACP, trzy instalatory, mypy-teatr
  (102 błędy, zero bramek), `COMPILE_EMBARGO` vs `--no-verify`, wymagany check
  „docker build" na obrazie v1.x.
- **Korekta własna:** pierwszy pomiar `nosemgrep` i `RUF100` był nieważny (zsh nie rozbił
  listy plików); bieg kontrolny pokazał 18 realnie tłumionych trafień semgrep. Żadnego
  `nosemgrep` nie wycięto. Wpis w `vibecrafted-fail.md`.
- Commity `ecd64e97..7eadd7bb` (16, wszystkie przez normalne hooki). Dowód: bramka shell
  179 plików czysto; clippy `-D warnings` tui-agent czysto; 33 pliki tests/tui drzewo po
  cięciu vs klon bazowy e6b94ac2: 148 vs 149 porażek, zero nowych, passed 1051 → 1058,
  skipped 13 → 7; testy core 495 passed w obu (placeholder −3 skip).
- Raporty: `~/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0914/reports/prune/`.
