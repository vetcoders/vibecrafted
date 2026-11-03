---
name: vc-release
version: 0.3.0
description: >
  Final outward ship skill. Turns "done in the repo" into "safe, visible, deployable,
  discoverable, and launchable in the world." Covers release mechanics, deployment
  topology, reverse-proxy defaults, Semgrep-gated security hygiene, domain and DNS
  wiring, SEO/indexability, verification challenges, onboarding truth, and post-release
  smoke checks. Trigger phrases: "release", "ship to market", "publish",
  "deploy to production", "vc-release", "go live", "launch", "wypuść wersję",
  "deploy", "release prep", "launch path", "launch checklist", "production checklist".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-release` (launcher `release`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                   |
> | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted release <agent>`                                                                                                            |
> | 2. Interactive          | `/vc-release` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                             |

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-release — Ship It Without Lying

> "Done in the repo" is not "done in the world."

## Operator Entry

### Living Tree / Worktree Rule

This workflow runs in the operator's current checkout and current branch. Do not create, switch to, or move execution into a git worktree unless the operator explicitly asks for a worktree in this prompt. Generic words like "isolate", "parallel", or "clean branch" are not enough. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree. Re-read files before editing, adapt to concurrent changes, and report a substrate failure if the current tree is too poisoned to continue safely.

See [Living Tree Rule](../LIVING_TREE_RULE.md).

## Canonical Orientation Gate

Before this workflow performs repo-specific analysis, planning, implementation, review, release, or delegation, it MUST run or consume the `vc-init` procedure for the assigned repo. If fresh `vc-init` evidence is absent, perform the init pass first and treat workflow-specific work as blocked until repo truth exists.

`Loctree:loctree` is the default structural perception skill for that pass. Use Loctree before grep or docs-driven claims to produce or refresh the Code-Derived Application Map: repo-view, focus, slice, impact, find, and follow as relevant. Search for existing symbols and contracts before creating new ones; run impact before delete or major refactor; run slice before editing.

The point is to find the hooks: load-bearing hubs, twins, dead code, drift, runtime entrypoints, and blast-radius traps. If the task is explicitly non-repo or no-code, state the no-repo exception in the report. Otherwise, missing `vc-init`/Loctree evidence is a process failure.

Enter via `vibecrafted start` (or `vc-start`). Then launch through the command deck:

```bash
vibecrafted release codex --prompt 'Prepare v1.2.1 release'
vc-release claude --prompt 'Ship the web surface safely behind Caddy'
vibecrafted release gemini --file /path/to/release-checklist.md
```

Prefer `--file` for an existing plan, `--prompt` for inline intent.

Release is not ceremony. Release is an operational, security, visibility, and adoption contract.

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Pipeline Position

```
scaffold → init → workflow → followup → marbles → dou → decorate → hydrate → [RELEASE]
```

Release runs after `vc-dou` verifies the product surface, `vc-decorate` ensures visual coherence, and `vc-hydrate` packages distribution/SEO/onboarding. Release makes hydrated artifacts real: tags, changelogs, registry/binary publication, deployment topology, proxy/TLS, domain/DNS/verification, indexability, security gates, post-release smoke.

## Public-surface floor

Release owns the ship gate. Run it before tagging and before calling the ship done:

```bash
vc-release --floor
vc-release --floor --require-release
```

`vc-release --floor` fails when a required channel, registry, or in-repo
surface disagrees with the version being cut. A GitHub Release that does not
exist yet is a warning at preflight, because this stage is what creates it:
pushing a tag and stopping is not a finished release. `--require-release`
fails until the Release object for that version exists. `scripts/publish-vibecrafted-release.sh`
runs the preflight and refuses to open a draft when the floor exits `1`.

Do not publish, and do not tell a stranger to install, while `install_cta: no`.
`--acknowledge <surface-id>` records an accepted gap; it is the written risk
acceptance this skill already requires, and it stays in the report.
Contract: [`../vc-dou/references/public-surface-floor.md`](../vc-dou/references/public-surface-floor.md).

## Core Rule

**If the release canon below is not satisfied, release is a no-op.** Do not confuse "I can deploy it" with "it is safe, visible, and ready to meet strangers."

## The Release Canon — six planes

1. **Artifact truth** — versions, tags, changelog, published outputs
2. **Deployment truth** — topology, proxying, healthchecks, restart behavior
3. **Security truth** — Semgrep, exposed surfaces, headers, auth, secret handling
4. **Domain truth** — DNS, default host, TLS, redirects, verification challenges
5. **Visibility truth** — SEO, indexability, social cards, sitemap, robots, public metadata
6. **Onboarding truth** — install path, first run, docs, screenshots, quickstart, buyer path

