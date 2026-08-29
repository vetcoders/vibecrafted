# Vibecrafted CLI Product Spec — One Sharp Instrument

> Status: APPROVED-FOR-IMPLEMENTATION design pack (vc-decorate output, 2026-06-10).
> Implementation status (2026-06-10, second decorate pass): cuts 1–4 of §7 landed —
> `scripts/lib/vc_ui.sh` + `vibecrafted_core/ui.py` exist, the §2 deck ships with
> `help --all` canonical, `make help` is six targets + `help-dev`, install.sh
> rhetoric purged (§4 kill list) with the §6.2 consent card.
> Implementation status (2026-06-10, third decorate pass): cuts 5–6 landed —
> `vetcoders_install.py` is compact-by-default with strict modes
> (`--verbose`/`--debug`, `--compact` retired as a silent no-op), prints the
> §6.1 finish card, the §6.6 skill counter, and the §6.4 summary-first doctor;
> bracket prefixes and checkpoint REASON lines are gone; `loop`/`cron`/`ship`
> adopt `ui.py`, and `cron tick` prints JSON only under `--json`
> (the crontab line generator emits `--json` for machine logs).
> Scope: `install.sh`, `Makefile`, `scripts/vibecrafted`, `scripts/vetcoders_install.py`,
> `scripts/install-foundations.sh`, `scripts/install-runtime.sh`, `vibecrafted_core` runtime
> commands (loop, cron, ship, doctor).
>
> This is productization of the CLI experience, not refactoring for code quality.
> Target feeling: **"I ran 1 command and immediately understood what is happening
> and what I should do next."**

---

## 0. Audit verdict (why this spec exists)

Evidence gathered across the four CLI surfaces:

| Surface                              | Finding                                                                                                                                                                                                     |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| install.sh + Makefile                | **7–8 coexisting visual languages.** install.sh prints plain text, Makefile prints 256-color ANSI. 17+ defensive/apologetic lines. **No end-of-install message at all** — the script `exec`s away silently. |
| vetcoders_install.py (5810 LOC)      | 8+ output styles, two parallel install narrations (verbose + compact), doctor prints 50–80 unsummarized finding lines, 15+ reassurance lines ("don't worry, a fallback exists").                            |
| scripts/vibecrafted (audit baseline) | The former surface exposed 42 hidden agent-first combinations alongside the skill-first grammar. The current deck rejects those combinations with a migration hint.                                         |
| runtime (loop/cron/ship/doctor)      | Python modules print **zero** styling — a different product than the bash layer. cron prints raw JSON to humans. No spinner anywhere; iTerm2 OSC progress exists (`iterm2_osc.py`) but is unused.           |

Identity vs drift:

- **Identity (preserve):** the `⚒` mark, copper `38;5;173` + steel `38;5;247` palette,
  the `𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.` wordmark, `✓ ✗ ! ▸ ─` glyph language, the tagline
  "Release engine for AI-developed software."
- **Drift (remove):** everything else — bracket prefixes (`[error]`, `[warn]`, `[note]`,
  `[app]`, `[wizard]`, `[telemetry]`), unstyled Python output, apologetic rhetoric,
  double install narrations, the 98-line help.

---

## 1. Final command map

### Golden surface — the only things `vibecrafted help` shows

| Command                       | One-line description                         |
| ----------------------------- | -------------------------------------------- |
| `vibecrafted init [agent]`    | Orient an agent in this repo                 |
| `vibecrafted <skill> <agent>` | Run a workflow with an agent                 |
| `vibecrafted resume <agent>`  | Continue a stopped run or a provider session |
| `vibecrafted status`          | Today's agent activity                       |
| `vibecrafted doctor`          | Installation health — pass/fail              |
| `vibecrafted update`          | Update to the latest release                 |
| `vibecrafted help [topic]`    | Command deck · `help --all` for everything   |

Seven entries. Skills shown in the main help are only the ship cycle:
`scaffold → implement → review → workflow → followup → marbles → audit → polarize → dou → hydrate → release`.
The remaining 14 skills live in `help --all` and in `help <skill>`.

### Hidden behind `help --all` (still work, never advertised up front)

| Command                                                | Reason                                                               |
| ------------------------------------------------------ | -------------------------------------------------------------------- |
| `gui`, `tui`, `dashboard` (+ ls/switch/attach/kill/gc) | Operator consoles — second visit, not first contact                  |
| `loop`, `cron`, `ship`, `dispatch run`                 | Runtime/automation plumbing; humans meet them via docs, not the deck |
| `telemetry smoke`                                      | Dev-only diagnostic                                                  |
| action-first workflow and lifecycle commands           | One public grammar: `<action> <agent>`                               |
| `marbles <pause·stop·resume·session·inspect·delete>`   | Control plane, documented in `help marbles`                          |
| `uninstall`, `version`                                 | Necessary, not promotional                                           |

