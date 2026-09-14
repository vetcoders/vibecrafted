# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. FAQ — ANSWERED

Answers from the trenches. This is the truth as of April 2026.

## Installation

- **Why does the installer write to `$VIBECRAFTED_ROOT/.vibecrafted/` and not `$HOME/.agents/` like other tools?**
  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. is the _orchestrator_, not just a collection of scripts. `$HOME/.agents/` is the old dumping ground for
  standalone agent configs. `$VIBECRAFTED_ROOT/.vibecrafted/` is the central command store where the actual skill source code lives,
  where artifacts are archived, and where the multi-agent state is managed. We separate the _source_ (vibecrafted) from
  the _view_ (the symlinks in agent-specific dirs).

- **What happens to my existing skills in `$HOME/.agents/skills/` after installation?**
  The installer is surgical. It detects old `vetcoders-*` skills and offers to prune them. If you have custom,
  non-Vetcoders skills there, it leaves them alone. 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. skills are symlinked into `$HOME/.agents/skills/` (and
  others) so your agents "see" them, but the source of truth remains in `$VIBECRAFTED_ROOT/.vibecrafted/`.

- **Why does `make install` run an interactive installer wizard instead of just installing silently?**
  Because the default human front door should show the machine shape before it mutates it. `make install` runs the
  terminal-native installer wizard — the shell-first default front door. It checks foundations, runs the repo-owned
  compact installer truth with a quiet progress surface, and leaves a plain-language `START_HERE.md` behind. If you prefer the browser surface,
  run `make wizard` (or its alias `make gui-install`). For a direct non-interactive install path, use `make install-auto` or
  call `python3 scripts/vetcoders_install.py install --source "$PWD" --non-interactive`.

- **Can I install 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. without giving it write access to my shell rc files?**
  Yes. You can opt-out of the shell-helper layer during the interactive install. You'll just need to manually source the
  helper file (`${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/vc-skills.sh`) if you want the high-level aliases like
  `vc-init`.

- **What if I already have a Starship/Atuin config — will 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. overwrite it?**
  No. It detects existing configs and prompts you. It can install the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. versions alongside yours or skip them
  entirely. Vibecrafted's own presets live only in `~/.config/vibecrafted/`; it never reads or writes your own
  Starship/Atuin configuration, and nothing launches a dashboard unless you ask for it.

- **How do I move my installation to a custom directory?**
  Set `VIBECRAFTED_HOME` in your environment before running the installer. The installer respects this variable for the
  central store.

- **Why is Gemini excluded from symlink creation by default?**
  Gemini CLI often duplicates the workflow of other agents or inherits skills differently. To avoid path noise and
  redundant discovery cycles in multi-model environments, we keep the symlink views to the "primary" trio (agents,
  claude, codex) unless you explicitly opt-in during advanced install.

- **What does `make doctor` actually check?**
  It verifies: 1) Central store integrity, 2) Symlink health (no broken links), 3) Foundation binaries (aicx, loctree,
  prview, etc.), 4) Shell helper availability, and 5) "Dumb terminal" quietness — ensuring your interactive shell
  doesn't leak noise that breaks agent parsers.

- **How do I completely remove 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. from my system?**
  `make uninstall`. It uses the install manifest to reverse symlinks, remove the central store, and clean up the shell
  helper source lines in your `.bashrc` or `.zshrc`. It even offers to restore the pre-install backup.

- **Why does the installer back up my existing state before every install?**
  Because things break. We snapshot your shell rc files, existing skills, and symlinks into
  `$VIBECRAFTED_ROOT/.vibecrafted/skills/.backup/` before touching anything. Safety over speed.

- **I pulled new commits — is my install updated?**
  No. The daily `vibecrafted` CLI runs the **staged tools home**
  (`~/.local/share/vibecrafted/tools/vibecrafted-current/`), not the floating git
  checkout. Re-run `make install` or `make install-auto` and confirm
  `VERSION` / `vibecrafted --version` share the same `+g<sha>` as
  `git rev-parse --short HEAD`. See [INSTALL.md](INSTALL.md) and
  [runtime/TRIAGE_AND_SESSIONS.md](runtime/TRIAGE_AND_SESSIONS.md).

