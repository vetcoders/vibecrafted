"""Exercise the actual mutation owner with tiny, strictly signed local fixtures."""

from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import shutil
import stat
import subprocess
import time
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/vc-app-update.sh"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def lane(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    if not Path("/usr/bin/codesign").exists():
        pytest.skip("requires macOS strict codesign")
    parent = tmp_path / "Applications"
    parent.mkdir()
    env = {"PATH": os.environ["PATH"]}
    env.update(
        HOME=str(tmp_path / "home"),
        VIBECRAFTED_HOME=str(tmp_path / "crafted"),
        VIBECRAFTED_RUNTIME_HOME=str(tmp_path / "runtime"),
        VIBECRAFTED_UPDATE_HELPER_HARNESS="1",
    )
    for key in ("HOME", "VIBECRAFTED_HOME", "VIBECRAFTED_RUNTIME_HOME"):
        Path(env[key]).mkdir()
    return parent, env


def app(path: Path, number: int) -> Path:
    resources = path / "Contents/Resources"
    resources.mkdir(parents=True)
    executable = path / "Contents/MacOS/Vibecrafted"
    executable.parent.mkdir()
    executable.write_text(f"#!/bin/sh\nexit {number}\n")
    executable.chmod(0o755)
    with (path / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "io.vetcoders.vibecrafted",
                "CFBundleExecutable": "Vibecrafted",
            },
            handle,
        )
    write_json(
        resources / "product-manifest.json",
        {
            "schema": "io.vetcoders.vibecrafted.product.v1",
            "version": "4.3.1",
            "git_sha": f"{number:040x}",
            "modules": [],
        },
    )
    script_root = resources / "runtime/scripts"
    script_root.mkdir(parents=True)
    for name in (
        "vetcoders_install.py",
        "distribution_manifest.py",
        "installer_brand.py",
    ):
        shutil.copy(ROOT / "scripts" / name, script_root / name)
    core = resources / "runtime/vibecrafted-core/vibecrafted_core"
    core.mkdir(parents=True)
    shutil.copy(ROOT / "vibecrafted-core/vibecrafted_core/runtime_paths.py", core)
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(HELPER), *args, "--expected-team", "not set"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def replace(parent: Path, env: dict[str, str], number: int) -> tuple[list[str], Path]:
    source = app(parent.parent / f"source-{number}.app", number)
    transaction = f"capture-test-{number:08d}"
    state = Path(env["VIBECRAFTED_HOME"]) / "transactions" / transaction
    state.mkdir(parents=True)
    args = [
        "--source",
        str(source),
        "--destination",
        str(parent / "Vibecrafted.app"),
        "--receipt",
        str(state / "receipt.json"),
        "--admission",
        str(state / "admission.json"),
        "--journal",
        str(state / "journal.json"),
        "--transaction",
        transaction,
    ]
    result = run(args, env)
    assert result.returncode == 0, result.stderr
    return args, state


def publish(parent: Path, env: dict[str, str], number: int, **extra: object) -> None:
    runtime = Path(env["VIBECRAFTED_RUNTIME_HOME"])
    version = f"4.3.1+g{number:08x}"
    payload = seed_runtime_pack(
        parent.parent / f"pack-{number}-{time.time_ns()}", version=version
    )
    source = json.loads((payload / "source-provenance.json").read_text())
    source["source_revision"] = f"{number:040x}"
    (payload / "source-provenance.json").write_text(
        json.dumps(source, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    )
    write_json(
        payload / "runtime-pack-provenance.json",
        {
            "schema": "io.vetcoders.vibecrafted.runtime-pack-provenance.v1",
            "source_revisions": {"vibecrafted": f"{number:040x}"},
        },
    )
    # The only modeled boundary is external process/service teardown. The real
    # installer materializes and publishes, and its real read-only resolver is
    # invoked by the production helper before any disposal.
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(
            installer, "_teardown_owned_runtime_for_uninstall", lambda *_a, **_k: ()
        ),
        redirect_stdout(io.StringIO()),
    ):
        assert (
            installer.cmd_runtime_install(
                Namespace(
                    payload_root=str(payload),
                    app_root=str(parent / "Vibecrafted.app"),
                    terminal_host=None,
                    frame_helper=None,
                )
            )
            == 0
        )
    receipt = json.loads((runtime / "install-receipt.json").read_text())
    write_json(runtime / "install-receipt.json", {**receipt, **extra})


