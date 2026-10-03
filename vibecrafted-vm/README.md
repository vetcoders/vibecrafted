# Linux arm64 Runtime Pack carrier

This directory owns the hardened image that will be consumed by the Workshop
`local-vm` backend in H2b3b. H2b3a does **not** enable that selector or create
per-run containers.

The image has one input: the checksum-pinned Runtime Pack produced by the
repository's canonical `package-runtime-pack.sh` contract. It does not build
from sibling repositories, install mutable releases, mount operator state, or
provide a success stub for a missing tool.

## Build the Runtime Pack

From a clean clone at the release commit:

```bash
sha="$(git rev-parse HEAD)"
stage="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-linux-arm64.XXXXXX")"
python3 scripts/distribution_manifest.py archive \
  --source "$PWD" --output "$stage/source.tar.gz" --root-name vibecrafted
tar -xzf "$stage/source.tar.gz" -C "$stage"
docker buildx build --platform linux/arm64 \
  -f "$stage/vibecrafted/vibecrafted-vm/RuntimePack.Containerfile" \
  --build-arg VIBECRAFTED_SOURCE_REVISION="$sha" \
  --output type=local,dest=build/linux-arm64-runtime-pack \
  "$stage/vibecrafted"
```

The manifest-owned public distribution stage supplies the closed
`source-provenance.json` carrier and excludes development/secret surfaces. The
builder then downloads only public immutable source archives, verifies their
SHA-256 digests, builds the native binaries, records executable versions and
provenance in `runtime-inventory.json`, and invokes the canonical Runtime Pack
packager.

## Build the exact image

```bash
pack=build/linux-arm64-runtime-pack/Vibecrafted_RuntimePack_linux-arm64.tar.gz
pack_sha="$(sha256sum "$pack" | awk '{print $1}')"
manifest_sha="$(tar -xOzf "$pack" VibecraftedRuntime/runtime-pack-provenance.json | sha256sum | awk '{print $1}')"
sha="$(git rev-parse HEAD)"
docker build --platform linux/arm64 -f vibecrafted-vm/Containerfile \
  --build-arg RUNTIME_PACK_ARCHIVE="$pack" \
  --build-arg RUNTIME_PACK_CARRIER_BASENAME="$(basename "$pack")" \
  --build-arg RUNTIME_PACK_SHA256="$pack_sha" \
  --build-arg RUNTIME_PACK_MANIFEST_SHA256="$manifest_sha" \
  --build-arg VIBECRAFTED_SOURCE_REVISION="$sha" \
  -t vibecrafted-local-vm:"${sha:0:12}" .
```

The default process is UID/GID 10001. No `VOLUME` is declared and no provider
credential, session, home, key, XDG, or repository material is baked in.
Provider CLIs are installed at exact versions recorded in
`runtime-provider-lock.json`; no paid provider call is part of the carrier
proof.

## Personal-dev compose and wizard

`compose.yaml` and `wizard/` are retained only as an operator-controlled
personal development convenience. They require an explicitly supplied
`VC_PERSONAL_DEV_IMAGE` and may mount broad host state. They are **not** a
security boundary, do not build this carrier, and are not the Workshop
selector backend.

## Persistent, per-repo dev container

`Dockerfile.dev` + `compose.dev.yaml` + `dev-up.sh` are a batteries-included
personal workstation: a **non-ephemeral, per-repo** container that preserves
**session-history continuity across agents**. It bakes the toolchain
(Debian 13 + uv + Node + Rust) and the agent CLIs — **claude, codex, gemini,
and kimi (pilot)** — plus the AICX/Loctree memory foundations. The repo is
mounted live at `/workspace`; every agent's session state (`~/.claude`, `~/.codex`,
`~/.gemini`, `~/.kimi`) and the AICX corpus (`~/.aicx`) live in per-project
named volumes, so rebuilding the container never loses history.

```bash
# from anywhere: stand up a container for the current repo
vibecrafted-vm/dev-up.sh                 # or: dev-up.sh /path/to/repo

# open a shell (project = vc-<repo-slug>)
docker compose -p vc-<repo-slug> -f vibecrafted-vm/compose.dev.yaml exec dev zsh

# stop but KEEP history
docker compose -p vc-<repo-slug> -f vibecrafted-vm/compose.dev.yaml down
# wipe history for this repo
docker compose -p vc-<repo-slug> -f vibecrafted-vm/compose.dev.yaml down -v
```

Each repo gets its own isolated container and volumes via the Compose project
name (`-p vc-<slug>`, set automatically by `dev-up.sh`). Tailnet is optional —
supply `TAILSCALE_AUTHKEY` to join the mesh (userspace networking, no `NET_ADMIN`).
Like `compose.yaml`, this is an operator convenience, not a security boundary,
and is distinct from the hardened Runtime Pack carrier above.

This is **not** a parallel toolchain: it shares the Debian 13 baseline with
`.cursor/Dockerfile` (the Cloud Agent env) and reuses this directory's
`entry.sh` (readiness probe + optional tailnet).
