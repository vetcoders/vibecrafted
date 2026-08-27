# Living Tree commit integrity

## Decision

Vibecrafted uses one layered mechanism, installed from `templates/hooks/`:

1. **Claim fence (M1).** Pre-commit asks the existing repository-mutation
   registry whether any staged path overlaps a live foreign claim. The registry
   under `control_plane/repository_claims/` remains the only authority. A human
   outside a Vibecrafted session may commit unclaimed paths, but cannot absorb a
   live agent's claimed path.
2. **Index projection (M3).** Ruff, Prettier, ESLint, Stylelint, and rustfmt run
   against a temporary tree materialized from the Git index. Only resulting
   blobs are written back to the index. The shared working copy and unstaged
   hunks are not formatted or re-added. Arbitrary `lint-staged` tasks are
   refused because their mutation boundary is unknowable.
3. **Commit-object push gate (M2).** Ruff, Prettier, and Semgrep materialize the
   pushed tip from `git archive` and inspect that projection. Ambient dirty
   files cannot block a clean branch push. Secret scanning still evaluates the
   complete pushed range.

This combines M1+M3+M2. M4 was rejected: a file-level claim cannot prove who
wrote a hunk, and guessed `Authored-By` trailers would violate the charter.
When an integrator knowingly carries another author's work, attribution stays
an explicit, evidence-backed commit decision.

## Degraded mode

If no installed Vibecrafted runtime or claim module can be resolved, the claim
step warns and the Git-only index/object gates continue. A valid registry
conflict never degrades to a warning. No server, MCP endpoint, Slack bridge, or
network connection is required; the adapter calls the local registry module.

## Other repositories

`templates/hooks/install.sh` copies every hook and library into the target
repository's `.husky/` directory for all four supported activators. Therefore a
repo initialized from this template receives the same claim adapter, staged
index formatter, and commit-object push gate without importing Vibecrafted
source or creating a repo-specific state store.

## Incident closure

- Staging a file held by another live session is refused before formatting or
  commit creation.
- A dirty file owned by a concurrent writer is absent from the projected pushed
  commit and cannot poison Ruff/Prettier/Semgrep.
- An unformatted staged Python/JS/Rust file is formatted in the index before
  the commit object is created, while any unstaged version remains untouched.