def test_five_upgrades_dispose_payload_keep_unique_receipts(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    states = []
    measurements = []
    for number in range(1, 6):
        args, state = replace(parent, env, number)
        before = sum(
            p.stat().st_size
            for root in parent.glob(".vc-update-capture-*")
            for p in root.rglob("*")
            if p.is_file()
        )
        publish(parent, env, number)
        result = run([*args, "--mode", "settle"], env)
        assert result.returncode == 0, result.stderr
        assert not list(parent.glob(".vc-update-capture-*"))
        assert (
            json.loads((state / "receipt.json.settlement.json").read_text())["status"]
            == "disposed"
        )
        states.append(
            {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir()}
        )
        measurements.append(
            {
                "upgrade": number,
                "payload_bytes_before": before,
                "payload_bytes_after": 0,
                "capture_count_after": 0,
                "receipt": str(state / "receipt.json"),
            }
        )
    for state in states:
        for path, digest in state.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    if evidence := os.environ.get("CAPTURE_LIFECYCLE_EVIDENCE_ROOT"):
        write_json(
            Path(evidence) / "repeated-upgrades.json", {"measurements": measurements}
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"install_pending": True},
        {"config_transaction": {}},
        {"rolled_back_at": "today"},
        {"install_pending": False, "rescue_pending": True},
    ],
)
def test_unresolved_or_failed_publication_keeps_rollback(lane, extra):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, _ = replace(parent, env, 1)
    publish(parent, env, 1, **extra)
    result = run([*args, "--mode", "settle"], env)
    assert result.returncode != 0
    assert (parent / ".vc-update-capture-capture-test-00000001/prior.app").is_dir()


@pytest.mark.parametrize("phase", ["settlement_authorized", "settlement_deleting"])
def test_cleanup_failure_retry_preserves_plan(lane, phase):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    publish(parent, env, 1)
    failed = run([*args, "--mode", "settle", "--fail-after", phase], env)
    assert failed.returncode != 0
    plan = state / "receipt.json.settlement-plan.json"
    assert plan.is_file()
    before = plan.read_bytes()
    success = run([*args, "--mode", "settle"], env)
    assert success.returncode == 0, success.stderr
    assert plan.read_bytes() == before


@pytest.mark.parametrize(
    "tamper", ["symlink", "foreign", "identity", "destination", "transaction", "inode"]
)
def test_settlement_rejects_wrong_or_foreign_capture(lane, tamper):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    publish(parent, env, 1)
    capture = parent / ".vc-update-capture-capture-test-00000001"
    if tamper == "symlink":
        saved = parent / "saved"
        capture.rename(saved)
        capture.symlink_to(saved, target_is_directory=True)
    elif tamper == "inode":
        saved = parent / "saved"
        capture.rename(saved)
        shutil.copytree(saved, capture)
    elif tamper == "foreign":
        (capture / "foreign.txt").write_text("Founder data")
    elif tamper == "identity":
        shutil.rmtree(capture / "prior.app")
        app(capture / "prior.app", 9)
    else:
        receipt = json.loads((state / "receipt.json").read_text())
        receipt[tamper] = (
            "/wrong.app" if tamper == "destination" else "wrong-transaction"
        )
        write_json(state / "receipt.json", receipt)
    result = run([*args, "--mode", "settle"], env)
    assert result.returncode != 0
    assert capture.exists()


