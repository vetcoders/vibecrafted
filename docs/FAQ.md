# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. FAQ

Public-facing answers for the questions people ask before they trust the framework.

For the public HTML version, see https://vibecrafted.io/en/faq/.
For the long-form answer bank, see [FAQ-ANSWERED.md](FAQ-ANSWERED.md).

## Installation

- **Why does 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. install into `$VIBECRAFTED_ROOT/.vibecrafted/` instead of `$HOME/.agents/`?**
  `$VIBECRAFTED_ROOT/.vibecrafted/` is the central store and control plane. Agent-specific directories are only views or symlink
  targets.

- **Can I install without editing my shell config?**
  Yes. You can opt out of shell-helper installation and source
  `${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/vc-skills.sh` manually when you want the helpers in your current session.

- **Do you have a guided GUI install path?**
  Yes. The public product is `Vibecrafted.app`, carried by the single signed and notarized canonical DMG on the [latest release](https://github.com/vetcoders/vibecrafted/releases/latest). Checkout-only `make wizard` and `make install` surfaces are maintainer tools, not alternative product installers.

- **What does `make doctor` check?**
  The doctor verifies the central store, helper availability, symlink health, required foundations (`loctree-mcp` and
  `aicx-mcp`), evidence tools such as `prview` and Screenscribe, and shell quietness.

- **Which install path should I use in CI?**
  Use `make install-auto` for the direct non-interactive path, or
  `python3 scripts/vetcoders_install.py install --source "$PWD" --non-interactive` when you want full CLI control.

- **I pulled new commits — is my install updated?**
  No. The daily CLI runs the **staged tools home**
  (`~/.local/share/vibecrafted/tools/vibecrafted-current/`), not the floating git
  checkout. Re-run `make install` (or `install-auto`) and confirm
  `VERSION` / `vibecrafted --version` show the same `+g<sha>` as
  `git rev-parse --short HEAD`. See [INSTALL.md](INSTALL.md) and
  [runtime/TRIAGE_AND_SESSIONS.md](runtime/TRIAGE_AND_SESSIONS.md).

## Skills, Agents, Foundations

- **What is the difference between a skill and an agent?**
  An agent is the runtime. A skill is the workflow protocol that tells that runtime how to behave for a specific
  engineering phase.

- **Why not just use a single giant prompt?**
  Because 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. is trying to solve system-shaping, not only chat convenience. It adds structural awareness,
  decision retrieval, convergence loops, and shipping audits.

- **Are loctree and aicx required?**
  Not honestly. `loctree` and `aicx` are required foundations: one gives the agent structural perception, the other
  restores prior decisions and intent. If either is missing, fix the foundation layer before trusting the workflow.

- **What is Marbles?**
  Marbles is the convergence loop: implement, follow up, measure, and repeat until the important classes of findings
  reach zero.

## Workflow and Operations

- **When should I use `vc-implement` vs `vc-justdo`?**
  Use `vc-implement` for a clear **ship WRITE** cut with structured e2e delivery (followup + marbles). Use
  `vc-justdo` for **standalone Just Do posture**: task type from the prompt, no ship-stage ceremony — not an
  implement alias (ADR-0001). Use phase skills individually when you want more supervisory control.

- **When should I use `vc-review` instead of `vc-followup`?**
  Use `vc-review` for a bounded review target: a PR, branch diff, commit range, or artifact pack. Use `vc-followup`
  after implementation when you need a broader direction audit across code, runtime, UX, docs, packaging, and the next
  highest-leverage move.

- **Can I run 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. in CI/CD?**
  Yes. The direct install path is non-interactive, and review/followup/release flows are shaped to work as repeatable
  gates.

- **What lives in `$VIBECRAFTED_ROOT/.vibecrafted/artifacts/`?**
  Plans, reports, transcripts, and metadata from major runs. The artifact store exists so agent work leaves durable
  evidence.

- **Why is the SESSIONS rail still `f · 0 x · 0 n · 0` when many runs completed?**
  Those counters count **tabs in bucket sessions** (`Finalized runs` /
  `Failed runs` / `Needs attention`) after `vc-frame triage-run`, not
  control-plane `completed` rows. Settlement without triage leaves finished
  tabs in the work session. See
  [runtime/TRIAGE_AND_SESSIONS.md](runtime/TRIAGE_AND_SESSIONS.md).

- **What is Definition of Undone?**
  DoU is the audit that checks whether people can discover, understand, install, trust, and adopt the thing, not only
  whether the codebase is healthy.

## Commercial Posture

- **Is 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. open source?**
  The framework is distributed under Business Source License 1.1, so the repo is visible and usable but not pure
  permissive open source.

- **Can small teams use it in production?**
  Yes. The Additional Use Grant allows individual developers and teams smaller than five people to use it in production
  as long as they are not offering a competitive hosted or embedded product.

- **What if I need broader commercial rights?**
  Read [LICENSE](../LICENSE) for the exact terms and contact path for alternative licensing arrangements.

---

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. by Vetcoders | https://vibecrafted.io/
