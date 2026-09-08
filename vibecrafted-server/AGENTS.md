# AGENTS.md — vibecrafted-server

**Agent-first runbook** for the local control-plane viewer (`vc-server`).

Also: [`README.md`](README.md) · [`llms.txt`](llms.txt) · monorepo `docs/runtime/CONTROL_STATUS_THREADS.md`.

---

## 0) Identity

| Field        | Value                                                                 |
| ------------ | --------------------------------------------------------------------- |
| Path         | `vibecrafted-server/` (Rust workspace)                                |
| Binary       | `vc-server` → `~/.local/bin` via `make install-server`                |
| Role         | **Read-only** HTTP + SSR console over `~/.vibecrafted/control_plane/` |
| Writer truth | Python `vibecrafted_core.control_plane` (not this crate)              |
| Crates       | `control-core` (model/merge) · `web` (Leptos SSR + axum API)          |

---

## 1) Status threads (do not collapse)

| Thread                     | Owner                         | Ends when                                        |
| -------------------------- | ----------------------------- | ------------------------------------------------ |
| Process `state` / `health` | merge snapshots+meta+events   | final / terminal liveness / exit                 |
| Settlement **f / x / n**   | Python snapshot only          | finalized / failed / needs_attention             |
| Delivery axes              | kernel receipt                | never from bare `completed`                      |
| Lifecycle container        | `lifecycle_runs/*/state.json` | **workflow status** final — not stage exit alone |

UI: Active / **Stalled** / Recent = live merge. All Runs = snapshots only. Board = settlement.

---

## 2) Mission

1. Never write control-plane snapshots from Rust (scaffold editor is the exception for plan artifacts).
2. Keep predicates honest: lifecycle mid-flight must not look terminal.
3. Export `stalled_runs` on `/api/control/state` and dashboard.
4. Settlement board remains Python-owned; do not invent f/x/n from exit codes.

---

## 3) Dev / verify

```bash
# from monorepo root (preferred) or this directory
make server-check    # clippy -D warnings
make server-test     # control-core + web tests
make server          # run 127.0.0.1:3024

cd vibecrafted-server
cargo test -p control-core
cargo check -p vibecrafted-server-web --features ssr
```

Smoke:

```bash
curl -s http://127.0.0.1:3024/api/health
curl -s http://127.0.0.1:3024/api/control/state | jq '{active:(.active_runs|length),stalled:(.stalled_runs|length),f:.settlement_counts.f,x:.settlement_counts.x,n:.settlement_counts.n}'
```

---

## 4) Style (console)

Dark editorial · zinc · settlement cells f/x/n · mono badges.  
Align with vc-slack console tokens (`#0a0a0b` bg, muted zinc, green live) and vc-drive (`#8ab4f8` accent when linking cross-product).

Footer: `Vibecrafted. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI`

---

## 5) Security / cloud notes

- Local default bind `127.0.0.1` — public cloud needs Tailscale + GitHub OAuth (operator-owned).
- `web/src/tools.rs` owns two boundaries: `/structure/report{,/{asset}}` serves the
  canonical Loctree report under a CSP `sandbox` (opaque origin, explicit sibling
  assets, no `fetch`/forms); `/api/aicx/{search,reference}` serve the private AICX
  corpus to a verified local peer only (loopback, or the bound interface itself),
  fail closed without `ConnectInfo`, run `aicx` with a bounded argv and a timeout,
  and never emit raw paths — hits carry a server-owned reference route.
- No Slack tokens here — use `vc-slack` for agency into Slack.
- Future: `VC_SERVER_TOKEN` for fleet observer (see vc-slack `docs/STACK.md`) — not wired yet.

---

## 6) Bootstrap prompt

```text
Zadanie: utrzymaj vibecrafted-server control plane.
Read-only nad control_plane. Nie zlewaj settlement z process health.
Lifecycle terminal tylko od statusu workflowu. stalled_runs w API.
Testy: cargo test -p control-core. Styl: dark editorial, f/x/n board.
```

Vibecrafted. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