If any plane is missing, call it out explicitly and block release unless the user knowingly accepts the risk.

## Artifact Canon

**Git/versioning:** tag exact commit (`git tag -a v1.2.3 -m "Release 1.2.3"`), push (`git push origin v1.2.3`), mandatory changelog, published version matches repo/badges/docs/website refs.

**Published outputs:** npm (`npm publish` after version bump), crates.io (`cargo publish`), PyPI (wheel + sdist), GitHub Release (attach exact artifacts with boring descriptive filenames), Docker (tag exact version, optionally `latest` — never ship `latest` alone as identity).

**Artifact naming:** `myapp-v1.2.3-linux-x86_64.tar.gz` (good) vs `release.zip` (bad).

## Deployment Topology

Pick one intentionally:

- **Caddy** — solo/small team, few simple upstreams, automatic HTTPS. Default for MVP web apps, landing+app proxy.
- **Nginx** — already operating it confidently, advanced reverse-proxy needs, many upstreams. For established ops stacks, larger web/API estates.
- **Docker** — reproducibility, heterogeneous environments, portable preview/staging/prod.

**Safe ladder:** simplest real launch → static hosting or Caddy. App + worker + db → Docker + reverse proxy. Mature infra → Nginx or platform standard. Choose the smallest honest stack, not what sounds impressive.

## Deployment Safety Defaults

- Bind app services to `127.0.0.1` by default; document every `0.0.0.0` exception
- Terminate TLS at a deliberate proxy/ingress; prefer reverse proxy over raw port exposure
- Internal Docker network over host-published ports for private services
- Environment injection at runtime, never secrets baked into images
- Require `/health` endpoint, graceful shutdown, non-root containers, `.dockerignore` with no secrets

**Red flags:** admin/debug panel bound publicly; public service on `:3000`/`:5173`/`:8000` without proxy/TLS; `CORS *` on authenticated APIs; stacktraces or framework banners exposed; `.env` or backup files web-accessible.

## Reverse Proxy and Exposure

Minimum reverse-proxy expectations:

- `Host` and forwarding headers preserved intentionally
- websocket upgrade support if the app needs it
- sane timeout and body-size settings
- redirect `www`/apex according to canonical decision
- 80 -> 443 redirect when public HTTPS is intended

Public internet exposure is a decision, not a default.

## Semgrep Release Gate

Semgrep is part of the canon. Not optional. Release report must carry the evidence.

Canonical command: `make semgrep` (wired same as local pre-commit/pre-push hooks: `semgrep scan --config auto --error --quiet --exclude-rule html.security.audit.missing-integrity.missing-integrity .`). Hooks live in `scripts/hooks/`, activated through `make init-hooks`.

Before release: run `make semgrep` against full repo, record findings (rule id, severity, file, line range), classify by **dataflow boundary** (not file location):

- tainted-path / LFI sinks → fix at validated root object
- ReDoS-prone regex → bounded parsing or safe shape
- header/object merge unsafety → explicit allowlist + immutable input boundary
- command/shell construction → parameterized invocation, never string concatenation across an untrusted seam

Block release on any unresolved blocking finding unless user explicitly accepts risk in writing inside the report.

Minimum classes: auth/authz bypasses, insecure secret handling, shell/command injection, SSRF, path traversal/LFI, unsafe file serving, weak input validation on dangerous sinks, insecure deserialization/eval-like, ReDoS regexes, unsafe header/object merge, framework debug/dev endpoints left enabled.

If Semgrep unavailable, say so explicitly, run `uvx semgrep` (documented fallback), and record in the report that the gate was not satisfied. Silence is not acceptable.

## Release Report Contract

Every release report must include these mandatory sections and link back to
`references/release-report-template.md`:

- **Security gate** — `make semgrep` evidence, findings, and unresolved risk.
- **Exposed surface inventory** — public routes, services, ports, domains, and admin/debug surfaces.
- **Deployment mode decision** — selected topology, proxy/TLS posture, and rollback path.
- **Post-release install smoke** — cold-path install/run verification from the published artifact.

## Domain, DNS, Verification

If the product has any public surface, verify: domain registered and intended, DNS to correct target, canonical host (
`www` vs apex), redirects match canonical, TLS resolves cleanly, staging vs prod domains not confused. Also: no stale
preview domains advertised as primary, no mismatched favicon/title/og:image leaking old identity, no broken
`/.well-known/*` paths.