## Skills & Agents

- **What is the difference between a skill and an agent?**
  An **Agent** is the runtime (Claude, Codex, Gemini) with its personality and tools. A **Skill** is a specialized
  _instruction set + protocol_ (found in `SKILL.md`) that tells an agent how to behave for a specific engineering
  phase (e.g., `vc-workflow`). Think of agents as the "brains" and skills as the "manuals" they follow.

- **Why can't I just use ChatGPT/Copilot instead of this framework?**
  You can, if you want a chat box. 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. is for building _systems_. It provides structural awareness (loctree),
  decision history (aicx), and a rigorous iterative loop (marbles). ChatGPT doesn't know your codebase's architecture;
  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. ensures the agent sees the 360-degree view before it touches a single line of code.

- **How does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. decide which AI model to use for a task?**
  It doesn't "decide" for you; it provides the _mechanics_ to run them. However, our workflows generally prefer **Codex
  ** for precision coding, **Claude** for deep investigative research, and **Gemini** for creative synthesis or
  high-volume data processing. `vc-agents` spawns the "frontier fleet" to get all three perspectives.

- **What is the Marbles loop and why does it exist?**
  It's an iterative denoising loop. AI is stochastic—it produces noise along with code. The Marbles loop (implement →
  followup → measure → repeat) runs until the "circle is full" (P0/P1/P2 findings = 0). It exists because "one-shot" AI
  generation is a myth for anything complex.

- **Why do agents sometimes produce worse code than a single prompt?**
  Context drift and "lazy" generation. If an agent isn't anchored by structural truth (loctree) or is overwhelmed by a
  200k token history, it starts hallucinating. 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.'s `vc-init` and `vc-workflow` combat this by providing
  surgical, high-signal context rather than a history dump.

- **How does `vc-followup` decide between P0, P1, and P2 severity?**
  - **P0**: Blocker. Code doesn't compile, critical security leak, or core feature is fundamentally broken.
  - **P1**: High Risk. Regression likely, edge cases unhandled, or architectural mismatch.
  - **P2**: Polish/Gap. Missing tests, suboptimal naming, observability gaps, or minor UI jank.

- **What happens when two agents edit the same file at the same time?**
  We follow the "Living Tree" rule. Agents are trained to re-read files before editing. However, 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.
  orchestration (like `vc-agents`) typically handles sequential execution or uses git-level isolation to prevent
  clobbering.

- **Can I add my own custom skills to the framework?**
  Absolutely. Drop a folder with a `SKILL.md` into `skills/` and run `make install`. The `skill-creator` skill can guide
  you through the process.

- **Why is there no `vc-test` skill?**
  Testing isn't a "skill"—it's a requirement of _every_ skill. `vc-workflow`, `vc-implement`, `vc-justdo`,
  and `vc-marbles` all have testing and validation baked into their "Execution" and "Validate" phases.

- **What is the Definition of Undone and why is it not the Definition of Done?**
  Definition of Done (DoD) is about checking boxes. **Definition of Undone (DoU)** is about exposing the gaps you
  _didn't_ think to check: SEO, installability, legal boilerplate, product identity. It's a "plague check" for the
  entire product surface.

## Architecture

- **Why are skills stored centrally instead of per-project?**
  To prevent "skill drift." If every project has its own version of `vc-workflow`, updates become impossible. Central
  storage allows you to improve your agent's "brain" once and have it apply across all your repos.

- **What is the relationship between `$VIBECRAFTED_ROOT/.vibecrafted/skills/`, `$HOME/.claude/skills/`, and `$HOME/.agents/skills/`?**
  - `$VIBECRAFTED_ROOT/.vibecrafted/skills/`: The **Central Store** (Source of Truth).
  - `$HOME/.claude/skills/`, `$HOME/.agents/skills/`: **Symlink Views**. These are portals that let specific agent CLIs find
    the skills. They point back to the Central Store.