def test_live_and_delayed_handoff_not_pruned(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    source = app(parent.parent / "source.app", 1)
    state = Path(env["VIBECRAFTED_HOME"]) / "pending"
    state.mkdir()
    args = [
        "--source",
        str(source),
        "--destination",
        str(parent / "Vibecrafted.app"),
        "--receipt",
        str(state / "receipt.json"),
        "--transaction",
        "delayed-transaction",
    ]
    release = state / "release"
    process = subprocess.Popen(
        [
            "/bin/bash",
            str(HELPER),
            *args,
            "--expected-team",
            "not set",
            "--hold-after",
            "ready",
            "--until",
            str(release),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not (state / "receipt.json.admission.json").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        lock = parent / ".vc-update.lock/held"
        inode = lock.stat().st_ino
        refused = run([*args, "--mode", "settle"], env)
        assert refused.returncode == 13
        release.touch()
        assert process.wait(timeout=20) == 0
        delayed = run([*args, "--mode", "settle"], env)
        assert delayed.returncode == 20  # no installer publication yet
        assert lock.stat().st_ino == inode
        assert (parent / ".vc-update-capture-delayed-transaction/prior.app").is_dir()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("tamper", ["runtime-bytes", "selector", "publication-source"])
def test_mismatched_publication_preserves_capture(lane, tamper):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, _state = replace(parent, env, 1)
    publish(parent, env, 1)
    runtime = Path(env["VIBECRAFTED_RUNTIME_HOME"])
    if tamper == "runtime-bytes":
        (runtime / "releases/4.3.1+g00000001/bin/vibecrafted").write_text("damaged")
    elif tamper == "selector":
        (runtime / "tools/vibecrafted-current").unlink()
        (runtime / "tools/vibecrafted-current").symlink_to(runtime)
    else:
        publish(parent, env, 2)
    result = run([*args, "--mode", "settle"], env)
    assert result.returncode == 20
    assert (parent / ".vc-update-capture-capture-test-00000001/prior.app").is_dir()


def test_failed_pack_restore_preserves_original_replace_chain(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    _args, state = replace(parent, env, 1)
    original = (state / "journal.json").read_bytes()
    original_receipt = (state / "receipt.json").read_bytes()
    prior = parent / ".vc-update-capture-capture-test-00000001/prior.app"
    restore = [
        "--source",
        str(prior),
        "--destination",
        str(parent / "Vibecrafted.app"),
        "--receipt",
        str(state / "restore.json"),
        "--journal",
        str(state / "journal.json"),
        "--transaction",
        "capture-test-00000001",
        "--mode",
        "restore",
    ]
    result = run(restore, env)
    assert result.returncode == 0, result.stderr
    assert (state / "journal.json.replace.json").read_bytes() == original
    assert (state / "receipt.json").read_bytes() == original_receipt
    assert json.loads((state / "restore.json").read_text())["detail"] == "restored"
    assert prior.is_dir()
    assert (
        (parent / "Vibecrafted.app/Contents/MacOS/Vibecrafted")
        .read_text()
        .endswith("exit 0\n")
    )


def test_historical_signed_chain_requires_exact_inode_authority(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    older, old_state = replace(parent, env, 1)
    newer, state = replace(parent, env, 2)
    publish(parent, env, 2)
    assert run([*newer, "--mode", "settle"], env).returncode == 0
    old_owner_path = old_state / "receipt.json.capture-owner.json"
    owner = json.loads(old_owner_path.read_text())
    old_owner_path.unlink()  # exact situation of pre-lifecycle historical captures
    history = old_state / "history.json"
    evidence = {
        **owner,
        "schema": "io.vetcoders.vibecrafted.app-capture-history.v1",
        "successor_settlements": [str(state / "receipt.json.settlement.json")],
    }
    write_json(history, {**evidence, "capture_inode": [0, 0]})
    rejected = run(
        [*older, "--mode", "settle", "--historical-evidence", str(history)], env
    )
    assert rejected.returncode == 20
    write_json(history, evidence)
    accepted = run(
        [*older, "--mode", "settle", "--historical-evidence", str(history)], env
    )
    assert accepted.returncode == 0, accepted.stderr
    assert not list(parent.glob(".vc-update-capture-*"))


def test_real_cli_wrapper_has_distinct_paths_and_bounded_capture(lane):
    parent, env = lane
    repo = parent.parent / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "dist").mkdir()
    shutil.copy(ROOT / "scripts/install-app.sh", repo / "scripts/install-app.sh")
    # Only fixture wiring differs: the real helper still performs all mutation,
    # strict codesign, locking, publication observation, and fd-bound disposal.
    (repo / "scripts/vc-app-update.sh").write_text(
        '#!/bin/bash\nexec /bin/bash "'
        + str(HELPER)
        + '" "$@" --expected-team "not set" --open-bin /usr/bin/true\n'
    )
    binaries = parent.parent / "fixture-bin"
    binaries.mkdir()
    (binaries / "pgrep").write_text("#!/bin/sh\nexit 1\n")
    (binaries / "pgrep").chmod(0o755)
    env.update(
        PATH=str(binaries) + ":" + env["PATH"],
        INSTALL_APP_DESTINATION=str(parent / "Vibecrafted.app"),
    )
    app(parent / "Vibecrafted.app", 0)
    receipts = []
    for number in (1, 2):
        source = repo / "dist/Vibecrafted.app"
        if source.exists():
            shutil.rmtree(source)
        app(source, number)
        # Fixture installer emits the canonical identity documents, without a
        # fake cleanup boolean. Owner must independently reject wrong tuples.
        pack_dir = source / "Contents/Resources/runtime-pack"
        pack_dir.mkdir()
        for suffix in ("", ".sha256", ".sig"):
            (pack_dir / ("Vibecrafted_RuntimePack_fixture.tar.gz" + suffix)).touch()
        publish(parent, env, number)
        wrapper = pack_dir / "install-runtime-pack.sh"
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        wrapper.chmod(0o755)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(source)],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["/bin/bash", str(repo / "scripts/install-app.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        pointer = (
            Path(env["VIBECRAFTED_HOME"])
            / "vibecrafted-product-update/install-app-latest.json"
        )
        latest = json.loads(pointer.read_text())
        receipts.append(latest["receipt"])
        assert Path(latest["settlement"]).is_file()
        assert not list(parent.glob(".vc-update-capture-*"))
    assert receipts[0] != receipts[1]
    assert all(Path(path).is_file() for path in receipts)


def test_same_generation_swift_completion_settles_raw_cli_capture(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    _args, state = replace(parent, env, 1)
    publish(parent, env, 1)
    shim = parent.parent / "fixture-helper.sh"
    shim.write_text(
        '#!/bin/bash\nexec /bin/bash "'
        + str(HELPER)
        + '" "$@" --expected-team "not set"\n'
    )
    shim.chmod(0o755)
    main = parent.parent / "Completion.swift"
    main.write_text("""import Foundation
@main struct Completion {
  static func main() throws {
    let args = CommandLine.arguments
    let result = settleProductUpdateLocalCaptures(
      home: URL(fileURLWithPath: args[1]), destination: URL(fileURLWithPath: args[2]),
      helper: URL(fileURLWithPath: args[3]), runtimeHome: URL(fileURLWithPath: args[4]))
    if case .failure(let error) = result { throw error }
  }
}
""")
    app_sources = ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted"
    files = [
        "RuntimePackMenuPolicy",
        "ServerMenuPolicy",
        "ProductUpdatePolicy",
        "ProductUpdateTransaction",
        "ProductUpdateTrust",
        "ProductUpdateReplacement",
        "ProductUpdateTransfer",
        "ProductUpdateProcess",
        "ProductUpdateCoordinator",
    ]
    binary = parent.parent / "completion"
    subprocess.run(
        [
            "/usr/bin/swiftc",
            "-swift-version",
            "6",
            *[str(app_sources / (name + ".swift")) for name in files],
            str(main),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    result = subprocess.run(
        [
            str(binary),
            env["VIBECRAFTED_HOME"],
            str(parent / "Vibecrafted.app"),
            str(shim),
            env["VIBECRAFTED_RUNTIME_HOME"],
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr[:2000]
    assert not list(parent.glob(".vc-update-capture-*"))
    assert (state / "receipt.json.settlement.json").is_file()


def test_historical_completed_restore_disposes_failed_candidate(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    owner = json.loads((state / "receipt.json.capture-owner.json").read_text())
    restore = [
        "--source",
        str(parent / ".vc-update-capture-capture-test-00000001/prior.app"),
        "--destination",
        str(parent / "Vibecrafted.app"),
        "--receipt",
        str(state / "restore.json"),
        "--journal",
        str(state / "journal.json"),
        "--transaction",
        "capture-test-00000001",
        "--mode",
        "restore",
    ]
    assert run(restore, env).returncode == 0
    publish(parent, env, 0)
    history = state / "history.json"
    write_json(
        history,
        {
            **owner,
            "schema": "io.vetcoders.vibecrafted.app-capture-history.v1",
            "successor_settlements": [],
            "recovery_receipt": str(state / "restore.json"),
            "recovery_journal": str(state / "journal.json"),
        },
    )
    result = run(
        [
            *args,
            "--journal",
            str(state / "journal.json"),
            "--mode",
            "settle",
            "--historical-evidence",
            str(history),
            "--plan-only",
        ],
        env,
    )
    assert result.returncode == 0, result.stderr
    assert (parent / ".vc-update-capture-capture-test-00000001/failed-new.app").is_dir()
    result = run(
        [
            *args,
            "--journal",
            str(state / "journal.json"),
            "--mode",
            "settle",
            "--historical-evidence",
            str(history),
        ],
        env,
    )
    assert result.returncode == 0, result.stderr
    assert not list(parent.glob(".vc-update-capture-*"))


def legacy_review(parent, env, state, history):
    capture = parent / ".vc-update-capture-capture-test-00000001"
    apps = {}
    entries = {}
    for path in capture.rglob("*"):
        info = path.lstat()
        value = [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid]
        if not path.is_dir():
            value += [info.st_size, info.st_mtime_ns]
        if path.name != ".DS_Store":
            entries[str(path.relative_to(capture))] = value
    for path in capture.iterdir():
        if path.name.endswith(".app"):
            shown = subprocess.run(
                ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            identity = next(
                line.split("=", 1)[1]
                for line in shown.stderr.splitlines()
                if line.startswith("CDHash=")
            )
            apps[path.name] = {
                "identity": "cdhash:" + identity,
                "inode": [path.stat().st_dev, path.stat().st_ino],
            }
    evidence = state / "legacy.json"
    write_json(
        evidence,
        {
            "schema": "io.vetcoders.vibecrafted.app-capture-legacy-admission.v1",
            "transaction": "capture-test-00000001",
            "capture": str(capture),
            "destination": str(parent / "Vibecrafted.app"),
            "parent_inode": [parent.stat().st_dev, parent.stat().st_ino],
            "capture_inode": [capture.stat().st_dev, capture.stat().st_ino],
            "parent_uid": parent.stat().st_uid,
            "uid": capture.stat().st_uid,
            "identifier": "io.vetcoders.vibecrafted",
            "team_id": "not set",
            "apps": apps,
            "entries": entries,
            "history": {"state": history, "records": []},
        },
    )
    return evidence


@pytest.mark.parametrize("history", ["missing", "failed-before-adoption", "restored"])
def test_legacy_review_preserves_history_without_forged_success(lane, history):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    capture = parent / ".vc-update-capture-capture-test-00000001"
    if history == "failed-before-adoption":
        shutil.rmtree(capture / "displaced.app")
    if history == "restored":
        restore = [
            "--source",
            str(capture / "prior.app"),
            "--destination",
            str(parent / "Vibecrafted.app"),
            "--receipt",
            str(state / "restore.json"),
            "--journal",
            str(state / "journal.json"),
            "--transaction",
            "capture-test-00000001",
            "--mode",
            "restore",
        ]
        assert run(restore, env).returncode == 0
        publish(parent, env, 0)
    else:
        publish(parent, env, 1)
    # Lost singleton and failed histories cannot be interpreted as replacement
    # success. Exact reviewed inventory plus explicit plan admission is authority.
    (state / "receipt.json").unlink()
    evidence = legacy_review(parent, env, state, history)
    command = [*args, "--mode", "settle", "--historical-evidence", str(evidence)]
    planned = run([*command, "--plan-only"], env)
    assert planned.returncode == 0, planned.stderr
    plan_hash = json.loads(planned.stdout)["plan_sha256"]
    refused = run(command, env)
    assert refused.returncode == 20
    assert capture.exists()
    applied = run([*command, "--admit-plan", plan_hash], env)
    assert applied.returncode == 0, applied.stderr
    result = json.loads((state / "receipt.json.settlement.json").read_text())
    assert result["history"]["state"] == history
    assert "source_identity" not in result
    assert not capture.exists()


@pytest.mark.parametrize(
    "tamper", ["foreign", "inode", "identity", "path", "live-pin", "live-apply", "plan"]
)
def test_legacy_review_refuses_unbound_or_live_payload(lane, tamper):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    publish(parent, env, 1)
    capture = parent / ".vc-update-capture-capture-test-00000001"
    evidence = legacy_review(parent, env, state, "missing")
    command = [*args, "--mode", "settle", "--historical-evidence", str(evidence)]
    pin = None
    if tamper == "foreign":
        (capture / "foreign").write_text("Founder data")
    elif tamper == "inode":
        capture.rename(parent / "saved")
        shutil.copytree(parent / "saved", capture)
    elif tamper in ("identity", "path"):
        data = json.loads(evidence.read_text())
        if tamper == "identity":
            data["apps"]["prior.app"]["identity"] = "cdhash:deadbeef"
        else:
            data["capture"] = str(parent / "another-capture")
        write_json(evidence, data)
    elif tamper == "live-pin":
        pin = (capture / "prior.app/Contents/Info.plist").open()
    try:
        planned = run([*command, "--plan-only"], env)
        if tamper in ("plan", "live-apply"):
            assert planned.returncode == 0, planned.stderr
            plan_hash = json.loads(planned.stdout)["plan_sha256"]
            if tamper == "live-apply":
                pin = (capture / "prior.app/Contents/Info.plist").open()
            else:
                plan_hash = "0" * 64
            assert run([*command, "--admit-plan", plan_hash], env).returncode == 20
        else:
            assert planned.returncode == 20, planned.stdout
        assert capture.exists()
    finally:
        if pin:
            pin.close()


def test_legacy_partial_disposal_retries_same_admitted_plan(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    publish(parent, env, 1)
    evidence = legacy_review(parent, env, state, "missing")
    command = [*args, "--mode", "settle", "--historical-evidence", str(evidence)]
    planned = run([*command, "--plan-only"], env)
    assert planned.returncode == 0, planned.stderr
    apply = [*command, "--admit-plan", json.loads(planned.stdout)["plan_sha256"]]
    failed = run([*apply, "--fail-after", "settlement_deleting"], env)
    assert failed.returncode == 20
    assert not (state / "receipt.json.settlement.json").exists()
    succeeded = run(apply, env)
    assert succeeded.returncode == 0, succeeded.stderr
    assert not list(parent.glob(".vc-update-capture-*"))


def test_legacy_real_preadoption_failure_keeps_original_journal(lane):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    source = app(parent.parent / "source.app", 1)
    state = Path(env["VIBECRAFTED_HOME"]) / "failed-records"
    state.mkdir()
    args = [
        "--source",
        str(source),
        "--destination",
        str(parent / "Vibecrafted.app"),
        "--receipt",
        str(state / "receipt.json"),
        "--journal",
        str(state / "journal.json"),
        "--transaction",
        "capture-test-00000001",
    ]
    failed = run([*args, "--fail-after", "captured"], env)
    assert failed.returncode != 0
    assert not (state / "receipt.json").exists()
    capture = parent / ".vc-update-capture-capture-test-00000001"
    assert {p.name for p in capture.iterdir()} == {"prior.app"}
    journal = (state / "journal.json").read_bytes()
    assert json.loads(journal)["phase"] == "captured"
    publish(parent, env, 0)
    evidence = legacy_review(parent, env, state, "failed-before-adoption")
    reviewed = json.loads(evidence.read_text())
    reviewed["history"]["records"] = [
        {
            "path": str(state / "journal.json"),
            "sha256": hashlib.sha256(journal).hexdigest(),
        }
    ]
    write_json(evidence, reviewed)
    command = [*args, "--mode", "settle", "--historical-evidence", str(evidence)]
    planned = run([*command, "--plan-only"], env)
    assert planned.returncode == 0, planned.stderr
    applied = run(
        [*command, "--admit-plan", json.loads(planned.stdout)["plan_sha256"]], env
    )
    assert applied.returncode == 0, applied.stderr
    assert not capture.exists()
    assert not (state / "receipt.json").exists()
    assert (state / "journal.json").read_bytes() == journal


@pytest.mark.parametrize("child", ["prior.app", "displaced.app"])
def test_automatic_settlement_preserves_live_capture_until_owner_releases(lane, child):
    parent, env = lane
    app(parent / "Vibecrafted.app", 0)
    args, state = replace(parent, env, 1)
    publish(parent, env, 1)
    capture = parent / ".vc-update-capture-capture-test-00000001"
    before = {
        str(p.relative_to(capture)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in capture.rglob("*")
        if p.is_file()
    }
    command = [*args, "--mode", "settle"]
    referenced = capture / child / "Contents/Info.plist"
    with referenced.open():
        planned = run([*command, "--plan-only"], env)
        assert planned.returncode == 20, planned.stdout
        assert "live mapped/open capture dependency" in planned.stderr
        assert not (state / "receipt.json.settlement-plan.json").exists()
    planned = run([*command, "--plan-only"], env)
    assert planned.returncode == 0, planned.stderr
    plan = (state / "receipt.json.settlement-plan.json").read_bytes()
    with referenced.open():
        refused = run(command, env)
        assert refused.returncode == 20
        assert "live mapped/open capture dependency" in refused.stderr
        assert {
            str(p.relative_to(capture)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in capture.rglob("*")
            if p.is_file()
        } == before
        assert (state / "receipt.json.settlement-plan.json").read_bytes() == plan
    completed = run(command, env)
    assert completed.returncode == 0, completed.stderr
    assert not capture.exists()
