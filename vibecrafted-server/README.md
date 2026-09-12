# vibecrafted-server

**Agents:** [`AGENTS.md`](AGENTS.md) · [`llms.txt`](llms.txt)

Local control-plane viewer for the Vibecrafted control plane (scaffold editor writes artifacts — local-only). **One core, two frontends.** The Python runtime (`vibecrafted-core/vibecrafted_core/control_plane.py`) is the source of truth that _writes_ `~/.vibecrafted/control_plane/`; this Rust workspace gives a typed, **read-only** view of the same data over HTTP.

| crate          | role                                                                                                                                                                                   |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `control-core` | read-model: `ControlPlane` / `StateView`, the `*.meta.json` + `*.lock` + `marbles/**/state.json` + `lifecycle_runs/**/state.json` merge, events. Never writes control-plane snapshots. |
| `web`          | Leptos 0.8 SSR + axum app. Serves the console shell, the scaffold review surface, and the control-plane read API.                                                                      |

> Installed by `make install-all` as part of the product runtime. `make install-server`
> remains the focused server-only install target.

## Installation

Install as a real binary in `~/.local/bin/vc-server`:

```bash
make install-all
# or, for a focused server-only refresh:
make install-server
```

This builds the release binary, installs the compatibility `vibecrafted-server-web`
copy, and copies public assets/fonts to the runtime home directory.

## Run

Through the first-class command deck:

```bash
vc-server                         # foreground dashboard on 127.0.0.1:3024
vc-server --addr 127.0.0.1:8080   # explicit bind address
vc-server --help                  # CLI contract (must not start a listener)
vc-server --version
vibecrafted server start        # start the daemonized viewer (port 3024)
vibecrafted server status       # check daemon and HTTP health
vibecrafted server open         # open the viewer in your default browser
vibecrafted server doctor       # run diagnostic checks on paths/ports
vibecrafted server stop         # stop the daemonized viewer
```

Or for local development from the repository root:

```bash
make server                      # build (ssr) + run on 127.0.0.1:3024
make server SERVER_ADDR=127.0.0.1:8080   # pick another address
```

Reads against the live `~/.vibecrafted/control_plane/` (or `$VIBECRAFTED_HOME`):

| route                                    | returns                                                                                                                 |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `GET /api/health`                        | constant-time process readiness; does not scan control-plane history                                                    |
| `GET /api/control/state`                 | merged `StateView` — `active_runs`, `recent_runs`, `warnings`, `events`, `generated_at`; includes lifecycle projections |
| `GET /api/control/runs`                  | every `runs/<id>.json` snapshot, newest-first, with `count`                                                             |
| `GET /api/control/runs/{run_id}`         | a single flat run projection, including lifecycle runs, or `404` JSON                                                   |
| `GET /api/control/runs/{run_id}/observe` | versioned one-shot run observation; never creates an await monitor                                                      |
| `GET /api/control/runs/{run_id}/await`   | shared server-owned await subscription with separate idle timeout and hard cap                                          |
| `GET /api/control/lifecycle`             | lifecycle run summaries from `control_plane/lifecycle_runs/`, newest-first, with DoU readiness and controls             |
| `GET /api/control/lifecycle/{run_id}`    | the full nested lifecycle `state.json`, or `404` JSON                                                                   |

Lifecycle summaries surface workflow, status, current stage, baton next
stage/agent, human controls, operator action count, accepted DoU, and
`dou_readiness`. `ZERO DoU` is shown only when `dou_index` resolves to integer
`0` from state, baton/stage data, or the latest worker report fallback; absent
values stay unknown.

Smoke it:

```bash
curl -s http://127.0.0.1:3024/api/control/state | python3 -m json.tool | head
curl -s http://127.0.0.1:3024/api/control/runs  | python3 -c 'import sys,json;print(json.load(sys.stdin)["count"],"runs")'
curl -s http://127.0.0.1:3024/api/control/lifecycle | python3 -c 'import sys,json;print(json.load(sys.stdin)["count"],"lifecycle runs")'
curl -s http://127.0.0.1:3024/api/control/lifecycle/<run_id> | python3 -m json.tool | head
```

## Verify

```bash
make server-build   # cargo build -p vibecrafted-server-web --features ssr
make server-check   # cargo clippy -D warnings on both crates
make server-test    # cargo test -p control-core
make server-smoke   # run the installation and startup smoke tests (runs 3x)
```

## macOS linker note

Leptos macro-expansion produces very long symbol names. Apple's default linker (`ld-prime`, Xcode 15+) asserts on them (`ld: Assertion failed: (name.size() <= maxLength)`). `.cargo/config.toml` pins the host target to the classic linker (`-ld_classic`) so a plain `cargo build` links cleanly; the wasm32 hydrate build is unaffected.
