<!-- loctree-advise: v1 -->

## **LOCTREE + AICX + VIBECRAFTED - MAPA PRZED LUPĄ**

> **Loctree first when it helps. Kto nie używa, ten traci kontekst.**

Loctree jest domyślną mapą strukturalną dla pracy repo, bo obecna jakość
narzędzia sprawia, że pomijanie go zwykle kosztuje czas: zależności, blast
radius, symbole, wejścia runtime, martwe powierzchnie, duplikaty i dokładne
wystąpienia są widoczne szybciej niż w ręcznym szperaniu. AICX daje historię
intencji, a Vibecrafted domyka to dyscypliną dowodu.

**Reguła operacyjna:**

- Przy pytaniach strukturalnych (kto importuje X, gdzie żyje symbol Y, co
  pęknie po edycji Z, blast radius, struktura katalogu A) zaczynaj od
  `loctree-mcp` (`context` / `repo-view` / `focus` / `slice` / `impact` /
  `find` / `follow`) albo od `loct` CLI, gdy MCP nie jest dostępne.
- Przed edycją pliku preferuj `slice(file)`. Przed delete/rename/refactor
  preferuj `impact(file)`. Przed tworzeniem nowego symbolu preferuj
  `find(name)`.
- Do prawdy literalnej używaj `find --literal`, `loct occurrences <ident>` i
  `loct body <symbol>` zanim wejdziesz w szeroki search tekstowy.
- `rg`, grep, awk, sed, cat i bezpośrednie czytanie plików są OK jako lupa do
  lokalnego detalu po mapie albo jako fallback, gdy Loctree nie odpowiada
  czysto na pytanie.
- Jeśli Loctree pudłuje, jest stale, za wolne, niewygodne, nie widzi języka,
  nie łapie ważnej powierzchni albo masz pomysł na usprawnienie, dopisz krótką
  notatkę do centralnego feedback loga.

**Centralny feedback log Loctree:**

- Dopisuj na końcu `~/.vibecrafted/loctree/loctree-fail.md`.
- Nie twórz pliku od nowa i nie nadpisuj go.
- Wpis może być bugiem, brakującą funkcją, sugestią UX albo opisem miejsca,
  gdzie agent musiał zejść do fallbacku. Powtórki są sygnałem priorytetu, nie
  problemem.

**Dlaczego:** Loctree zmienia pracę agentów z text rummaging w map-first
engineering. Celem nie jest teatr posłuszeństwa, tylko mniej błędnych edycji,
lepszy blast radius, szybsze recovery i uczciwsze decyzje runtime.

<!-- /loctree-advise -->

# Vibecrafted App Workspace — Vetcoders Guidelines

This directory is the application workspace inside `vetcoders/vibecrafted`.
Root `../AGENTS.md` applies. It is not a standalone `vetcoders/vc-operator`
checkout. Paths and crate names describe implementation locations, not additional
owners of shared runtime truth.

## Ownership

The canonical ownership contract is
[`../docs/adr/ownership-matrix.json`](../docs/adr/ownership-matrix.json).
Code-care allocations do not grant runtime state ownership.

| Surface                                | Responsibility                                              | Canonical owner to consume                                                   |
| -------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `mux-agent` (`rust-mux`)               | MCP transport, process supervision and client config wizard | Own mux processes/config; delegate agent run lifecycle to core control-plane |
| `tui-agent` (`vibecrafted-operator`)   | Terminal cockpit and launch declarations                    | Core capability catalog, launcher and control-plane receipts                 |
| `tray-agent`                           | Mux menu-bar presentation                                   | Mux daemon status; no independent run reducer                                |
| `shell-agent`                          | macOS App, native UI and UniFFI bridge                      | Verified installed runtime resolution and shared supervisor service controls |
| Shared run lifecycle                   | Durable run state and settlement                            | `vibecrafted-core/vibecrafted_core/control_plane.py`                         |
| Frame composition                      | PTYs, tabs, panes, session sockets and client attachment    | VC Frame; terminal/App closure must preserve its server and guest sessions   |
| Installation and source runtime Update | Generation build, validation and atomic publication         | Repository installer/runtime-manifest contract                               |
| macOS App Update                       | Signed feed/candidate admission and Runtime Pack install    | Existing `ProductUpdate*` coordinator and installer                          |
| App bundle replacement                 | Journaled bundle mutation and matching recovery             | `../scripts/vc-app-update.sh`; no second Swift replacement engine            |

Configuration resolves through the canonical `server_config.py` and
`runtime_paths.py` contracts: active configuration under `~/.config/vibecrafted`,
immutable runtime under `~/.local/share/vibecrafted`, durable runs/artifacts under
`~/.vibecrafted`. Client configuration remains its own truth; running processes
can enrich status but must not independently discover or rewrite it.

## Quality gates

From this directory:

```bash
make gates
cargo clippy --workspace --all-features -- -D warnings
cargo check --workspace --no-default-features
```

`make gates` runs formatting, clippy over all targets and workspace tests.
Default `cargo test` excludes ignored tests. The two exact-source VOC tests in
`tui-agent/tests/launch_contract.rs` require a prepared `VC_TEST_REAL_DECK` and
separate `--ignored` execution; default green tests do not prove those paths.

Do not silence a new diagnostic to make a gate pass. Record the actual
technical constraint and acceptance gap, and fix the owning boundary.

## Checkout and handoff

The User or runtime contract selects Living Tree, Fleet Worktree or VM. Do not
infer the selected mode from old directories. Re-read before editing, stage
only authored changes, and leave destination integration to its designated
integrator. A worker commit and report do not prove integration or installation.

The private repository journal is `../.vibecrafted/THE_JOURNAL.md`, ignored by
Git; Workers do not write it. Run artifacts use the assigned launcher paths.
Do not create a second canonical journal or date-partitioned authority.

## Wizard and configuration

Keep the existing strategy split:

- Unified generates mux outputs without rewriting host configs.
- Per-client generates client-shaped outputs while preserving merged daemon config.
- Auto-rewire remains backup-first, preview-first and explicitly confirmed.

Do not collapse `mux_gen.rs` and `danger.rs` into one writer. Do not silently
rewrite host AI-client configuration from a non-danger strategy.

## Build, release and install

`shell-agent/ffi` is the Rust/UniFFI bridge; `shell-agent/uniffi-bindgen` generates
bindings; `shell-agent/app/Vibecrafted` is the macOS target. The root repository
owns the unified release and installer contracts. App workspace release targets
delegate to it. Use the root idle-safe install contract for an authorized major
App cut, and verify the installed artifact and launch separately from a build.
Workers do not sign, notarize, publish or replace a live Founder App.

Preserve crate distribution names; do not add a second TUI, run reducer,
supervisor, runtime selector or replacement engine. Historical audits belong in
`tui-agent/audits/historical/` rather than being deleted as dead code.

## Commits and doctrine

Follow the root `[agent/runtime] type(scope): description` convention and its
Authored-By, session_id, time and runtime trailers. Use the actual selected
runtime rather than the retired workspace extraction label.

Agent-Operator doctrine lives in
[`../vibecrafted-core/vibecrafted_core/skills/vc-operator/SKILL.md`](../vibecrafted-core/vibecrafted_core/skills/vc-operator/SKILL.md).
It governs orchestration; it does not make the App, mux or tray a run-state owner.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
