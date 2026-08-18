# Vibecrafted 4.2.0 roadmap — measured truths, finished seams

Status: planned (scaffolded 2026-08-18). Not part of the 4.1.0 release contract.

Plan package (atlas · falsification · tracker · DRIVER · 9 briefs · manifest):
`/Users/polyversai/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0818/plans/roadmap-4.2.0/`
Drive it from `DRIVER.md` there; this file is the repo-facing summary.

## Thesis

4.1.0 shipped two channels (DMG + portable) and a durable Workspace identity, but
several truths are still asserted rather than measured, and three product seams
are visibly unfinished. 4.2.0 turns each into a verifier-earned `[x]` or an honest
`[?]`. Only a delivery-verifier flips `[~]→[x]`.

## Waves

| Wave | Cut  | Title                                           | Vector    | Repo                   |
| ---- | ---- | ----------------------------------------------- | --------- | ---------------------- |
| W0   | W0-a | Verify 4.1.0 payloads symlink/.env/HOME-free    | recon     | vibecrafted            |
| W0   | W0-b | `resume --run-id` e2e on the installed build    | e2e       | vibecrafted            |
| W0   | W0-c | LIVE RUNS dashboard runtime acceptance          | e2e       | vibecrafted            |
| W1   | W1-a | Symlink-free tree: guard + Windows-clone smoke  | stabilize | vibecrafted            |
| W1   | W1-b | Donor snapshots as a release feature            | implement | vibecrafted            |
| W1   | W1-c | Serve `install.ps1`                             | implement | vibecrafted-io         |
| W2   | W2-a | Workspaces surface in the vc-frame session rail | implement | vc-frame               |
| W2   | W2-b | Vibecrafted.app boundary + chrome polish        | implement | vibecrafted + vc-frame |
| W3   | W3-a | Core `__init__` import direction                | stabilize | vibecrafted            |

Order: W0 (parallel, read-only) → W1 (parallel, disjoint files) → W2 (parallel) →
W3 (after W1-a). Every wave ends at an operator button (merge / deploy / install).

## Decisions

1. Repo tree is symlink-free (landed in #47); a contract test + Windows-clone smoke guard it; projections are produced by installer/packers, never linked.
2. Dirty donors are a release feature (`--snapshot-donors`), not an operator ritual.
3. Windows gets a served entry point (`/install.ps1`, WSL2 hand-off), not a native install.
4. vc-frame shows Workspaces (catalog, `workspace_id`), not physical session names.
5. Runtime acceptance on the installed build is a cut (W0), not a footnote.

## Explicit non-goals

Native Windows runtime · a second control plane · new vc-frame features beyond the
rail and the 2026-08-16 chrome asks · rewriting the release scripts · merges into trunk,
deploys, or host installs performed by an agent (branch pushes and PR creation are the supervisor's; canonical list: vc-operator/AUTONOMY.md).
