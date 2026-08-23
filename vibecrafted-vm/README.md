# vc-workspace

SoTA dev container for the **Vetcoders / vibecrafted / loctree / aicx** stack.
Debian 13 trixie base, multi-arch (linux/amd64 + linux/arm64), full framework

- 11 foundations + 21 vc-\* skills + 3 agent CLIs + tailnet integration.

Single image, mesh-wide consistency. Works on host-a (macOS arm64), host-b
(macOS), host-d (macOS), host-e (Linux), windows (WSL2) — same surface
everywhere.

> **Naming, once:** `vc-workspace` is **this container** (image + tailnet node +
> build folder). `vc-runtime` is the **multiroot repo tree** it mounts at
> `/workspace`. The container is named after the workspace it serves; the code
> it serves keeps its own name. One name → one thing.
>
> This is one of **three** container paths in the tree — see
> [`../WORKSPACE.md`](../WORKSPACE.md) § "Trzy ścieżki kontenera" for when to
> use this vs `.devcontainer/` (VS Code) vs `vibecrafted/docker/` (CI/minimal).

## What's inside

| Layer                     | Components                                                                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Base**                  | `debian:trixie-slim` (multi-arch)                                                                                                                                               |
| **Toolchains**            | Rust (rustup stable) · Zig 0.13.0 · Node 22 LTS · Python 3 (uv)                                                                                                                 |
| **Vetcoders foundations** | `loct` · `loctree` · `loctree-mcp` · `loctree-lsp` · `aicx` · `aicx-mcp` (all via loct.io) · `screenscribe` · `semgrep` · `mise` · `starship` · `atuin` · `zoxide` · `vc_frame` |
| **Agent CLIs**            | `claude` (`@anthropic-ai/claude-code`) · `codex` (`@openai/codex`) · `gemini` (`@google/gemini-cli`)                                                                            |
| **Framework**             | vibecrafted 21 vc-\* skills + agent symlinks + frontier config                                                                                                                  |
| **CLI niceties**          | `eza` · `bat` · `fd` · `rg` · `just` · `tokei`                                                                                                                                  |
| **Network**               | tailscale (userspace mode — no `/dev/net/tun` kernel module needed)                                                                                                             |
| **Shell**                 | zsh + starship + atuin + zoxide                                                                                                                                                 |

## Quick start

### 1. Setup non-secret environment

```bash
cd vc-runtime/vc-workspace
cp .env.example .env
# Edit .env — set TAILSCALE_HOSTNAME + VC_RUNTIME_DIR
# Never put TAILSCALE_AUTHKEY in this file.
```

### 2. Build (multi-arch via buildx)

```bash
docker buildx create --use --name vc-workspace-builder
docker buildx build --platform linux/amd64,linux/arm64 \
    -t vetcoders/vc-workspace:trixie \
    --push .   # or --load for single-arch local
```

### 3. Run (local dev with mounts)

```bash
# Optional Tailscale: inject an ephemeral key from your secret manager into
# this process only. Without it, the container starts without joining tailnet.
TAILSCALE_AUTHKEY="$(your-secret-manager read tailscale-ephemeral-key)" \
  docker compose up -d
docker compose exec dev zsh
```

Or single-shot:

```bash
docker compose run --rm dev
```

### 4. Verify tailnet access

Inside container:

```bash
tailscale status
# expect: vc-workspace-<hostname>  100.x.y.z  ...
```

From any other mesh node (host-a/host-b/host-d/ops) — works via **Tailscale SSH**
(no sshd in the image; `entry.sh` runs `tailscale up --ssh`, gated by ACLs):

```bash
ssh root@vc-workspace-<hostname>
# or via tailnet IP
```

## Mount strategy

The container expects these host paths (mounted automatically by
`compose.yaml`):