- **Why does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. use symlinks instead of copying skill files?**
  Instant updates. If you change a skill in the central store, every agent on your system gets the new version
  immediately without a re-install.

- **What is the `$VIBECRAFTED_ROOT/.vibecrafted/artifacts/` directory for?**
  Persistence. Agents are ephemeral; their logs and reports shouldn't be. Every major run (review, research, workflow)
  dumps a structured report here so you have a "paper trail" of AI decisions.

- **Why do artifact paths include the date?**
  For the "Perception over Memory" philosophy. It's easier to find "what the agent thought on Tuesday" by looking at a
  folder than by searching a multi-gigabyte vector database.

- **How does the central store know which GitHub org/repo I'm working in?**
  It detects it via `git remote` and organizes artifacts accordingly: `$VIBECRAFTED_ROOT/.vibecrafted/artifacts/<org>/<repo>/...`.

- **What is `VIBECRAFTED_HOME` and when would I change it?**
  It defaults to `$VIBECRAFTED_ROOT/.vibecrafted`. You'd change it if you want to store your 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. data on an external drive, a
  synced Dropbox folder, or in a shared team location.

- **Why is there a `config/` directory with Starship and Atuin configs?**
  Because the "Operator UX" matters. 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. keeps a small, honest operator layer in-repo: prompt context and
  history recall. That same layer now includes optional vc-frame config and branded layouts, while terminal-emulator
  presets such as Alacritty stay outside the core repo surface.

- **What is mise and why does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. include a `mise.toml`?**
  `mise` (formerly `rtx`) handles the toolchain. It ensures that the specific versions of Python, Rust, or Node needed
  for the foundations (like `loctree`) are present without polluting your global system.

## Runtime Foundations

- **What is loctree and why does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. depend on it?**
  `loctree` is the agent's eyes. It provides structural code intelligence (who imports what, where are the hubs, what
  breaks if I change this). Without it, the agent is just guessing based on filenames.

- **What is aicx-mcp and why is it called a "decisions retrieval engine" not a "memory system"?**
  Because "memory" implies fuzzy recall. `aicx` (AI Contextualizer) is a deterministic engine that retrieves _prior
  decisions_ and _context chunks_ based on the current task. It's built for precision, not nostalgia.

- **Are loctree and aicx required?**
  No. You can still run isolated shell scripts, but that is not the Vibecrafted operating model. `loctree` is required
  for structural perception and `aicx` is required for intention/session continuity. Install the foundations first,
  then trust the workflow.

- **Why does prview generate artifacts instead of just printing to terminal?**
  So they can be consumed by _other_ agents. A terminal print is lost. A `report.json` or `findings.md` can be read by a
  followup agent to fix the issues discovered during review.

- **What is Screenscribe and when would I use it instead of a bug report?**
  Screenscribe turns screen recordings (narrated bug demos) into structured engineering findings. Use it when "it's
  broken" is easier to show than to type. It's the ultimate "bridge" from product to engineering.

## Workflow

- **What does "Craft, Converge, Ship" actually mean in practice?**
  - **Craft**: Research, scaffold, and implement the initial "noise" (rough code).
  - **Converge**: Run Marbles loops to denoise the code, fix bugs, and fill gaps until P0/P1/P2 = 0.
  - **Ship**: Run `vc-dou`, hydrate the product (docs, SEO), and push to market.

- **How long does a typical Marbles convergence loop take?**
  A single iteration (followup + fix) takes 2-5 minutes. A task typically converges in 2-4 loops. Massive refactors
  might take 10+.

- **When should I use `vc-implement` vs `vc-justdo` vs running individual skills manually?**
  Use `vc-implement` when the task is a clear **ship WRITE** cut and you want structured e2e delivery (followup +
  marbles built in). Use `vc-justdo` for **standalone Just Do posture**: no ceremony, task type defined by the prompt
  (implement / review / audit / research / fix / …) — not a ship stage and not an implement alias (ADR-0001). Use
  individual skills (init → workflow → followup) when you want to supervise the architectural "cuts" at each step.