### Remove from help entirely (keep working as silent aliases)

`stats`, `check`, `upgrade`, `remove`, `mc`, `mission-control`, `sessions`,
`vibecraft`, `dispatcher`, `-v`. Aliases are muscle memory, not documentation surface.
`justdo` keeps exactly one line in `help --all` (it is brand, not noise).

### Merge / delete

| Item                                                                             | Action                                                                                                                       |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `vibecrafted start`                                                              | Fold into `dashboard` (both start a vc-frame session); keep `start` as silent alias                                          |
| `vibecrafted agents`                                                             | Delete as a command; becomes `help agents`                                                                                   |
| `vc-research` listed twice in help (as `vibecrafted research` and `vc-research`) | One line; the `vc-*` layer gets a single footnote: "Every skill also installs a `vc-<skill>` shortcut."                      |
| `help --full` vs `--verbose` shadowing                                           | `help --all` is the canonical flag (keep `--full` silent). `--verbose` is ONLY an output-verbosity mode, never a help switch |
| `--sandbox` available in `vc-*` wrappers but not in the dispatcher               | Close the split: dispatcher accepts and forwards `--sandbox`                                                                 |

### Makefile

62 targets stay, but `make help` shows exactly six:
`install · doctor · update · uninstall · test · check`.
Everything else moves to `make help-dev` (one new target that prints the full inventory,
grouped: install variants / tests / iTerm2 / server / version / hooks).

---

## 2. Rewritten `--help` (the whole thing — 26 lines)

```
⚒  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. 3.1.0 — release engine for AI-developed software
─────────────────────────────────────────

Usage:
  vibecrafted <command> [args]
  vibecrafted <skill> <agent> [-p <prompt> | -f <file>]

Commands:
  init [agent]         Orient an agent in this repo
  <skill> <agent>      Run a workflow with an agent
  resume <agent>       Continue a stopped run or a provider session
  status               Today's agent activity
  doctor               Installation health — pass/fail
  update               Update to the latest release
  help [topic|--all]   This deck · full reference

Ship cycle:
  scaffold → implement → review → workflow → followup → marbles → audit → polarize → dou → hydrate → release
  14 more skills: vibecrafted help --all

Agents:  claude · codex · gemini · agy · junie · grok · cursor

Examples:
  vibecrafted init claude
  vibecrafted implement codex -p "Ship dark mode"
  vibecrafted marbles claude -p "Loop until clean"
```

Rules baked in: every command has exactly one line; exactly three examples;
no "Start here" essay (that lives in `START_HERE.md`, linked once at the end of
install, not re-printed on every help); read time under 10 seconds.

`help --all` keeps today's full reference content, reorganized under the same
visual system, and is allowed to be long — that is its job.

---

## 3. Output system — one language, three layers

### 3.1 Tokens (single source of truth)

New shared modules — **`scripts/lib/vc_ui.sh`** (bash) and
**`vibecrafted_core/ui.py`** (python) — both implementing the identical contract:

| Token      | Value            | Use                                |
| ---------- | ---------------- | ---------------------------------- |
| copper     | `\033[38;5;173m` | brand mark, headers, stage spinner |
| steel      | `\033[38;5;247m` | rules `─`, secondary meta          |
| green `✓`  | `\033[32m`       | success                            |
| red `✗`    | `\033[31m`       | failure                            |
| yellow `!` | `\033[33m`       | warning                            |
| cyan       | `\033[36m`       | copy-pasteable commands            |
| dim        | `\033[2m`        | "next:" hints, paths               |

All color is TTY-gated (`[ -t 1 ]` / `sys.stdout.isatty()`) and `NO_COLOR`-aware.
Bracket prefixes (`[error]`, `[warn]`, `[note]`, `[app]`, `[wizard]`, `[telemetry]`)
are **deleted everywhere** — the glyph is the prefix.

### 3.2 Loading template

```
⠋ scanning repo
```

- One spinner: braille frames `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`, copper, 80 ms, rendered with
  `\r\033[K` (no flicker, no scroll).
- The message is always a **stage verb + object**, never "processing…".
  Canonical stage verbs: `scanning · resolving · staging · installing · finalizing`.
- Non-TTY/CI fallback: a single `▸ scanning repo` line per stage, nothing animated.
- On completion the spinner line is **replaced** by the success line — a stage never
  occupies two lines.
- Every command that can exceed ~400 ms must show a stage. No silent waits.

### 3.3 Success template

