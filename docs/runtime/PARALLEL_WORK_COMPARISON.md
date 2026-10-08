# Compare parallel work before deploy

Let independent workers finish their cuts, then review their delivered work
before deciding what to integrate and deploy:

```sh
vc-git /path/to/repository --compare DISPATCH_RUN_ID --cuts LEFT_CUT RIGHT_CUT
vc-git /path/to/repository --compare DISPATCH_RUN_ID --cuts LEFT_CUT RIGHT_CUT --json > comparison.json
```

Use the dispatch run ID and cut IDs from your dispatch plan and receipts. The
command reads the existing control-plane dispatch ledger under
`$VIBECRAFTED_HOME/control_plane/dispatches/DISPATCH_RUN_ID/receipts.json`.
Both selected cuts must be `settled` with `acceptance: verified`. Active,
queued, reported, failed, and stopped cuts are refused with exit code 2 before
either worker's changes are compared. A canonical provider runtime record, when
present, must also say `completed` with exit code 0.

The comparison uses each receipt's full baseline and delivered commit SHA. It
shows each worker's patch, shared files, report paths, and the difference between
the delivered trees. Later branch movement or uncommitted edits do not enter the
comparison. A changed receipt during comparison causes a refusal and retry.
The invoking repository must share the dispatch repository's Git storage.

Review both reports and patches, resolve disagreements, and choose the work to
integrate. Shared files are a review signal; they do not prove merge conflicts.
Different baselines remain visible, so a difference between tips may also contain
baseline differences. Retain the JSON output if the decision needs a durable
receipt. Repeat for additional pairs when a wave has more than two deliveries.

This command performs a review step. It does not merge, deploy, or grant release
approval, and does not intercept unrelated deployment tools. Integration and
deployment remain Founder decisions. The dispatch ledger attests worker delivery;
the comparison does not certify installed runtime or product acceptance.
