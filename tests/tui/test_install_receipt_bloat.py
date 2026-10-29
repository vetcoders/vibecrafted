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

import copy
import json
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer

_RETIREMENT_LEDGERS = (
    "retirement_generations",
    "retirement_copies",
    "retirement_pending",
)


def _retirement_ledgers() -> dict:
    inventory = {
        f"file-{number}": {"sha256": "a" * 64, "size": 123, "mode": 420}
        for number in range(500)
    }
    return {
        key: {
            f"/retired/{key}/{number}": {"files": copy.deepcopy(inventory)}
            for number in range(4)
        }
        for key in _RETIREMENT_LEDGERS
    }


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
        **_retirement_ledgers(),
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
    assert all(previous[key] for key in _RETIREMENT_LEDGERS)


def test_recovery_snapshot_keeps_retirement_ledgers_only_in_the_live_layer():
    previous = _previous_receipt()
    original = copy.deepcopy(previous)
    snapshot = installer._recovery_receipt_snapshot(previous)
    assert not any(key in snapshot for key in _RETIREMENT_LEDGERS)
    assert snapshot["retirement_rollback"] == {
        key: value
        for key, value in previous["retirement_rollback"].items()
        if key != "receipt"
    } | {"receipt": {"owned_dirs": ["/tmp/rt/releases/old"]}}
    assert previous == original


def test_recovery_snapshot_drops_a_stale_nested_snapshot_instead_of_matryoshka():
    previous = _previous_receipt()
    previous["preparing_previous_receipt"] = {"version": "4.3.2+gstale"}
    snapshot = installer._recovery_receipt_snapshot(previous)
    assert "preparing_previous_receipt" not in snapshot


def test_loader_compacts_legacy_embedded_ledgers(tmp_path: Path) -> None:
    """A pre-fix on-disk document is healed on load: the dead nested ledger
    copies go, the live-layer ledger (ownership truth) stays byte-for-byte."""
    previous = _previous_receipt()
    previous["preparing_previous_receipt"] = {
        "version": "4.3.2+gstale",
        "drift_backup_history": {"/skills/x.md": ["/backups/x"]},
        "preparing_previous_receipt": {"version": "4.3.1+gdeeper"},
        "retirement_rollback": {
            "receipt": {"drift_backup_history": {"/skills/y.md": ["/backups/y"]}}
        },
    }
    path = tmp_path / "install-receipt.json"
    path.write_text(json.dumps(previous), encoding="utf-8")

    loaded = installer._load_runtime_install_receipt(path)

    assert len(loaded["drift_backup_history"]) == 5000
    assert "drift_backup_history" not in loaded["retirement_rollback"]["receipt"]
    saved = loaded["preparing_previous_receipt"]
    assert saved["drift_backup_history"] == {}
    assert "preparing_previous_receipt" not in saved
    assert "drift_backup_history" not in saved["retirement_rollback"]["receipt"]


def test_rollback_receipt_keeps_attribution_and_drops_the_ledger():
    rollback = installer._rollback_receipt_snapshot(_previous_receipt())
    assert "drift_backup_history" not in rollback
    assert "preparing_previous_receipt" not in rollback
    # No retirement_* recursion either — same contract as the inline
    # comprehension this helper replaced.
    assert not any(key.startswith("retirement_") for key in rollback)
    assert rollback["owned_dirs"] == ["/tmp/rt/releases/4.3.3+gaaaaaaaa"]
    assert rollback["version"] == "4.3.3+gaaaaaaaa"