Ownership proofs (when public products need them): Search Console, Bing Webmaster, domain TXT/challenge files, Apple/Google ecosystem `.well-known/` endpoints, any challenge-response proofs required by infra/platforms. If domain ownership proof is required and the challenge path is missing, release is not done.

## SEO and Visibility Canon

Visibility is a hard checklist, not nice-to-have.

- **Page-level**: descriptive `<title>`, meta description, one real `<h1>`, crawlable content in initial HTML or
  truthful fallback, canonical URL, Open Graph + Twitter card tags, correct status code, `noindex` only when
  intentional.
- **Site-level**: `robots.txt`, `sitemap.xml`, canonical host strategy, consistent internal linking, no broken
  docs/marketing links, favicon + social preview assets.
- **Indexability checks**: `curl` page and verify meaningful content without JS; route not blocked by `robots.txt`; meta
  robots not `noindex` unless intentional; canonical points to intended public URL.
- **Domain visibility checks**: docs/landing/CTA all resolve; install instructions point to real public artifacts; social share preview not broken.

If a stranger cannot discover, understand, and try the product quickly, release is incomplete.

## Onboarding Truth

Verify the first-user path: install from published artifacts (not the repo), follow the public quickstart cold, screenshots and demos match reality, app or CLI starts without dev-only assumptions, errors are human-readable.

## Post-Release Smoke Verification

Verify from a **cold path**. The dev machine is not a witness.

Install from the **published artifact** (npm/cargo/PyPI/GitHub Release/Docker registry — never local checkout, never
side-loaded tarball, never dev branch). Then verify: public URL resolves, TLS valid + matches canonical host, health
endpoint passes, core action works end to end, docs and CTA links resolve, published version matches running version,
onboarding screenshots/demos match cold-installer output.

Report must name exact artifact source (registry URL, tag, digest, download URL). "It worked on my repo" does not satisfy this gate.

Every `vc-release` run must produce a report with actual evidence. Cannot honestly say "done" without the four mandatory sections below. If any is missing, release is **blocked** until filled or the user accepts the gap in writing.

> Canonical template: [`references/release-report-template.md`](references/release-report-template.md).
> Full operator checklist: [`references/release-checklist.md`](references/release-checklist.md).
> Deployment reality deep-dive: [`references/deployment-reality.md`](references/deployment-reality.md).

## Release Report Contract

**Mandatory sections:**

1. **Security gate** — command run (`make semgrep` or equivalent), exit status and finding count, per-finding classification (rule id, severity, file, line range, dataflow boundary), resolution per finding (fixed in commit X / accepted with reason / deferred with tracking issue), explicit statement when gate was not actually satisfied.
2. **Exposed surface inventory** — listening ports and bind addresses (default `127.0.0.1`, document every `0.0.0.0`), reverse proxy in front (Caddy/Nginx/cloud LB/none) and where TLS terminates, authentication boundaries per surface, response headers added/stripped at the edge (HSTS, CSP, frame options, CORS allowlist), secret materialization path.
3. **Deployment mode decision** — chosen topology with justification, why it is the smallest honest fit, rollback story (how to revert without manual heroics).
4. **Post-release install smoke** — artifact source (registry URL, tag, digest, download URL — never `file://` from working tree), command sequence executed from clean environment, first-run output evidence (exit code, version banner, health check), any drift between documented quickstart and observed behavior.

**Sign-off** only when all four sections are populated and each has objective evidence attached. A green Semgrep gate without exposed-surface inventory is not a sign-off. A topology decision without a smoke run is not a sign-off. Truth is cumulative.

## Financial / Legal Reality

Hosting and bandwidth costs understood, registry/CDN limits known, LICENSE correct, SECURITY.md exists, privacy policy/terms exist if user data is involved. Do not market proprietary as open source. Do not collect data without saying so.

## Anti-Patterns

- Publishing without `vc-dou`
- Skipping hydration and assuming users will figure it out
- No Semgrep or equivalent security gate
- Exposing services on `0.0.0.0` without deliberate proxy/TLS design
- Broken canonical domain or redirect logic
- Forgetting verification challenge files / TXT records
- Shipping a JS-only empty shell that crawlers cannot understand
- Tagging without a changelog
- Tagging without a GitHub Release (`vc-release --floor --require-release` stays red)
- Calling the ship done while `vc-release --floor` exits `1`
- Deploying without post-release smoke checks
- Treating release as one-time ceremony instead of repeatable discipline

## Final Principle

Ship only when it is safe enough, visible enough, installable enough, understandable enough, and the deployment story is boring enough to trust. If not, the honest result of `vc-release` is not "done" — it is "blocked, for these exact reasons."

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
