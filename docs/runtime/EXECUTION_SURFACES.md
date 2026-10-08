# Execution Surfaces

Vibecrafted has several ways to launch the same workflows. They are intentionally
different surfaces, not interchangeable assumptions.

## 1. Human Interactive Shell

Use this when a human operator is working in zsh, usually inside vc-frame.

- Surface: `vc-*` shell functions sourced from `vc-skills.sh`.
- Canonical helper source: `${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/vc-skills.sh`.
- Developer override: `VIBECRAFTED_ROOT=/path/to/Vibecrafted`.
- Installed fallback: `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/tools/vibecrafted-current`.

Interactive shell helpers may resolve before binaries. This is expected for a
human shell, but agents must not assume these functions exist.

**Checkout override ≠ staged daily driver.** A floating git tree can be newer
than the tools home the installed `vibecrafted` CLI actually loads. Finish hooks
(triage into SESSIONS `f · x · n`, origin stamp) only exist in the **installed**
wire — re-run install after runtime landings and match `VERSION` `+g<sha>` to
HEAD. Canon: [`TRIAGE_AND_SESSIONS.md`](./TRIAGE_AND_SESSIONS.md).

## 2. Installed Binary

Use this for headless execution, scripts, and agent subprocesses.

- Surface: `vibecrafted` on `PATH`.
- Preferred location: `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/bin/vibecrafted`.
- Compatibility location: `$HOME/.local/bin/vibecrafted`.

Before invoking a command, agents verify it with `command -v`. They do not
assume an interactive PATH or sourced shell helpers.

## 3. Active vc-frame User Session

Use this when the operator needs a real interactive agent TUI or wants to
observe durable worker state.

- Surface: `vibecrafted start`, `vibecrafted dashboard`, and slash-command style
  workflow prompts inside the active agent pane.
- State: vc-frame session state plus Vibecrafted control-plane events.
- Strength: human-owned PTY for `init`, `operator`, and bare interactive
  `resume`; visible projection of worker transcripts and settlement state.
- Limit: this is a live terminal surface, not a portable subprocess contract.

Ordinary workflow workers still launch through the installed headless runtime,
even when this session is live. vc-frame observes them; it does not own their
processes. Closing a viewer or the whole User Session must not stop a headless
worker. Use an explicit `terminal` / `visible` worker runtime only for a
provider path proven to require a TTY.

## 4. Sandbox Execution

Use this when the agent process should run through microsandbox.

- Surface: workflow launch with `--sandbox`.
- Runtime path: `SandboxAdapter -> MsbserverLifecycle -> microsandbox SDK`.
- Events: `spawn-update` records lifecycle and policy intent.
- Failure rule: if the sandbox cannot start, Vibecrafted fails the run instead
  of falling back to host execution.

The policy parser currently records the full policy and passes CPU/memory to the
sandbox start call. Network, host allowlists, filesystem root mode, tmp mode, and
mounts are policy intent unless the installed microsandbox runtime is separately
verified to enforce them.

## Curated Agent PATH

Agents should be launched with a small, explicit PATH when practical:

```text
~/.vibecrafted/bin
~/.local/bin
~/.cargo/bin
~/tools/scripts
/opt/homebrew/bin
/opt/homebrew/sbin
/usr/local/bin
/usr/bin
/bin
/usr/sbin
/sbin
```

Agent launchers build this PATH as an allowlist. They do not append arbitrary
entries inherited from the parent process, so command checks cannot pass through
stale temp directories, shell plugin caches, or local editor tool paths.

Agents should not assume Bun, LM Studio, Antigravity IDE, or `python@3.13` paths
exist. If a task needs one of those tools, put it in the declared runtime
contract or verify and report the requirement explicitly.

## Loader Rule

The installed helper shim should prefer sources in this order:

1. `VIBECRAFTED_ROOT`, only when explicitly set as a development override.
2. `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/tools/vibecrafted-current`.
3. `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/skills`.

It should not contain a user-machine-specific repository fallback. A local
checkout is a development override, not the canonical installed runtime.
