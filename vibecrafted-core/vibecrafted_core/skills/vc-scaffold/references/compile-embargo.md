# Compile Embargo — Phase-Aware Recovery Contract

A compile embargo may protect an architectural shaping phase from compiler-driven redesign. It is
not a Git-hook bypass, a push ban, or permission to make the only recovery point local.

## Admission gate

A scaffold may declare compile embargo only when all of the following are explicit:

- the founder/operator decision that authorizes the experiment;
- the phases covered and the exact compile/lint/test gates deferred in each phase;
- the assertions or structural evidence that replace those gates temporarily;
- the attestation that ends the embargo (for example `W2_STRUCTURALLY_CLOSED`), its required author,
  journal location, and commit SHA;
- the repository-owned hook/policy path that understands that marker.

Commit-message, secret, security, ref-safety, and destructive-command checks are never deferred.
`--no-verify` and equivalent bypass flags are forbidden in every phase.

If the repository has no policy-aware hook mechanism capable of deferring only the named gates,
the scaffold must add that mechanism as a separate prerequisite cut or split the work into ordinary
hook-clean commits. A prose claim that hooks "should be quiet" is not implementation and cannot
admit the embargo.

## Recovery channel under embargo

Every coherent phase boundary produces an ordinary, attributed commit through the active hooks.
When the current-turn mandate authorizes remote mutation, publish that commit to the dedicated,
non-trunk recovery ref `embargo/<plan-id>`. The repository's policy-aware pre-push must verify:

1. the destination is exactly the declared embargo ref, never trunk, a release branch, or a tag;
2. the phase marker names the plan ID, phase, deferred gates, attestation state, and exact commit;
3. commit-message, security, secret, identity, and ref-safety checks remain hard;
4. only the explicitly listed compile/lint/test gates are deferred;
5. the remote checkpoint receipt and pushed SHA are written to the mission journal.

This is a recovery ref, not a merge candidate or a second control plane. The plan, tracker, journal,
and the `.dispatch.toml` artifact remain the sources of execution truth. Never infer push authority from the
existence of an embargo: without current-turn authorization, report the checkpoint as local-only
and remote recoverability as blocked. With authorization, a blanket push ban is a reliability
defect.

## Releasing the embargo

The named attestation ends the embargo. Before the next ordinary feature-branch checkpoint:

1. run every deferred gate plus the normal full gate set;
2. record results and the attestation against the exact commit SHA;
3. make the next commit and push through the normal feature-branch policy;
4. retain the embargo ref as recovery evidence until integration policy permits cleanup.

A failed deferred gate reopens the declared recovery path; it never resurrects `--no-verify`.
Merge, tag, release, publication, and stable promotion remain `vc-release` buttons.