```
✓ staged vibecrafted 3.1.0 → ~/.local/share/vibecrafted/tools/vibecrafted-current
  → next: vibecrafted init claude
```

- Line 1: `✓` + result with the one key number/path. ≤ 1 line.
- Line 2 (optional, dim): exactly one next step. Never a list of three.

### 3.4 Error template

```
✗ could not refresh staged tools
  → fix: rerun `vibecrafted update`
  log: ~/.vibecrafted/install.log
```

- Line 1: what failed, human words, no internal-state narration.
- Line 2: one copy-pasteable fix.
- Line 3 (optional, dim): where the full log is.
- Stack traces and raw subprocess output appear **only** under `--debug`.
- All errors go to **stderr**, exit code non-zero. No exceptions to this.

### 3.5 Strict modes

| Mode        | Behavior                                                                                                               |
| ----------- | ---------------------------------------------------------------------------------------------------------------------- |
| default     | Bounded, stage lines + result card only. Full transaction log always written to `~/.vibecrafted/install.log` / run log |
| `--verbose` | Adds per-step detail lines (what today's verbose install prints)                                                       |
| `--debug`   | Raw subprocess output, tracebacks, env dumps                                                                           |

Never mixed. The compact/verbose **dual narration** in `vetcoders_install.py`
collapses: compact becomes the default, "verbose mode" becomes `--verbose`,
and the `--compact` flag is retired (kept as a silent no-op for one release).

### 3.6 Bounded output rules

- Lists > 8 items print `first 5 + "… and N more (--full)"`.
- Skill install loop prints **one counter line** (`⠼ installing skills 12/17`),
  not 17 lines; per-skill detail under `--verbose`.
- Doctor, status, and any report print a **summary line first**, findings after,
  capped at the failures + warnings; passing checks are a count, not lines.
- Log tails capped at 12 lines (already the convention in compact mode — now the
  only convention).

---

## 4. Rhetoric purge — kill list (verbatim, verified line numbers)

The installer must stop apologizing. Signal replaces reassurance:

| Location                                                             | Today (verbatim)                                                                                                                                                   | Becomes                                                                                  |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `install.sh:320`                                                     | `Nothing will be staged or installed until you say yes.`                                                                                                           | _(deleted — the `[y/N]` prompt already says it)_                                         |
| `install.sh:326`                                                     | `Bootstrap cancelled: no confirmation received.`                                                                                                                   | `Cancelled.`                                                                             |
| `install.sh:335`                                                     | `Cancelled. Nothing was staged or installed.`                                                                                                                      | `Cancelled.`                                                                             |
| `install.sh:611-613`                                                 | `The archive has been extracted and symlinked.` / `Shell integration runs next — if the step below fails,` / `re-run the install command.`                         | _(deleted — replaced by the finish card, §6.1)_                                          |
| `install.sh:231-236`                                                 | `Runtime root contract failed fast.` + 5-line manual cleanup essay                                                                                                 | `✗ runtime root drift: <got> ≠ <expected>` + `→ fix: vibecrafted doctor --fix-launchers` |
| `install.sh:254/259/265`                                             | `Fail-fast: store/runtime/launcher root drift detected…`                                                                                                           | same `✗ … drift` pattern, one line each                                                  |
| `vetcoders_install.py:4336-4337`                                     | `Stopping install so stale ~/.local/share/vibecrafted/tools/vibecrafted-current cannot shadow fresh skills.`                                                       | `✗ could not refresh staged tools` + `→ fix: rerun \`vibecrafted update\``               |
| `vetcoders_install.py:4074/4084/4101`                                | `(recommended; Python fallback available)` / `(visible Terminal automation unavailable; non-visible fallback exists)` / `(not found — helpers will use bash only)` | status glyph only; fallback notes move to `--verbose`                                    |
| `install-foundations.sh:930`                                         | `Agent CLI bootstrap incomplete; continuing because agents are optional during foundation install.`                                                                | `! agent CLIs 3/5 — optional, install later: vibecrafted doctor`                         |
| compact checkpoint `REASON` lines (`vetcoders_install.py:4668-4677`) | `REASON  This keeps the terminal readable while the full transaction log stays on disk.`                                                                           | _(deleted — installers don't explain their own typography)_                              |

General rule: **no line may describe what the tool did NOT do, will NOT do, or
might have done.** If a guarantee matters (no dotfile edits), it is stated once in
`INSTALL.md`, not printed at runtime.

---

## 5. Golden paths (each = 1 command, bounded output)

1. **Install** — `curl -fsSL vibecrafted.io/install.sh | sh`
   → one bounded screen: 4 stage lines → finish card (§6.1) with one next step.
2. **Orient** — `vibecrafted init claude`
   → spinner stages (scanning repo · building context) → agent session opens.
3. **Ship** — `vibecrafted implement codex -p "…"`
   → `✓ dispatched codex · run deco-173747` + dim line with report path and
   `→ next: vibecrafted status`.

A first-time user never needs a fourth command before seeing value.

---

## 6. Before → after

### 6.1 End of install

**Before** (today): _nothing_ — `install.sh` line 701 `exec`s into the Python
installer; no completion signal exists anywhere in install.sh or the Makefile.

**After** (the finish card, printed by the last stage owner — the Python installer):

```
✓ 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. 3.1.0 installed

  skills 17 · agents claude codex gemini · store ~/.vibecrafted

  → vibecrafted init claude       start here
  → vibecrafted doctor            verify
```

Six lines. The old 20-line summary box (hammer banner, tagline, product line,
copyright, `🅰·🅱·🅲` alphabet) moves to `--verbose`.

### 6.2 Consent prompt

**Before** (install.sh:313-320, 8 lines + retry loop):

```
This bootstrap will:
  • download vibecrafted (main) from GitHub
  • stage the control plane under ~/.local/share/vibecrafted/tools/vibecrafted-main
  • refresh the current symlink at .../vibecrafted-current
  • launch the guided installer
Nothing will be staged or installed until you say yes.
Proceed? [y/N]
```

**After** (4 lines):

```
⚒ 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. 3.1.0 → ~/.local/share/vibecrafted
  download · stage · launch installer
Proceed? [y/N]
```

### 6.3 Error

**Before** (`vetcoders_install.py:4333-4339`):

```
  [missing] Could not refresh staged tools: <OSError …>
  Stopping install so stale ~/.local/share/vibecrafted/tools/vibecrafted-current cannot shadow fresh skills.
```

**After:**

```
✗ could not refresh staged tools
  → fix: rerun `vibecrafted update`
  log: ~/.vibecrafted/install.log
```

### 6.4 Doctor

**Before:** 50–80 finding lines, no header, no verdict — the user reads everything
to learn whether they are healthy.

**After:**

```
⚒ doctor — 24 checks
✓ 21 ok   ! 2 warnings   ✗ 1 failure

✗ launcher drift: ~/.cargo/bin/vc-implement shadows ~/.local/bin
  → fix: vibecrafted doctor --fix-launchers
! loctree missing — optional foundation         docs/FOUNDATION.md
! shell helpers not on PATH — optional          vibecrafted help shell

details: vibecrafted doctor --verbose
```

Verdict in line 2, in under two seconds. Passing checks are a count.

### 6.5 `make help`

**Before:** banner + 11 advertised targets across three visual styles, while 51
more targets are invisible anyway.

**After:**

```
⚒  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. 3.1.0

  make install      Guided install
  make doctor       Health check
  make update       Pull latest + reinstall
  make uninstall    Reverse the install
  make test         Run the gates
  make check        Lint shell scripts

  dev targets: make help-dev
```

### 6.6 Skill install loop

**Before:** one `-> vc-<skill>` line per skill (~17–25 lines) plus per-runtime
symlink chatter (~100 logged operations).

**After:** `⠼ installing skills 12/17` (single live line) → `✓ skills 17 installed`.

---

## 7. Implementation order (six bounded cuts)

1. **`scripts/lib/vc_ui.sh` + `vibecrafted_core/ui.py`** — the token/template
   library (spinner, ✓/✗/!, stage lines, TTY/NO_COLOR gating). Everything else
   consumes it.
2. **`scripts/vibecrafted` help rewrite** — §2 deck, `help --all` reorg, alias
   de-advertising, stderr discipline for all errors.
3. **Makefile** — `help` cut to six targets, new `help-dev`, unify echo styles
   onto vc_ui glyphs.
4. **`install.sh`** — rhetoric purge (§4), consent compaction (§6.2), adopt
   stage lines; hand off knowing the Python installer prints the finish card.
5. **`vetcoders_install.py`** — compact-by-default (drop dual narration),
   finish card (§6.1), skill-loop counter, doctor summary-first (§6.4),
   `--verbose`/`--debug` strict modes.
6. **Runtime modules** (`loop.py`, `cron.py`, `ship.py`, foundations/runtime
   shells) — adopt `ui.py`; cron keeps JSON on stdout **only** under `--json`,
   prints the human summary otherwise.

Each cut is independently shippable and testable (`make test-install`,
`tests/` parity suites). Verification for every cut: run the touched golden
path in a real terminal + `NO_COLOR=1` + non-TTY pipe, and confirm bounded
line counts.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. — premium is not ornament. Premium is coherence._