| Host path                    | Container path             | Purpose                                     |
| ---------------------------- | -------------------------- | ------------------------------------------- |
| `~/.vibecrafted/vc-runtime/` | `/workspace/`              | Operator repos — the multiroot (read-write) |
| `~/.aicx/`                   | `/root/.aicx/`             | Canonical corpus (persistent)               |
| `~/.keys/`                   | `/root/.keys/` (ro)        | GPG passphrase, notary creds — read-only    |
| `~/.claude/`                 | `/root/.claude/`           | Claude sessions (persistent)                |
| `~/.codex/`                  | `/root/.codex/`            | Codex sessions (persistent)                 |
| `~/.gemini/`                 | `/root/.gemini/`           | Gemini sessions (persistent)                |
| `~/.vibecrafted/`            | `/root/.vibecrafted/`      | vibecrafted artifacts (plans, reports)      |
| `~/.config/vetcoders/`       | `/root/.config/vetcoders/` | Frontier config (starship, atuin, vc_frame) |
| `~/.gnupg/`                  | `/root/.gnupg/` (ro)       | GPG keyring for release-tag signing         |

## Tailnet integration

Tailscale runs in **userspace mode** (`tailscaled --tun=userspace-networking`),
no host kernel module needed. Container joins tailnet as a regular node:

- Get an ephemeral auth key from https://login.tailscale.com/admin/settings/keys
- Inject `TAILSCALE_AUTHKEY` only into the `docker compose up` process; never
  persist it in `.env`, generated config or shell history
- Optionally set `TAILSCALE_TAGS=tag:devbox` for ACL routing
- Container appears in tailnet as `${TAILSCALE_HOSTNAME}` on first boot

Outbound: container reaches tailnet peers (aicx-mcp endpoints, ssh, etc.) via
tailnet IPs (100.x.y.z range).

Inbound: tailnet peers ssh into container via hostname or tailnet IP.

## Why not native macOS containers?

Apple's `container` CLI (WWDC 2024) runs native macOS Mach-O binaries — would
require separate `container` build per Mac host + split surface from
Linux mesh nodes (host-e, etc.). For Vetcoders Rust cross-platform
framework, single Linux image (this) preserves mesh-wide consistency.

If/when Metal-accelerated MLX embeddings become hot-path (e.g.
`aicx-embeddings/metal` feature), add a parallel `apple/container` track
specifically for M-series Mac dev. Until then, cloud embedder
(`qwen3-embedding:8b @ host-d`) handles embedding side via tailnet.

## Authority

- Built atop the host-side `bootstrap-modal.sh` install pattern (Modal /
  Codespaces / bare-metal) — same 9-stage layout, here containerized +
  framework-aware.
- Vetcoders foundations (`loct`, `loctree`, `loctree-mcp`, `loctree-lsp`,
  `aicx`, `aicx-mcp`) installed **prebuilt** via the official loct.io installer
  — GPG-verified, signed bundles per target triple (arm64 + x86_64 linux):
  `curl -fsSL https://loct.io/install.sh | sh` (in the image: `INSTALL_DIR=/usr/local/bin`,
  pin with `LOCTREE_VERSION`). No source compile → fast, reproducible builds.
- Vibecrafted framework installed via the **official installer**, newest stable.
  Piping to bash is already non-interactive (no-TTY → compact path); `--yes`
  skips the consent prompt; `VIBECRAFTED_HOME` sets the location:
  `curl -fsSL https://vibecrafted.io/install.sh | bash -s -- --yes`
  (in the image: `VIBECRAFTED_HOME=/opt/vibecrafted bash install.sh --yes`).

## Caveats

- **Tailscale auth keys are sensitive.** Use ephemeral keys
  (`tskey-auth-..._ephemeral`) for short-lived dev containers. Persistent
  keys = ssh in indefinitely.
- **Mounted `~/.keys/` is read-only** by design — container should never
  modify host GPG keyring or notary creds.
- **Container is NOT a security boundary** — operator's mounted repos are
  read-write, agent CLIs have full shell access. Treat as extension of host
  workspace, not isolated sandbox.
- **First build is slow** (~10-20 min depending on host) — Rust workspace
  compile of aicx + loctree-suite. Subsequent builds cache layer-by-layer.

## Sister paths (same tree)

- [`../.devcontainer/`](../.devcontainer/) — VS Code "Reopen in Container"
  (Docker + microsandbox hybrid), for local IDE work
- [`../vibecrafted/docker/`](../vibecrafted/docker/) — minimal entrypoint that
  seeds skills from `/opt/vibecrafted`, for CI / headless
- [`vibecrafted`](https://vibecrafted.io) — release engine for AI-built software
- [`loctree`](https://loctree.dev) — semantic-AST structural map

## License

MIT — see [LICENSE](./LICENSE).

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
