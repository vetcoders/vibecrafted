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
