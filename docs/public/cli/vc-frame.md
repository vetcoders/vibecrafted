---
title: "vc-frame: The Terminal Dashboard"
description: "Operator dashboard surfaces: vibecrafted dashboard layouts, session management, gui and tui, and the headless-worker invariant."
section: cli
order: 50
---

# vc-frame: The Terminal Dashboard

vc-frame is the terminal dashboard layer: multiplexed operator layouts, a
sessions rail, and viewer tabs for finished runs. It is an observation
surface, not an execution surface — workers run headless in their own
processes, and vc-frame projects their state. You can close every dashboard
and lose nothing but the view.

## Opening a dashboard

```bash
vibecrafted dashboard              # default dashboard layout
vibecrafted dashboard <layout>     # open/switch to a specific layout
vibecrafted start                  # alias for vibecrafted dashboard
```

Shipped layouts:

| Layout      | Purpose                                               |
| ----------- | ----------------------------------------------------- |
| `dashboard` | Mission-control 2x2 grid (default)                    |
| `operator`  | Operator entry: Launchpad + work shell, sessions rail |
| `marbles`   | Convergence-loop workspace                            |
| `workflow`  | Examine → Research → Implement workspace              |
| `research`  | Research swarm: synthesis pane + agent panes          |

The dashboard is optional and a second-visit surface — the CLI front door is
`vibecrafted help` and `vibecrafted init <agent>`.

## Session management

```bash
vibecrafted dashboard ls               # list active vc-frame sessions
vibecrafted dashboard switch <name>    # switch (inside) or attach (outside)
vibecrafted dashboard attach <name>    # attach from outside vc-frame
vibecrafted dashboard kill <name>      # kill a session
vibecrafted dashboard gc               # preview prune of dead sessions
vibecrafted dashboard gc --apply       # prune dead EXITED sessions
```

Worker tabs are hosted in per-project sessions named `<repo basename> workers`
— never in the operator's own interactive session, which is the bare
`<repo basename>` card. The `VIBECRAFTED_WORKER_SESSION` environment variable
overrides the host session name; nothing else does, so where the dispatch was
fired from never affects where the worker tab lands.

## gui and tui

Two more operator surfaces read the same shared state:

```bash
vibecrafted gui       # local-web launch + observe surface
vibecrafted tui       # Rust operator console over shared state
vibecrafted server status   # local control-plane viewer server
```

`gui` opens the localhost control plane in a browser; `tui` is a terminal
console. Both are readers of control-plane state — the Python runtime remains
the single writer.

## The headless-worker invariant

This is the load-bearing rule of the whole surface:

> Ordinary workers launch headless in their own process session. vc-frame
> observes them; it does not own their processes. Closing a viewer tab, a
> layout, the whole dashboard session, or the terminal itself must not stop
> a headless worker.

Consequences you can rely on:

- Lifecycle runs survive terminal loss. If your SSH connection drops
  mid-`ship`, the run keeps going; re-attach and read
  `vibecrafted ship status`.
- Viewer tabs are projections. A rail full of finished tabs says nothing
  about settlement; an empty rail says nothing about failure.
- A true PTY is reserved for the interactive operator session and for
  explicitly requested terminal-compatibility runs — never the default
  worker path.

Verify a worker's truth through the run record, not the tab:

```bash
vibecrafted status
vibecrafted await <agent> --run-id <id>
vibecrafted settlements summary
```

## Finished-run buckets and `f · x · n`

Finished terminal-compatibility runs may be triaged into viewer bucket
sessions: `Finalized runs` (f), `Failed runs` (x), `Needs attention` (n).
These buckets are transcript/rerun projections. The product `f · x · n`
counters come from the append-only settlement ledger — never from counting
bucket tabs. Closing a bucket changes no settlement fact.

Classification into a bucket is a conjunction of signals (exit code, run
state, report presence and size, transcript size, worker claim) — never exit
code alone. Contradictory signals land in Needs attention.

## When the dashboard disagrees with reality

If viewer rails or counters stay at zero while runs clearly finish, the usual
cause is drift between your source checkout and the installed runtime — the
finish hooks only exist in the installed wire. Check:

```bash
vibecrafted receipt        # source ↔ installed drift labels
vibecrafted doctor         # installation health
```

then reinstall with `vibecrafted update`. See
[Commands](/docs/commands/) for the receipt contract and
[Observe and await](/docs/observe-await/) for run-level truth.