- **What is the difference between `vc-review` and `vc-followup`?**
  `vc-review` is bounded: give it a PR, branch diff, commit range, or review artifact pack and expect findings-first
  review output. `vc-followup` is post-implementation and directional: use it when a pass has landed and you need to know
  what drift remains, whether the trajectory is healthy, and what the next move should be across code, runtime, UX, docs,
  packaging, or launch readiness.

- **How do I know when convergence is "done"?**
  When P0=0, P1=0, and P2=0 in the `vc-followup` report, and the Build/Lint/Test gates are all green. That's the signal
  to stop.

- **What is the ralph-loop and how does it relate to vc-marbles?**
  Named after the "Ralph Wiggum" technique (Geoffrey Huntley), it's the underlying bash mechanic (`while true`) that
  powers iterative AI loops. `vc-marbles` is the sophisticated, score-driven version of this simple persistent loop.

- **Can I run 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. in CI/CD or is it only for interactive use?**
  Yes. Use the direct non-interactive install path (`make install-auto` or
  `python3 scripts/vetcoders_install.py install --source "$PWD" --non-interactive`). `vc-review` and `vc-followup` are
  designed to run as quality gates in CI pipelines.

- **Why is the SESSIONS rail still `f · 0 x · 0 n · 0` when many runs completed?**
  Those counters count **tabs in bucket sessions** (`Finalized runs` /
  `Failed runs` / `Needs attention`) after `vc-frame triage-run`, not
  control-plane `completed` rows. Settlement without triage leaves finished
  tabs in the work session. Full diagnostic list:
  [runtime/TRIAGE_AND_SESSIONS.md](runtime/TRIAGE_AND_SESSIONS.md).

- **How does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. handle merge conflicts between parallel agents?**
  By emphasizing "Surgical Edits." 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. encourages small, targeted changes. If conflicts happen, the
  `vc-marbles` loop detects the "divergence" (entropy increase) and triggers a re-examination.

## For Skeptics

- **Is this just a fancy prompt wrapper?**
  No. It's a structural intelligence layer. It combines static analysis (loctree), deterministic retrieval (aicx), and a
  rigorous iterative methodology (marbles). A prompt wrapper doesn't know your dependency graph; 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. does.

- **Can two veterinarians really build enterprise software with AI?**
  We don't know yet. We're building. What we do know: our production app has 300k LOC, a real licensing system, real
  users, real data. Whether that's "enterprise" is for someone else to decide. We just needed it to work.

- **Why should I trust a framework built by people who aren't professional programmers?**
  We started the same way everyone starts — playing with AI toys, pasting prompts, hoping. The framework exists because
  hoping didn't scale. Trust it or don't — the code is open, the methodology is documented, the results are measurable.

- **What makes 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. different from AutoGPT/CrewAI/LangChain agents?**
  - **AutoGPT**: May be too chaotic; some claims that it lacks structural anchoring.
  - **CrewAI**: Great for roles, but lacks the "denoising" rigor of the Marbles loop.
  - **LangChain**: A library for building tools, not a workflow for shipping products.
  - **𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.**: A complete pipeline (Research → Strategy → Execution → Convergence → DoU) focused on the
    _entire_ product surface.

- **Does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. actually work on large codebases or only small projects?**
  In a small project, you can fit the whole thing in context and an agent can manage. The challenge starts when you
  approach or go beyond 100k LOC — dead code, circular imports, invisible dependencies. That's where `loctree` and
  `aicx` provide real leverage. Our production app has 300k LOC. That's where we live.

- **Why is the marble metaphor useful and not just marketing?**
  It's about **entropy**. In 2026, we've accepted that AI output is "noisy." The marble metaphor (filling the circle)
  correctly frames development as a process of _reducing uncertainty_ rather than _writing lines_.

---

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. by Vetcoders | https://vibecrafted.io/
