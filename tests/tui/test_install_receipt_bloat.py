"""The install receipt must not embed the backup ledger into recovery snapshots.

Fail-ledger 2026-10-04/05: one 75k-entry ``drift_backup_history`` map was
embedded four times over (live layer, ``retirement_rollback.receipt``, and
both again inside ``preparing_previous_receipt``), so the checkpoint write
breached the 128 MiB receipt limit and refused the install two days in a row.
``_restore_runtime_publication_receipt`` provably discards the nested copies
(it overwrites ``backups``/``drift_backups``/``drift_backup_history`` from the
live layer), and a rollback receipt is only ever read for light attribution
keys — so the snapshots carry identity and ownership, never the ledger.
"""

from scripts import vetcoders_install as installer


def _previous_receipt() -> dict:
    ledger = {f"/skills/file-{i}.md": [f"/backups/{i}"] for i in range(5000)}
    return {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "version": "4.3.3+gaaaaaaaa",
        "roots": {"runtime_home": "/tmp/rt"},
        "owned_symlinks": {
            "/tmp/rt/tools/vibecrafted-current": "/tmp/rt/releases/4.3.3+gaaaaaaaa"
        },
        "owned_dirs": ["/tmp/rt/releases/4.3.3+gaaaaaaaa"],
        "drift_backup_history": dict(ledger),
        "retirement_rollback": {
            "generation": "/tmp/rt/releases/old",
            "publication": "/tmp/rt/.installer-backups/pub",
            "receipt": {
                "owned_dirs": ["/tmp/rt/releases/old"],
                "drift_backup_history": dict(ledger),
            },
            "verified_before_publication": True,
        },
    }


def test_recovery_snapshot_never_carries_the_backup_ledger():
    snapshot = installer._recovery_receipt_snapshot(_previous_receipt())
    assert snapshot["drift_backup_history"] == {}
    assert "drift_backup_history" not in snapshot["retirement_rollback"]["receipt"]
    # Restore/validation identity survives: roots gate the resume refusal,
    # version + owned_symlinks feed _previous_runtime_is_available.
    assert snapshot["schema"] == installer.RUNTIME_INSTALL_SCHEMA
    assert snapshot["roots"] == {"runtime_home": "/tmp/rt"}
    assert snapshot["version"] == "4.3.3+gaaaaaaaa"
    assert snapshot["owned_symlinks"]
    assert snapshot["retirement_rollback"]["receipt"]["owned_dirs"] == [
        "/tmp/rt/releases/old"
    ]


def test_recovery_snapshot_does_not_mutate_the_live_previous():
    previous = _previous_receipt()
    installer._recovery_receipt_snapshot(previous)
    assert len(previous["drift_backup_history"]) == 5000
    assert (
        len(previous["retirement_rollback"]["receipt"]["drift_backup_history"]) == 5000
    )


def test_recovery_snapshot_drops_a_stale_nested_snapshot_instead_of_matryoshka():
    previous = _previous_receipt()
    previous["preparing_previous_receipt"] = {"version": "4.3.2+gstale"}
    snapshot = installer._recovery_receipt_snapshot(previous)
    assert "preparing_previous_receipt" not in snapshot


def test_rollback_receipt_keeps_attribution_and_drops_the_ledger():
    rollback = installer._rollback_receipt_snapshot(_previous_receipt())
    assert "drift_backup_history" not in rollback
    assert "preparing_previous_receipt" not in rollback
    # No retirement_* recursion either — same contract as the inline
    # comprehension this helper replaced.
    assert not any(key.startswith("retirement_") for key in rollback)
    assert rollback["owned_dirs"] == ["/tmp/rt/releases/4.3.3+gaaaaaaaa"]
    assert rollback["version"] == "4.3.3+gaaaaaaaa"
