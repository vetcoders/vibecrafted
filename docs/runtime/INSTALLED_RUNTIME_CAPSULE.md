# Installed runtime capsule

The repository is a workshop. The installed generation is the runtime.

`~/.local/bin/vibecrafted` and its `vc-*` aliases enter an **installed runtime
root**, never a repository checkout. Ownership is decided by where the launcher
lands, not by the shape of a directory name:

```text
~/.local/share/vibecrafted/            <- $VIBECRAFTED_RUNTIME_HOME, the boundary
  tools/vibecrafted-current/           <- `make install` channel (capsule)
  releases/<version>/                  <- Vibecrafted.app channel
```

Two publication channels are live and both are first-class installed owners:

- **`make install`** stages `tools/vibecrafted-generation-*` and flips the
  atomic `vibecrafted-current` symlink onto it.
- **`Vibecrafted.app`** publishes `releases/<version>/`, records it in
  `active.json` (`vibecrafted.active-runtime.v1`), and writes the `~/.local/bin`
  launchers as env preambles ending in `exec '<runtime_root>/bin/<tool>' "$@"`.

A launcher qualifies when it resolves inside the runtime home, or when the
executable it `exec`s does — and that target really exists and is executable.
Neither a uv tool shim posing as a deck nor a repository checkout can qualify.

Both channels writing `~/.local/bin` means both can be installed at once. When
they disagree, `vibecrafted doctor` says so: the version it reports is checked
against the `VERSION` of the runtime root the PATH launcher actually enters, and
a mismatch is a `warn`, not a silent `ok` on a generation nothing runs.

## Generation manifest

Every published generation contains `runtime-manifest.json` with schema
`vibecrafted.runtime-generation.v2`. It binds:

- the installed version;
- the canonical command-deck entrypoint;
- a one-way fingerprint of the source root, never the checkout path itself;
- the canonical distribution-tree digest and entry count from the verified v2
  source carrier;
- SHA-256 digests for `VERSION`, the launcher, command deck, generated vc-frame
  configuration, and the complete W0 release verifier closure: verifier engine,
  runner, public schema, release policy, and signing key.

The manifest and active runtime files are created and audited before the
single pointer swap. A failed audit leaves the previous generation live and
rollbackable.

The managed `verify-vibecrafted-walkaround` wrapper validates its own regular,
single-link identity plus the exact closed manifest and all bound file digests
before it executes any generation-owned Python. This keeps corruption
detection outside the code whose integrity is being decided.

Source archives have a separate closed v2 `source-provenance.json` carrier. A
Git checkout may claim its `HEAD` only when every included payload path, type,
mode, byte sequence, and symlink target equals that commit. The carrier records
the canonical distribution-tree SHA-256 and entry count. Bootstrap recomputes
that identity before extraction, after extraction, and after candidate staging,
before archive-owned Python may influence publication. Raw tarballs without v2
and contradictory or mismatched records fail bootstrap.

The carrier is an internal-integrity boundary, not an authenticity proof. W4
must bind the exact archive/carrier identity through the pinned release trust
root and keep bootstrap verification fail closed.

## Checkout-free gate

Publication fails when:

- any installed symlink is broken or resolves outside its generation;
- active config, KDL, helper, or command-deck content references the source
  checkout;
- the runtime manifest cannot be created from its required inputs.

`vibecrafted doctor` repeats the audit against the installed artifact. It also
fails when the public launcher resolves outside
`~/.local/share/vibecrafted` — including through the `exec` target of a
generated wrapper — when the manifest is invalid, or when a manifest-bound file
has drifted. The generation manifest and its digest closure are a property of
the `make install` capsule channel; the app channel is bounded by the app's own
pre-publication executable audit, not by `runtime-manifest.json`.

Generations created before this closed verifier inventory are intentionally
rejected and must be reinstalled. W4 binds this manifest into the signed release
receipt; the adjacent manifest alone is the immutable-generation corruption
boundary, not a substitute for the release signature.

## Host shell boundary

The default installer does not source Vibecrafted helpers into the host shell.
It may add only the guarded `~/.local/bin` path entry after explicit consent.
The full helper profile belongs to the explicit `vc-start` environment.

## Spawned-surface environment

Every surface that spawns a process on the operator's behalf — the launchers
`Vibecrafted.app` writes into `~/.local/bin`, the app's workspace terminal, and
the launchd LaunchAgent for the server supervisor — publishes the same
environment contract. It is stated here once because those three writers used to
disagree.

### PATH composes, it never replaces

`PATH` is set as `<generation>/bin` followed by the PATH of whoever invoked the
surface. The signed generation always wins; the caller's PATH survives behind
it. Only when the caller carried no PATH at all does the minimal system set
`/usr/bin:/bin:/usr/sbin:/sbin` stand in as the fallback.

This is a hard requirement, not a convenience. Agent CLIs are installed by
Homebrew, cargo and npm into `/opt/homebrew/bin`, `~/.cargo/bin` and
`~/.local/bin`, and several of them start with `#!/usr/bin/env node`. A surface
that froze PATH at the system set therefore killed them with
`env: node: No such file or directory` (exit 127) — codex, gh, loct, aicx,
semgrep and claude were all invisible to anything the product launched.

| Surface                         | Where PATH is built                                                                                                                                                         |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `~/.local/bin/<tool>` launchers | `AppDelegate.installCanonicalRuntime` emits `export PATH="<generation>/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"` — double-quoted so the expansion stays live at run time |
| App workspace terminal          | `AppDelegate.launchWorkspaceTerminal` composes the app process's own PATH                                                                                                   |
| launchd service                 | `server_supervisor.render_launch_agent_plist` embeds the PATH of whoever ran `server service install`, prefixed with the generation named by `runtime_home/active.json`     |
| Supervised children             | `server_supervisor._child_environment` keeps the canonical entries first as a floor and appends the inherited PATH                                                          |

launchd is the strict case: a job receives nothing but the PATH written into its
plist, so `server service install` is the moment that value is captured. Moving
the installing shell's PATH after that requires a reinstall of the service.

### vc-frame socket directory

The deck defaults `VC_FRAME_SOCKET_DIR` to `/tmp/vc-frame-<uid>` (mode `0700`)
whenever the operator has not set it. Darwin caps an AF_UNIX `sun_path` near 104
bytes and the per-user `$TMPDIR` macOS hands out under `/var/folders/` already
spends about 81 of them, which left vc-frame with a negative session-name budget
and made it refuse any non-trivial name.

The deck is the single owner of this default — it is the door every vc-frame
surface walks through, so the value is not repeated in the launcher generator or
in dispatch. An explicit `VC_FRAME_SOCKET_DIR` always wins, and a `/tmp` entry
that is a symlink or is owned by another user is refused, in which case vc-frame
keeps its own default rather than accepting a hostile socket home.

Worker host session names are single-token for the same reason
(`{label}-{workspace_short}-w`, see `docs/runtime/WORKSPACE_IDENTITY.md`).

### Install failures are visible

`Vibecrafted.app` no longer fails silently when it cannot publish the canonical
runtime. Each failure is written to the unified log (subsystem
`io.vetcoders.vibecrafted`, category `install`) and raised once per launch as a
modal naming the exact cause — including the full path of any forbidden symlink
found beneath the generation. Read the log with:

```bash
log show --predicate 'subsystem == "io.vetcoders.vibecrafted"' --last 1h
```
