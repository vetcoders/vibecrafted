# Compile Embargo — Phase-Aware Recovery Contract

A compile embargo protects architectural shaping from compiler- and test-driven redesign.
Under the Founder's authorization, checkpoint commits may use `--no-verify` or an equivalent
selective bypass for deferred Ruff, compile, lint, type-check, and test gates. Keep coherent work
committed; a temporarily failing build is not a reason to leave the only recovery point in a dirty tree.

## Admission gate

A scaffold may declare compile embargo only when all of the following are explicit:

- the Founder decision that authorizes the experiment;
- the phases covered and the exact compile/lint/test gates deferred in each phase;
- the assertions or structural evidence that replace those gates temporarily;
- the attestation that ends the embargo (for example `W2_STRUCTURALLY_CLOSED`), its required author,
  journal location, and commit SHA;
- the checkpoint procedure: which hooks and gates are deferred or bypassed, and the report location
  that records what actually ran and what was skipped.

For a local worker checkpoint under a declared embargo, `--no-verify` is fully authorized. It
bypasses Git's bundled hook entrypoint as a whole; it is not a selective execution mechanism and
does not impose a security-hook prerequisite on the worker. Its authorization is limited to the
declared checkpoint scope, not to a claim that skipped gates passed. Preserve accurate commit
attribution and the authorized ref/operation scope. Record the deferred gates with the commit and
report the gates that actually ran or were skipped.

Use a selective repository-owned, policy-aware hook policy when available. Its absence does not
block a local worker checkpoint or require building a new policy system first: phase authorization
is sufficient for every checkpoint in that phase. Do not weaken product assertions to obtain green.

## Recovery channel under embargo

The embargo has three distinct states:

| State | Owner and allowed action | Evidence and meaning |
| --- | --- | --- |
| Local worker checkpoint | Worker commits in its Fleet Worktree and stops. No push, publication, or remote `embargo/<plan-id>` ref. | Exact SHA, scope, and report of gates run/skipped. The bundled hook entrypoint may be bypassed; this is neither security-clean nor verified delivery. |
| Structural admission under embargo | Designated integrator verifies the exact worker commit and scope, runs Semgrep plus secret/security review, and may integrate the local baton so later worker waves build on coherent architecture. | Structurally admitted only. Compile, lint, type-check, and tests remain deferred until named closure; the integrator never calls a skipped security gate clean. |
| Verified delivery | Designated integrator after named closure. | Full language-appropriate deferred and normal gates pass and are recorded against the exact admitted SHA. |

The plan, tracker, journal, and `.dispatch.toml` artifact remain the sources of execution truth.
Structural admission is a local architectural join, not a delivery claim or a second control plane.

## Releasing the embargo

The named attestation ends the embargo. Before verified delivery:

1. run every deferred gate plus the normal full gate set;
2. record results and the attestation against the exact commit SHA;
3. have the designated integrator record verified delivery against the exact admitted SHA;
4. retain the local checkpoint and structural-admission receipts as recovery evidence until
   integration policy permits cleanup.

### Mixed-repository deferred-gate matrix

Until named closure, structural admission does not authorize compile, lint, type-check, or test
execution merely to make an embargo green. At closure, run the applicable categories for the
admitted scope: Swift — build/type-check and tests; Rust — `cargo check`/Clippy and tests; Python —
lint/type-check and tests; Shell — syntax, formatter/linter, and script tests. The exact commands
belong to the repository's normal gate contract and are recorded with the admitted SHA.

A failed deferred gate triggers repair of the implementation. A renewed structural embargo and
its checkpoint bypass require a recorded phase decision; neither a failing test nor an old bypass
receipt is evidence of verified delivery.
Merge, tag, release, publication, and stable promotion remain `vc-release` buttons.
