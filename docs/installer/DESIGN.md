# UV-Style Installer Transformation

> Status note (2026-04-10): the public shipping front door is now the
> browser-guided installer at `scripts/installer_gui.py`, not a full-screen TUI.
> Keep this file as a reference for compact output rhythm and installer trust
> surfaces, not as the default public onboarding contract.

## Goal

Transform the𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. installer from a 3-screen wall of text into a
compact, progressive build-up that fits on ONE terminal screen. Like how
`uv` (Astral's Python package manager) installs — the output IS the result,
not the log of the process.

## Current Problem

`make install` used to output ~80 lines across:

-𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Framework Setup header + plan (8 lines)

- Y/n prompt
- "Installing the shared skill store" block (3 lines)
- "Running Core Installer..."
- Installer banner + separator (3 lines)
- Bundle contents with all 16 skills listed individually (18 lines)
- System check with 4 deps listed (6 lines)
- Agent runtimes with 3 agents listed (5 lines)
- Gemini note (2 lines)
- Y/n prompt for agent views
- Runtime Foundations with 9 foundations (12 lines)
- Plan summary (4 lines)
- Y/n prompt
- Backup saved (2 lines)
- "Installing shared skills..." with 16 lines of -> skill names
- "Linking agent views..." with 3 lines
- "Installing shell helper..." with 3 lines
- Manifest saved (1 line)
- Verification (2 lines)
- Unicode summary box (14 lines)

Total: ~80+ lines. User scrolls past everything to see the result.

## Target

- Keeping the all each step, but display it as one screen as the result (~20-25 lines max).
- Visualize the results as they go but the summary is built from curated list of lines.
- During proces Each line appears on the visible portion but only **some** STAYS as a summary.
- They are built from current lines.
- The final state on screen IS the complete summary but the details are hidden and can be shown by pressing ->| key.
- Introduce the installer's $VIBECRAFTED_ROOT/.vibecrafted/logs/install.log.

---

## Current setup:

```bash
 make setup-dev

  ⚒  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Installer
  ─────────────────────────────────
  Source: ~/hosted/vetcoders/vetcoders-skills
  Version: 1.0.4

Bundle contents:
  Vetcoders Pipeline (16)
    - vc-agents
    - vc-decorate
    - vc-delegate
    - vc-dou
    - vc-followup
    - vc-hydrate
    - vc-implement
    - vc-init
    - vc-justdo (standalone Just Do posture; not implement)
    - vc-marbles
    - vc-partner
    - vc-prune
    - vc-release
    - vc-research
    - vc-review
    - vc-scaffold
    - vc-workflow

Select skills to install:
  (Use UP/DOWN to navigate, SPACE or number to toggle, ENTER to confirm)
  > [x] 1. vc-agents
    [x] 2. vc-decorate
    [x] 3. vc-delegate
    [x] 4. vc-dou
    [x] 5. vc-followup
    [x] 6. vc-hydrate
    [x] 7. vc-implement
    [x] 8. vc-init
    [x] 9. vc-justdo  (standalone Just Do posture; not implement)
    [x] 10. vc-marbles
    [x] 11. vc-partner
    [x] 12. vc-prune
    [x] 13. vc-release
    [x] 14. vc-research
    [x] 15. vc-review
    [x] 16. vc-scaffold
    [x] 17. vc-workflow

System check:
  [ok] python3 -> /opt/homebrew/bin/python3
  [ok] git -> /opt/homebrew/bin/git
  [ok] rsync -> /opt/homebrew/bin/rsync
  [ok] osascript -> /usr/bin/osascript

  [optional] zsh (not found — helpers will use bash only)
Agent runtimes:
  [ok] codex -> /opt/homebrew/bin/codex
  [ok] claude -> ~/.local/bin/claude
  [ok] gemini -> /opt/homebrew/bin/gemini

  Note: gemini-cli in some versions duplicates the workflows, inheriting
  skills from the other agents. Gemini symlinks skipped by default.
Select runtimes for symlink views:
  (Use UP/DOWN to navigate, SPACE or number to toggle, ENTER to confirm)
  > [x] 1. agents
    [x] 2. claude
    [x] 3. codex
    [ ] 4. gemini

Runtime Foundations:
  [ok] aicx-mcp -> <product-managed path>/aicx-mcp
       AICX MCP server for session history and intentions recovery
  [ok] loctree-mcp -> <product-managed path>/loctree-mcp
       Structural code mapping MCP server
  [ok] prview -> ~/.cargo/bin/prview
       PR review artifact generator
  [ok] screenscribe -> ~/.local/bin/screenscribe
       Screencast analysis — turns narrated recordings into structured engineering findings
  [ok] mise -> /opt/homebrew/bin/mise
       Repo-owned toolchain, environment, and task substrate
  [ok] starship -> /opt/homebrew/bin/starship
       Cross-shell prompt/status line for operator UX
  [ok] atuin -> /opt/homebrew/bin/atuin
       Shell history recall with optional encrypted sync
  [ok] zoxide -> /opt/homebrew/bin/zoxide
       Fast directory jumping for agent-heavy shell workflows

Enable the shell helper layer (bash + zsh)? [y/N] y

Plan:
  Skills:    16 -> ~/.vibecrafted/skills
  Runtimes:  agents, claude, codex (symlink views)
  Shell:     yes

Install this plan? [Y/n] y

Saving current state...
  [ok] Backup saved: ~/.vibecrafted/skills/.backup/20260328_152106

Installing shared skills...
  -> vc-agents
  -> vc-decorate
  -> vc-delegate
  -> vc-dou
  -> vc-followup
  -> vc-hydrate
  -> vc-implement
  -> vc-init
  -> vc-justdo (standalone Just Do posture; not implement)
  -> vc-marbles
  -> vc-partner
  -> vc-prune
  -> vc-release
  -> vc-research
  -> vc-review
  -> vc-scaffold
  -> vc-workflow

Linking agent views...
  agents -> ~/.agents/skills
  claude -> ~/.claude/skills
  codex -> ~/.codex/skills

Orphaned skills detected (no longer in bundle):
  [dir] store/vc-screenscribe
  [symlink] agents/vc-screenscribe
  [symlink] claude/vc-screenscribe
  [symlink] codex/vc-screenscribe

Remove orphaned skills? [Y/n] y
  [ok] Removed 4 orphaned entries

Installing shell helper...
Installing Vetcoders shell helpers
  source: ~/hosted/vetcoders/vetcoders-skills/runtime/shell/vetcoders.sh
  target: ~/.config/vibecrafted/shell/vc-skills.sh
  ~/.zshrc: already sourced


  [ok] Install manifest saved to ~/.vibecrafted/skills/.vc-install.json

Verification:
  [ok] All checks passed


  ⚒ Ｖｉｂｅｃｒａｆｔｅｄ. ⚒

 𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. (𝚟𝚌-𝚌𝚕𝚒) 𝚟1.0.4
  ─────────────────────────────────────

  ✓ Skills       16 installed
  ✓ Agents       claude · codex · gemini
  ✓ Helpers      zsh
  ✓ Foundations   aicx-mcp · loctree-mcp · prview +6
  ✓ Store        $VIBECRAFTED_ROOT/.vibecrafted/skills

  ─────────────────────────────────────
    Start        vibecrafted help
    Verify       vibecrafted doctor
    Reverse      vibecrafted uninstall

    🅵·🅁·🄰·🄼·🄴·🅆·🅞·🅡·🅺
```

## Target flow for interactive mode (`make install`):

- I. Upper portion - Hero

```bash
─────────────────────────────────────────────────────────
              ⚒ Ｖｉｂｅｃｒａｆｔｅｄ. ⚒
             𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. (𝚟𝚌-𝚌𝚕𝚒) 𝚟1.0.4
─────────────────────────────────────────────────────────
```

- II. Middle section - dynamic

```bash
  Welcome to The 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.

  This setup will install and configure The Framework
  and all of its required foundation packages and
  tooling.

  This setup will stay inside temporary folder
  and unless you accept all the consents, won't
  make any modifications in your system.

  Each step says what changes, why it matters, and
  we will provide you a summary before we touch your
  filesystem. Do do it so friendly as we want it to
  keep we need to checkout some things if you agree.

  This installer will guide you through the setup but
  it won't explain what the Framework is, what it does,
  why you need it, or why you should use it.
  If you are here you probably already know what that.
  If not - you can read about it here:
  https://vibecrafted.io
```

III. Action prompt or progress bar

```bash
                Press ⏎ Enter to proceed or Esc to quit
```

- IV. Footer with nav helpers

```bash
─────────────────────────────────────────────────────────
↑↓ Nav | ␣ Sel |  ⏎ Next |  ⌫ Back |  ⇥ View | ⎋ Quit # Alternatively in the small window mode: ⌥ Shortcuts
              🅵·🅁·🄰·🄼·🄴·🅆·🄞·🅁·🅺
─────────────────────────────────────────────────────────
```

---

## THE FLOW STEP BY STEP:

- 0. Welcome step

```shell
─────────────────────────────────────────────────────────
              ⚒ Ｖｉｂｅｃｒａｆｔｅｄ. ⚒
             𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. (𝚟𝚌-𝚌𝚕𝚒) 𝚟1.0.4
─────────────────────────────────────────────────────────
  Welcome to The 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.

  This setup will install and configure The Framework
  and all of its required foundation packages and
  tooling.

  This setup will stay inside temporary folder
  and unless you accept all the consents, won't
  make any modifications in your system.

  Each step says what changes, why it matters, and
  we will provide you a summary before we touch your
  filesystem. Do do it so friendly as we want it to
  keep we need to checkout some things if you agree.

  This installer will guide you through the setup but
  it won't explain what the Framework is, what it does,
  why you need it, or why you should use it.
  If you are here you probably already know what that.
  If not - you can read about it here:
  https://vibecrafted.io

                Press ⏎ Enter to proceed or Esc to quit
─────────────────────────────────────────────────────────
↑↓ Nav | ␣ Sel |  ⏎ Next |  ⌫ Back |  ⇥ View | ⎋ Quit # Alternatively in the small window mode: ⌥ Shortcuts
              🅵·🅁·🄰·🄼·🄴·🅆·🄞·🅁·🅺
─────────────────────────────────────────────────────────
```

- I. Explain step

```shell
─────────────────────────────────────────────────────────
              ⚒ Ｖｉｂｅｃｒａｆｔｅｄ. ⚒
             𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. (𝚟𝚌-𝚌𝚕𝚒) 𝚟1.0.4
─────────────────────────────────────────────────────────
  Craftsmanship is about making things useful, handy and
  beautiful. 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. is a craft for the code.
  We mainly use the cli for the daily coding.
  We believe though that the cli shouldn't be
  unfriendly.
  We created this installer to make it friendly for you.
  You can simply follow the instructions and be sure
  we won't do any action without your explicit consent
  primarly explaining it to you.



  Quick overview of the navigation keys:
    ⇅  Arrows       to navigate
    ␣  Space        to select
    ⏎  Enter        to proceed
    ⌫  Backspace    to go back
    ⇥  Tab          to view details
    ⎋  Escape       to quit
    ⌥  Option/Alt   to show shortcuts


                                ⏎ proceed ⌫ Back ⎋ quit
─────────────────────────────────────────────────────────
⇅ Nav | ␣ Sel |  ⏎ Next |  ⌫ Back |  ⇥ View | ⎋ Quit
              🅵·🅁·🄰·🄼·🄴·🅆·🄞·🅁·🅺
─────────────────────────────────────────────────────────
```

- II. Installer checklist plan:

```shell
─────────────────────────────────────────────────────────
              ⚒ Ｖｉｂｅｃｒａｆｔｅｄ. ⚒
             𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. (𝚟𝚌-𝚌𝚕𝚒) 𝚟1.0.4
─────────────────────────────────────────────────────────
  Installer checklist:
  1. Diagnostics
  We will check your environment for:
    - Any existing framework installation
      foundations · workflows · helpers · binaries
    - Toolchains
      node · python · rust
    - Agents
      claude-code · codex · gemini-cli
    - Additional tools
      vc_frame · mise · starship · atuin · zoxide
  2. Installation
  We will install:
    - Missing dependencies
    - Workflows (9 basic, 7 advanced)
    - Helpers and binaries (executables and functions)
  3. Post-installation setup
    - Workspace directories (in $VIBECRAFTED_ROOT/.vibecrafted/)
    - Symlinks for AI coding agents (in $HOME/.runtime/)
  4. Verification and quick tour

                                ⏎ proceed ⌫ Back ⎋ quit
─────────────────────────────────────────────────────────
⇅ Nav | ␣ Sel |  ⏎ Next |  ⌫ Back |  ⇥ View | ⎋ Quit
              🅵·🅁·🄰·🄼·🄴·🅆·🄞·🅁·🅺
─────────────────────────────────────────────────────────
```

---

> The other steps are to be referenced from the detailed mockups within this folder.

---

# Target for non-interactive mode (`make install-auto`):

Same output but without Y/n prompts — just runs and prints the final state.

## Architecture

### setup_vibecrafted.py (interactive orchestrator)

Simplify to:

1. Print header + plan (keep, but shorter)
2. Ask Y/n (one prompt, not three)
3. Call vetcoders_install.py with `--compact` flag
4. Done — no duplicate summary (already removed)

### vetcoders_install.py changes

Add a `--compact` flag (default True when called from setup_vibecrafted.py).

When compact:

- **System check**: run silently. If any critical dep missing, print error and stop.
  Otherwise don't print individual checks.
- **Bundle contents**: don't list 16 skill names. Just count.
- **Agent runtimes**: detect silently. Print one summary line.
- **Foundations**: detect silently. Print count + first 3 names.
- **Plan**: don't print the plan block or ask Y/n (setup_vibecrafted already asked).
- **Installing skills**: don't print 16 `-> skill` lines. Print one line when done.
- **Linking agent views**: silent.
- **Installing shell helper**: silent (or one line).
- **Manifest saved**: silent.
- **Verification**: run silently. If issues, print them. Otherwise nothing.
- **Summary**: print the unicode box (already done).

When NOT compact (direct `make install` or `python3 installer.py`):

- Keep current verbose output as fallback

### Verbose log

All the verbose output that gets suppressed in compact mode should go to:
`$VIBECRAFTED_ROOT/.vibecrafted/logs/installer/install.log`

The user can always check it if something went wrong.

## Implementation Steps

### 1. Add --compact flag to vetcoders_install.py

In the argument parser, add:

```python
p_install.add_argument("--compact", action="store_true",
    help="Compact output — one screen, details to log")
```

### 2. Create a log redirect mechanism

```python
import io

class TeeLogger:
    """Captures print output to a log file while optionally suppressing stdout."""
    def __init__(self, log_path, quiet=False):
        self.log = open(log_path, 'w')
        self.quiet = quiet
        self.stdout = sys.stdout

    def write(self, text):
        self.log.write(text)
        if not self.quiet:
            self.stdout.write(text)

    def flush(self):
        self.log.flush()
        if not self.quiet:
            self.stdout.flush()
```

### 3. Restructure cmd_install flow for compact mode

When `--compact`:

1. Redirect stdout to TeeLogger (quiet=True) for the duration of install
2. Run all the existing logic (system check, skill discovery, etc.) silently to log
3. After each phase completes, print ONE compact line to real stdout
4. At the end, print the unicode summary box

The compact lines use the same data that's already computed — just format
differently.

### 4. Update setup_vibecrafted.py

Remove the "Installing the shared skill store" block (What/Reason/Safe).
Pass `--compact` to the underlying installer.
Keep only: header, plan, Y/n, then let installer handle the rest compactly.

### 5. Single Y/n prompt

Currently there are 3 prompts in interactive mode:

- "Start setup?" (setup_vibecrafted.py)
- "Create the default skill views?" (vetcoders_install.py)
- "Install this plan?" (vetcoders_install.py)

Reduce to ONE in setup_vibecrafted.py: "Start setup?"
The installer runs without additional prompts when called with --compact.

## Files to Modify

1. `scripts/vetcoders_install.py` — add --compact, TeeLogger, compact output
2. `scripts/setup_vibecrafted.py` — simplify, pass --compact
3. Leave `make install` (non-interactive) behavior as-is unless --compact is added

## What NOT to Change

- Core install logic (backup, rsync, symlinks, state save)
- Doctor check logic
- Uninstall/restore logic
- The unicode summary box (already good)
- Shell helper installation logic
- Foundation detection logic

## Test

- `make install` fits on one screen (~25 lines)
- `make install-auto` still works without prompts
- `$VIBECRAFTED_ROOT/.vibecrafted/install.log` contains full verbose output
- `bash scripts/check-portable.sh` passes
- All 16 skills installed correctly
- Doctor passes after compact install

## Reference

Look at how `uv` (https://github.com/astral-sh/uv) handles install output:

- Each resolution step is a single line
- Progress updates overwrite in place
- Final state is clean and compact
- Errors break out of compact mode with full detail

At the end of the task, write your final human-readable report to this exact path:
~/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0328/reports/20260328_0458_0510_uv-style-installe
r_implement_claude.md

Keep streaming useful progress to stdout while you work. If you cannot write a
standalone report file, finish normally and let the transcript act as the fallback
artifact.

## When writing your report file, include YAML frontmatter at the top:

agent: claude
run_id: impl-000
prompt_id: 0510_uv-style-installer_implement_20260328
started_at: (ISO 8601 when you began)
model: (your model name)

---