def _receipt_bytes(receipt: dict) -> bytes:
    return (
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode()


def _receipt_publication_scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME", str(home / ".local/share/vibecrafted")
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local/bin"))
    paths = installer._runtime_install_paths()
    destination = paths["product_config"]
    destination.mkdir(parents=True)
    (destination / "fixture.txt").write_text("old\n")
    selector = paths["runtime_home"] / "active.json"
    selector.parent.mkdir(parents=True)
    selector.write_text("old\n")
    previous = installer._compact_legacy_receipt_layers(_previous_receipt())
    previous["roots"] = {key: str(value) for key, value in paths.items()}
    previous["owned_dirs"] = [str(paths["runtime_home"] / "releases/old")]
    return paths, destination, selector, previous


def test_config_transaction_checkpoint_peak_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated upgrades checkpoint one live ledger, with exact rollback lineage."""
    paths, destination, selector, previous = _receipt_publication_scene(
        tmp_path, monkeypatch
    )
    original_history = copy.deepcopy(previous["drift_backup_history"])
    original_retirement = {
        key: copy.deepcopy(previous[key]) for key in _RETIREMENT_LEDGERS
    }
    peaks = []
    checkpoint = installer._checkpoint_runtime_install_receipt

    def observe(runtime_home, receipt):
        peaks.append(len(_receipt_bytes(receipt)))
        transaction = receipt.get("config_transaction")
        if transaction:
            saved = transaction["previous_receipt"]
            assert saved["drift_backup_history"] == {}
            assert saved["schema"] == previous["schema"]
            assert saved["version"] == previous["version"]
            assert saved["roots"] == previous["roots"]
            assert not any(key in saved for key in _RETIREMENT_LEDGERS)
        checkpoint(runtime_home, receipt)

    monkeypatch.setattr(installer, "_checkpoint_runtime_install_receipt", observe)
    limit = len(_receipt_bytes(previous)) * 3 // 2
    monkeypatch.setattr(installer, "_RUNTIME_LEGACY_DOCUMENT_MAX_BYTES", limit)
    for number in range(8):
        previous_bytes = _receipt_bytes(previous)
        receipt = copy.deepcopy(previous)
        receipt.update(
            version=f"4.4.0+g{number:08x}",
            install_pending=True,
            install_phase="preparing",
            preparing_previous_receipt=installer._recovery_receipt_snapshot(previous),
        )
        staging = paths["runtime_home"] / f".installer-backups/publication-{number}"
        after = staging / "after-config"
        after.mkdir(parents=True)
        (after / "fixture.txt").write_text(f"new-{number}\n")
        next_selector = staging / "after-active"
        next_selector.write_text(f"new-{number}\n")
        installer._publish_runtime_config_transaction(
            paths,
            receipt,
            previous,
            [(destination, after), (selector, next_selector)],
            staging,
            installer._runtime_config_digest(destination),
        )
        assert _receipt_bytes(previous) == previous_bytes
        assert (destination / "fixture.txt").read_text() == f"new-{number}\n"
        assert selector.read_text() == f"new-{number}\n"
        assert "config_transaction" not in receipt
        assert "preparing_previous_receipt" not in receipt
        assert all(
            receipt["drift_backup_history"][key] == value
            for key, value in original_history.items()
        )
        assert all(receipt[key] == value for key, value in original_retirement.items())
        receipt.pop("install_pending")
        receipt.pop("install_phase")
        installer._checkpoint_runtime_install_receipt(paths["runtime_home"], receipt)
        previous = receipt
    assert max(peaks) < limit


@pytest.mark.parametrize("failure", ["before-publication", "after-replacement"])
def test_config_transaction_failure_preserves_live_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    paths, destination, selector, previous = _receipt_publication_scene(
        tmp_path, monkeypatch
    )
    previous_bytes = _receipt_bytes(previous)
    receipt = copy.deepcopy(previous)
    staged_generation = str(paths["runtime_home"] / "releases/new")
    receipt["owned_dirs"].append(staged_generation)
    for key in _RETIREMENT_LEDGERS:
        receipt[key][staged_generation] = {"captured_after_previous": key}
    receipt.update(
        version="4.4.0+gnew",
        install_pending=True,
        install_phase="preparing",
        preparing_previous_receipt=installer._recovery_receipt_snapshot(previous),
    )
    staging = paths["runtime_home"] / ".installer-backups/publication-failure"
    after = staging / "after-config"
    after.mkdir(parents=True)
    (after / "fixture.txt").write_text("new\n")
    next_selector = staging / "after-active"
    next_selector.write_text("new\n")
    checkpoint = installer._checkpoint_runtime_install_receipt
    replace_entry = installer._replace_runtime_transaction_entry
    failed = False
    snapshot_history_counts = []

    def observe(runtime_home, current):
        nonlocal failed
        transaction = current.get("config_transaction")
        if transaction:
            snapshot_history_counts.append(
                len(transaction["previous_receipt"].get("drift_backup_history", {}))
            )
        checkpoint(runtime_home, current)
        if transaction and failure == "before-publication" and not failed:
            failed = True
            raise RuntimeError("injected before publication")

    def replace_and_fail(entry, source):
        nonlocal failed
        replace_entry(entry, source)
        if failure == "after-replacement" and not failed:
            failed = True
            raise RuntimeError("injected after replacement")

    monkeypatch.setattr(installer, "_checkpoint_runtime_install_receipt", observe)
    monkeypatch.setattr(
        installer, "_replace_runtime_transaction_entry", replace_and_fail
    )
    with pytest.raises(RuntimeError, match="injected"):
        installer._publish_runtime_config_transaction(
            paths,
            receipt,
            previous,
            [(destination, after), (selector, next_selector)],
            staging,
            installer._runtime_config_digest(destination),
        )
    if failure == "before-publication":
        assert (destination / "fixture.txt").read_text() == "old\n"
        assert selector.read_text() == "old\n"
        receipt = installer._load_runtime_install_receipt(
            installer._runtime_receipt_path(paths["runtime_home"])
        )
        installer._rollback_runtime_config_transaction(paths, receipt)
    assert (destination / "fixture.txt").read_text() == "old\n"
    assert selector.read_text() == "old\n"
    assert receipt["version"] == previous["version"]
    assert receipt["owned_symlinks"] == previous["owned_symlinks"]
    assert staged_generation in receipt["owned_dirs"]
    assert receipt["backups"] == previous.get("backups", {})
    assert receipt["drift_backups"] == previous.get("drift_backups", {})
    history = receipt["drift_backup_history"]
    assert all(
        history[key] == value for key, value in previous["drift_backup_history"].items()
    )
    assert history[str(destination)] == [str(staging / "before-0")]
    assert history[str(selector)] == [str(staging / "before-1")]
    for key in _RETIREMENT_LEDGERS:
        assert receipt[key] == previous[key] | {
            staged_generation: {"captured_after_previous": key}
        }
    assert "config_transaction" not in receipt
    assert "install_pending" not in receipt
    assert _receipt_bytes(previous) == previous_bytes
    assert snapshot_history_counts and not any(snapshot_history_counts)


def test_loader_compacts_legacy_config_transaction_history_idempotently(
    tmp_path: Path,
) -> None:
    previous = _previous_receipt()
    previous["retirement_generations"] = {"old": {"digest": "a" * 64}}
    receipt = copy.deepcopy(previous)
    receipt["config_transaction"] = {
        "schema": "vibecrafted.config-publication.v1",
        "entries": [{"before_digest": "b" * 64, "phase": "prepared"}],
        "previous_receipt": copy.deepcopy(previous),
    }
    path = tmp_path / "install-receipt.json"
    original = _receipt_bytes(receipt)
    path.write_bytes(original)
    loaded = installer._load_runtime_install_receipt(path)
    saved = loaded["config_transaction"]["previous_receipt"]
    assert saved == installer._recovery_receipt_snapshot(previous)
    assert loaded["drift_backup_history"] == previous["drift_backup_history"]
    assert loaded["retirement_generations"] == previous["retirement_generations"]
    assert (
        loaded["config_transaction"]["entries"]
        == receipt["config_transaction"]["entries"]
    )
    compacted = _receipt_bytes(loaded)
    assert _receipt_bytes(installer._compact_legacy_receipt_layers(loaded)) == compacted
    assert path.read_bytes() == original


def test_loader_compacts_all_legacy_recovery_retirement_duplicates(
    tmp_path: Path,
) -> None:
    previous = _previous_receipt()
    receipt = copy.deepcopy(previous)
    receipt["preparing_previous_receipt"] = copy.deepcopy(previous)
    receipt["config_transaction"] = {
        "schema": "vibecrafted.config-publication.v1",
        "entries": [{"before_digest": "b" * 64, "phase": "prepared"}],
        "previous_receipt": copy.deepcopy(previous),
    }
    path = tmp_path / "install-receipt.json"
    original = _receipt_bytes(receipt)
    path.write_bytes(original)
    loaded = installer._load_runtime_install_receipt(path)
    for saved in (
        loaded["preparing_previous_receipt"],
        loaded["config_transaction"]["previous_receipt"],
    ):
        assert not any(key in saved for key in _RETIREMENT_LEDGERS)
    assert all(loaded[key] == previous[key] for key in _RETIREMENT_LEDGERS)
    assert len(_receipt_bytes(loaded)) < len(original) // 2
    assert installer._compact_legacy_receipt_layers(copy.deepcopy(loaded)) == loaded
    assert path.read_bytes() == original


@pytest.mark.parametrize("key", _RETIREMENT_LEDGERS)
@pytest.mark.parametrize(
    "location",
    ["preparing", "transaction", "rollback", "deep-preparing", "deep-rollback"],
)
def test_loader_refuses_to_discard_snapshot_only_retirement_proof(
    tmp_path: Path, key: str, location: str
) -> None:
    receipt = {"schema": installer.RUNTIME_INSTALL_SCHEMA, "roots": {}}
    snapshot = {key: {"legacy": {"sha256": "a" * 64}}}
    if location == "preparing":
        receipt["preparing_previous_receipt"] = snapshot
    elif location == "transaction":
        receipt["config_transaction"] = {"previous_receipt": snapshot}
    elif location == "rollback":
        receipt["retirement_rollback"] = {"receipt": snapshot}
    elif location == "deep-preparing":
        receipt["preparing_previous_receipt"] = {"preparing_previous_receipt": snapshot}
    else:
        receipt["preparing_previous_receipt"] = {
            "retirement_rollback": {"receipt": snapshot}
        }
    path = tmp_path / "install-receipt.json"
    original = _receipt_bytes(receipt)
    path.write_bytes(original)
    with pytest.raises(
        installer.RuntimeInstallRefusal, match="only in a recovery snapshot"
    ):
        installer._load_runtime_install_receipt(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("live_present", [False, True])
def test_restore_preserves_snapshot_only_proof_but_live_empty_maps_win(
    tmp_path: Path, live_present: bool
) -> None:
    paths = {"runtime_home": tmp_path}
    receipt = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "roots": {"runtime_home": str(tmp_path)},
    }
    proof = {key: {"legacy": {"sha256": "a" * 64}} for key in _RETIREMENT_LEDGERS}
    restored = {**receipt, "version": "old", **copy.deepcopy(proof)}
    if live_present:
        receipt.update({key: {} for key in _RETIREMENT_LEDGERS})
    installer._restore_runtime_publication_receipt(paths, receipt, restored)
    assert all(
        receipt[key] == ({} if live_present else proof[key])
        for key in _RETIREMENT_LEDGERS
    )
    loaded = installer._load_runtime_install_receipt(
        installer._runtime_receipt_path(tmp_path)
    )
    assert all(loaded[key] == receipt[key] for key in _RETIREMENT_LEDGERS)


@pytest.mark.parametrize("published", [False, True], ids=["prepared", "replaced"])
def test_first_install_empty_previous_survives_checkpoint_load_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: bool
) -> None:
    """An interrupted first install has no previous identity, even after load."""
    paths, destination, selector, _ = _receipt_publication_scene(tmp_path, monkeypatch)
    staged_generation = str(paths["runtime_home"] / "releases/new")
    captured = str(paths["runtime_home"] / ".installer-backups/original/captured")
    receipt = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "roots": {key: str(value) for key, value in paths.items()},
        "version": "4.4.0+gnew",
        "install_pending": True,
        "install_phase": "preparing",
        "owned_dirs": [staged_generation],
        "backups": {str(selector): captured},
        "drift_backup_history": {str(selector): [captured]},
    }
    staging = paths["runtime_home"] / ".installer-backups/first-install"
    after = staging / "after-config"
    after.mkdir(parents=True)
    (after / "fixture.txt").write_text("new\n")
    next_selector = staging / "after-active"
    next_selector.write_text("new\n")
    checkpoint = installer._checkpoint_runtime_install_receipt

    def interrupt(runtime_home, current):
        checkpoint(runtime_home, current)
        raise RuntimeError("interrupted first publication")

    monkeypatch.setattr(installer, "_checkpoint_runtime_install_receipt", interrupt)
    with pytest.raises(RuntimeError, match="interrupted first publication"):
        installer._publish_runtime_config_transaction(
            paths,
            receipt,
            {},
            [(destination, after), (selector, next_selector)],
            staging,
            installer._runtime_config_digest(destination),
        )
    monkeypatch.setattr(installer, "_checkpoint_runtime_install_receipt", checkpoint)
    path = installer._runtime_receipt_path(paths["runtime_home"])
    assert json.loads(path.read_bytes())["config_transaction"]["previous_receipt"] == {}
    loaded = installer._load_runtime_install_receipt(path)
    assert loaded["config_transaction"]["previous_receipt"] == {}
    if published:
        # Model death after one real replacement, before the transaction seals.
        entry = loaded["config_transaction"]["entries"][0]
        entry["phase"] = "replacing"
        checkpoint(paths["runtime_home"], loaded)
        installer._replace_runtime_transaction_entry(entry, after)
        assert (destination / "fixture.txt").read_text() == "new\n"
    checkpoint(paths["runtime_home"], loaded)
    recovered = installer._load_runtime_install_receipt(path)
    assert recovered["config_transaction"]["previous_receipt"] == {}
    installer._rollback_runtime_config_transaction(paths, recovered)
    restored = installer._load_runtime_install_receipt(path)
    assert (destination / "fixture.txt").read_text() == "old\n"
    assert selector.read_text() == "old\n"
    assert restored["schema"] == installer.RUNTIME_INSTALL_SCHEMA
    assert restored["roots"] == receipt["roots"]
    assert restored["install_pending"] is True
    assert "version" not in restored
    assert "config_transaction" not in restored
    assert restored["owned_dirs"] == [staged_generation]
    assert restored["backups"] == {str(selector): captured}
    assert restored["drift_backup_history"][str(destination)] == [
        str(staging / "before-0")
    ]
    assert restored["drift_backup_history"][str(selector)] == [
        captured,
        str(staging / "before-1"),
    ]
