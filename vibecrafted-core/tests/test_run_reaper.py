"""Unit tests for the stale-run reaper.

Every test drives a fake process table and a fake env index; no test signals a
real process. The one real-process proof lives in the report, not the suite.
"""

from __future__ import annotations

import signal
import subprocess
from types import SimpleNamespace

import pytest
from vibecrafted_core import run_reaper

TERMINAL_RUN = {
    "run_id": "impl-260720-000001-11000",
    "state": "completed",
    "exit_code": 0,
    "worker_pgid": 4242,
}
LIVE_RUN = {
    "run_id": "impl-260720-000002-22000",
    "state": "running",
    "worker_pgid": 5252,
}


def entry(
    pid: int, ppid: int = 1, pgid: int | None = None, command: str = "node agent"
):
    return run_reaper.ProcessEntry(
        pid=pid, ppid=ppid, pgid=pid if pgid is None else pgid, command=command
    )


# --------------------------------------------------------------------------
# Ownership proof — positive
# --------------------------------------------------------------------------


def test_env_run_id_proves_ownership():
    table = [entry(900, command="voc monitor")]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN],
        table,
        env_index={900: TERMINAL_RUN["run_id"]},
        self_pid=1000,
        env={},
    )
    assert [c.pid for c in plan.doomed] == [900]
    assert plan.doomed[0].evidence == "env_run_id"
    assert plan.doomed[0].run_id == TERMINAL_RUN["run_id"]


def test_worker_pgid_proves_ownership_without_env():
    """The orphan case: parent is gone (ppid=1) and the env is unreadable."""
    table = [entry(901, ppid=1, pgid=4242, command="/bin/sleep 99")]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN], table, env_index={}, self_pid=1000, env={}
    )
    assert [c.pid for c in plan.doomed] == [901]
    assert plan.doomed[0].evidence == "worker_pgid"


def test_worker_pid_also_registers_as_a_group():
    run = {"run_id": "r-1", "state": "completed", "worker_pid": 7777}
    table = [entry(902, pgid=7777)]
    plan = run_reaper.plan_reap([run], table, env_index={}, self_pid=1000, env={})
    assert [c.pid for c in plan.doomed] == [902]


# --------------------------------------------------------------------------
# Ownership proof — refusal (fail-closed)
# --------------------------------------------------------------------------


def test_untagged_process_is_never_reaped():
    """No env, no matching pgid — the reaper must not touch it at all."""
    table = [entry(903, pgid=9999, command="some-unrelated-daemon")]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN], table, env_index={}, self_pid=1000, env={}
    )
    assert plan.doomed == ()
    assert plan.unproven == ()


def test_process_of_a_live_run_is_unproven_not_doomed():
    table = [entry(904, pgid=5252)]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN, LIVE_RUN],
        table,
        env_index={904: LIVE_RUN["run_id"]},
        self_pid=1000,
        env={},
    )
    assert plan.doomed == ()
    assert [c.pid for c in plan.unproven] == [904]
    assert plan.unproven[0].proven is False


def test_run_id_in_a_command_line_cannot_forge_ownership():
    """Only a real SPAWN_RUN_ID= token counts; a mention in argv is not proof."""
    index = run_reaper.build_env_index(
        runner=lambda argv: SimpleNamespace(
            returncode=0,
            stdout=(
                "  PID   TT  STAT      TIME COMMAND\n"
                f"  905   ??  Ss     0:00.00 grep --run 'SPAWN_RUN_ID:{TERMINAL_RUN['run_id']}'\n"
                f"  906   ??  Ss     0:00.00 node agent SPAWN_RUN_ID={TERMINAL_RUN['run_id']}\n"
            ),
        )
    )
    assert 905 not in index
    assert index[906] == TERMINAL_RUN["run_id"]


def test_pgid_zero_and_one_never_prove_ownership():
    """A run recording pgid 0/1 would otherwise match half the machine."""
    run = {"run_id": "r-2", "state": "completed", "worker_pgid": 1}
    table = [entry(907, pgid=1), entry(908, pgid=0)]
    plan = run_reaper.plan_reap([run], table, env_index={}, self_pid=1000, env={})
    assert plan.doomed == ()


def test_non_terminal_runs_contribute_no_targets():
    table = [entry(909, pgid=5252)]
    plan = run_reaper.plan_reap([LIVE_RUN], table, env_index={}, self_pid=1000, env={})
    assert plan.doomed == ()


# --------------------------------------------------------------------------
# Self-preservation
# --------------------------------------------------------------------------


def test_reaper_never_kills_itself_or_its_ancestors():
    """The terminal-seam caller is itself a process of the run being reaped."""
    table = [
        entry(500, ppid=1, pgid=4242, command="launcher.sh"),  # our parent
        entry(600, ppid=500, pgid=4242, command="python -m run_reaper"),  # us
        entry(700, ppid=1, pgid=4242, command="voc monitor"),  # sibling: fair game
    ]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN],
        table,
        env_index={p: TERMINAL_RUN["run_id"] for p in (500, 600, 700)},
        self_pid=600,
        env={},
    )
    assert [c.pid for c in plan.doomed] == [700]


def test_pid_one_is_never_a_candidate():
    table = [entry(1, ppid=0, pgid=4242, command="/sbin/launchd")]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN],
        table,
        env_index={1: TERMINAL_RUN["run_id"]},
        self_pid=1000,
        env={},
    )
    assert plan.doomed == ()


@pytest.mark.parametrize(
    "command",
    ["vc-frame --server", "zellij attach main", "loctree-mcp serve", "tmux new"],
)
def test_workspace_infrastructure_is_protected_even_when_proven(command):
    table = [entry(910, pgid=4242, command=command)]
    plan = run_reaper.plan_reap(
        [TERMINAL_RUN], table, env_index={}, self_pid=1000, env={}
    )
    assert plan.doomed == ()
    assert [c.pid for c in plan.protected] == [910]


# --------------------------------------------------------------------------
# Kill switch
# --------------------------------------------------------------------------


def test_kill_switch_disables_planning():
    table = [entry(911, pgid=4242)]
    for value in ("0", "false", "no", "off", "OFF"):
        plan = run_reaper.plan_reap(
            [TERMINAL_RUN],
            table,
            env_index={},
            self_pid=1000,
            env={run_reaper.REAPER_ENABLED_ENV: value},
        )
        assert plan.should_run is False
        assert plan.skip_reason == "disabled"
        assert plan.doomed == ()


def test_reaper_enabled_by_default():
    assert run_reaper.reaper_enabled({}) is True
    assert run_reaper.reaper_enabled({run_reaper.REAPER_ENABLED_ENV: "1"}) is True


def test_grace_seconds_falls_back_on_garbage():
    assert run_reaper.grace_seconds({}) == run_reaper.DEFAULT_GRACE_SECONDS
    assert (
        run_reaper.grace_seconds({run_reaper.REAPER_GRACE_ENV: "nonsense"})
        == run_reaper.DEFAULT_GRACE_SECONDS
    )
    assert (
        run_reaper.grace_seconds({run_reaper.REAPER_GRACE_ENV: "-4"})
        == run_reaper.DEFAULT_GRACE_SECONDS
    )
    assert run_reaper.grace_seconds({run_reaper.REAPER_GRACE_ENV: "2.5"}) == 2.5


# --------------------------------------------------------------------------
# Escalation order
# --------------------------------------------------------------------------


def test_escalation_terms_then_kills_a_survivor():
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(
            run_reaper.ReapCandidate(
                pid=920, command="voc", run_id="r", evidence="env_run_id"
            ),
        ),
    )
    sent: list[tuple[int, int]] = []

    receipts = run_reaper.execute_reap(
        plan,
        grace=0,
        sleeper=lambda _: None,
        alive_check=lambda pid: (pid, signal.SIGKILL) not in sent,
        signaller=lambda pid, sig: (sent.append((pid, sig)), "signalled")[1],
    )

    assert sent == [(920, signal.SIGTERM), (920, signal.SIGKILL)]
    row = receipts["r"].reaped[0]
    assert row["term"] == "signalled"
    assert row["kill"] == "signalled"
    assert row["outcome"] == "killed"


def test_escalation_stops_at_term_when_the_process_exits():
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(
            run_reaper.ReapCandidate(
                pid=921, command="voc", run_id="r", evidence="worker_pgid"
            ),
        ),
    )
    sent: list[tuple[int, int]] = []

    receipts = run_reaper.execute_reap(
        plan,
        grace=0,
        sleeper=lambda _: None,
        alive_check=lambda pid: False,
        signaller=lambda pid, sig: (sent.append((pid, sig)), "signalled")[1],
    )

    assert sent == [(921, signal.SIGTERM)]
    row = receipts["r"].reaped[0]
    assert row["outcome"] == "exited"
    assert "kill" not in row


def test_delivered_sigkill_reads_as_killed_not_survived():
    """A killed orphan lingers as a zombie; that must not be reported as survival."""
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(
            run_reaper.ReapCandidate(
                pid=924, command="voc", run_id="r", evidence="env_run_id"
            ),
        ),
    )
    receipts = run_reaper.execute_reap(
        plan,
        grace=0,
        sleeper=lambda _: None,
        alive_check=lambda pid: True,  # zombie: still visible to kill(pid, 0)
        signaller=lambda pid, sig: "signalled",
    )
    assert receipts["r"].reaped[0]["outcome"] == "killed"


def test_undeliverable_sigkill_reads_as_survived():
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(
            run_reaper.ReapCandidate(
                pid=925, command="voc", run_id="r", evidence="env_run_id"
            ),
        ),
    )
    receipts = run_reaper.execute_reap(
        plan,
        grace=0,
        sleeper=lambda _: None,
        alive_check=lambda pid: True,
        signaller=lambda pid, sig: "permission_denied",
    )
    assert receipts["r"].reaped[0]["outcome"] == "survived"


def test_already_gone_pid_is_receipted_not_escalated():
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(
            run_reaper.ReapCandidate(
                pid=922, command="voc", run_id="r", evidence="env_run_id"
            ),
        ),
    )
    receipts = run_reaper.execute_reap(
        plan,
        grace=0,
        sleeper=lambda _: None,
        alive_check=lambda pid: False,
        signaller=lambda pid, sig: "already_gone",
    )
    assert receipts["r"].reaped[0]["term"] == "already_gone"


def test_grace_window_is_waited_before_kill():
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(
            run_reaper.ReapCandidate(
                pid=923, command="voc", run_id="r", evidence="env_run_id"
            ),
        ),
    )
    slept: list[float] = []
    run_reaper.execute_reap(
        plan,
        grace=0.3,
        sleeper=lambda s: slept.append(s),
        alive_check=lambda pid: True,
        signaller=lambda pid, sig: "signalled",
    )
    assert slept, "expected the reaper to wait out the grace window before SIGKILL"


def test_disabled_plan_signals_nothing():
    plan = run_reaper.ReapPlan(should_run=False, skip_reason="disabled")
    receipts = run_reaper.execute_reap(
        plan,
        grace=0,
        sleeper=lambda _: None,
        alive_check=lambda pid: True,
        signaller=lambda pid, sig: pytest.fail("must not signal when disabled"),
    )
    assert receipts == {}


# --------------------------------------------------------------------------
# Rendering / dry run
# --------------------------------------------------------------------------


def test_dry_run_render_shows_evidence_for_each_verdict():
    plan = run_reaper.ReapPlan(
        should_run=True,
        doomed=(run_reaper.ReapCandidate(930, "voc monitor", "r-term", "env_run_id"),),
        unproven=(run_reaper.ReapCandidate(931, "helper", "r-live"),),
        protected=(run_reaper.ReapCandidate(932, "vc-frame", "r-term", "worker_pgid"),),
    )
    out = plan.render()
    assert "REAP" in out and "930" in out and "env_run_id" in out
    assert "KEEP/unproven" in out and "931" in out
    assert "KEEP/protected" in out and "932" in out


def test_render_of_disabled_plan_names_the_reason():
    assert "disabled" in run_reaper.ReapPlan(False, "disabled").render()


def test_render_with_no_survivors():
    assert "no survivors" in run_reaper.ReapPlan(should_run=True).render()


def test_dry_run_never_signals(monkeypatch):
    monkeypatch.setattr(
        run_reaper,
        "execute_reap",
        lambda *a, **k: pytest.fail("dry-run must not execute"),
    )
    plan = run_reaper.reap_terminal_runs(
        dry_run=True,
        env={},
        runs=[TERMINAL_RUN],
        table=[entry(940, pgid=4242)],
        env_index={},
    )
    assert [c.pid for c in plan.doomed] == [940]


# --------------------------------------------------------------------------
# Table / index parsing
# --------------------------------------------------------------------------


def test_process_table_parses_ps_output():
    table = run_reaper.build_process_table(
        runner=lambda argv: SimpleNamespace(
            returncode=0,
            stdout=(
                "    1     0     1 /sbin/launchd\n"
                "  500   400   500 node /path/agent --flag value\n"
                "garbage line\n"
            ),
        )
    )
    assert [e.pid for e in table] == [1, 500]
    assert table[1].command == "node /path/agent --flag value"
    assert table[1].ppid == 400


def test_process_table_is_empty_when_ps_fails():
    assert (
        run_reaper.build_process_table(
            runner=lambda argv: SimpleNamespace(returncode=1, stdout="")
        )
        == ()
    )

    def boom(argv):
        raise OSError("no ps")

    assert run_reaper.build_process_table(runner=boom) == ()


def test_env_index_empty_when_ps_fails():
    def boom(argv):
        raise subprocess.SubprocessError("nope")

    assert run_reaper.build_env_index(runner=boom) == {}


def test_reap_is_disabled_without_a_process_table():
    """No table means no evidence; the reaper must refuse rather than guess."""
    plan = run_reaper.reap_terminal_runs(env={}, runs=[TERMINAL_RUN], table=[])
    assert plan.should_run is False
    assert plan.skip_reason == "no_process_table"


def test_reap_never_raises_on_internal_failure(monkeypatch):
    monkeypatch.setattr(
        run_reaper,
        "plan_reap",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    plan = run_reaper.reap_terminal_runs(
        env={}, runs=[TERMINAL_RUN], table=[entry(950)], env_index={}
    )
    assert plan.should_run is False
    assert plan.skip_reason == "error"


# --------------------------------------------------------------------------
# G5 — reaper_ownership buckets + legacy quarantine migration
# --------------------------------------------------------------------------


LEGACY_TERMINAL = {
    "run_id": "impl-legacy-000001-11000",
    "state": "completed",
    "exit_code": 0,
    # no worker_pgid — historical run predating the second ownership proof
}

LEGACY_MARKED = {
    "run_id": "impl-legacy-000002-22000",
    "state": "completed",
    "exit_code": 0,
    "reaper_ownership": "legacy",
}

PROVABLE_TERMINAL = {
    "run_id": "impl-provable-000003-33000",
    "state": "completed",
    "exit_code": 0,
    "worker_pgid": 7777,
}

LIVE_NO_PGID = {
    "run_id": "impl-live-000004-44000",
    "state": "running",
    # no worker_pgid — still live; migration must not mark legacy
}


def test_classify_run_ownership_three_buckets():
    assert run_reaper.classify_run_ownership(LEGACY_MARKED) == "legacy"
    assert run_reaper.classify_run_ownership(PROVABLE_TERMINAL) == "provable"
    assert run_reaper.classify_run_ownership(LEGACY_TERMINAL) == "undecidable"
    assert run_reaper.classify_run_ownership(LIVE_NO_PGID) == "undecidable"


def test_quarantine_marks_terminal_without_pgid_as_legacy():
    """Three fixtures: terminal-no-pgid → legacy; terminal-with-pgid → untouched; live → skipped."""
    written: dict[str, dict] = {}

    def writer(run_id: str, payload: dict) -> None:
        written[run_id] = dict(payload)

    result = run_reaper.quarantine_legacy_runs(
        runs=[LEGACY_TERMINAL, PROVABLE_TERMINAL, LIVE_NO_PGID],
        table=(),
        env_index={},
        writer=writer,
    )

    assert LEGACY_TERMINAL["run_id"] in result.marked_legacy
    assert written[LEGACY_TERMINAL["run_id"]]["reaper_ownership"] == "legacy"
    assert "worker_pgid" not in written[LEGACY_TERMINAL["run_id"]]

    assert PROVABLE_TERMINAL["run_id"] in result.skipped_has_pgid
    assert PROVABLE_TERMINAL["run_id"] not in written

    assert LIVE_NO_PGID["run_id"] in result.skipped_live
    assert LIVE_NO_PGID["run_id"] not in written


def test_quarantine_is_idempotent():
    store: dict[str, dict] = {
        LEGACY_TERMINAL["run_id"]: dict(LEGACY_TERMINAL),
    }

    def writer(run_id: str, payload: dict) -> None:
        store[run_id] = dict(payload)

    first = run_reaper.quarantine_legacy_runs(
        runs=[store[LEGACY_TERMINAL["run_id"]]],
        table=(),
        env_index={},
        writer=writer,
    )
    assert first.changed == 1
    assert store[LEGACY_TERMINAL["run_id"]]["reaper_ownership"] == "legacy"

    second = run_reaper.quarantine_legacy_runs(
        runs=[store[LEGACY_TERMINAL["run_id"]]],
        table=(),
        env_index={},
        writer=writer,
    )
    assert second.changed == 0
    assert second.marked_legacy == []
    assert LEGACY_TERMINAL["run_id"] in second.already_legacy


def test_quarantine_recovers_pgid_for_live_run_with_env_proof():
    """Best-effort: live run without pgid gets worker_pgid only via SPAWN_RUN_ID proof."""
    written: dict[str, dict] = {}

    def writer(run_id: str, payload: dict) -> None:
        written[run_id] = dict(payload)

    live = dict(LIVE_NO_PGID)
    table = [entry(8801, pgid=8801, command="node agent")]
    env_index = {8801: live["run_id"]}

    result = run_reaper.quarantine_legacy_runs(
        runs=[live],
        table=table,
        env_index=env_index,
        writer=writer,
    )

    assert result.recovered_pgid
    assert result.recovered_pgid[0]["run_id"] == live["run_id"]
    assert result.recovered_pgid[0]["worker_pgid"] == 8801
    assert written[live["run_id"]]["worker_pgid"] == 8801
    assert written[live["run_id"]].get("reaper_ownership") != "legacy"


def test_quarantine_does_not_guess_pgid_without_env_proof():
    """A matching pgid alone on a live run is NOT enough — need SPAWN_RUN_ID."""
    written: dict[str, dict] = {}

    def writer(run_id: str, payload: dict) -> None:
        written[run_id] = dict(payload)

    live = dict(LIVE_NO_PGID)
    # process group exists but env is silent (macOS SIP case without SPAWN_RUN_ID readable)
    table = [entry(8802, pgid=8802, command="node agent")]
    result = run_reaper.quarantine_legacy_runs(
        runs=[live],
        table=table,
        env_index={},  # no SPAWN_RUN_ID visible
        writer=writer,
    )
    assert result.recovered_pgid == []
    assert live["run_id"] not in written or "worker_pgid" not in written.get(
        live["run_id"], {}
    )


def test_legacy_run_never_produces_kill_candidates():
    """Even with a live process carrying SPAWN_RUN_ID of a legacy run — KEEP, never REAP."""
    table = [entry(9901, pgid=9901, command="voc orphan")]
    plan = run_reaper.plan_reap(
        [LEGACY_MARKED, PROVABLE_TERMINAL],
        table,
        env_index={9901: LEGACY_MARKED["run_id"]},
        self_pid=1000,
        env={},
    )
    assert plan.doomed == ()
    assert any(c.pid == 9901 for c in plan.legacy)
    assert plan.legacy[0].evidence == "legacy"
    assert LEGACY_MARKED["run_id"] in plan.ownership.legacy


def test_legacy_run_pgid_match_also_not_killed():
    """Fail-closed: reaper_ownership=legacy wins even if a spurious pgid is present."""
    run = {
        "run_id": "impl-legacy-pgid-1",
        "state": "completed",
        "worker_pgid": 4242,
        "reaper_ownership": "legacy",
    }
    table = [entry(9902, pgid=4242, command="sleep 99")]
    plan = run_reaper.plan_reap([run], table, env_index={}, self_pid=1000, env={})
    assert plan.doomed == ()
    assert any(c.pid == 9902 for c in plan.legacy)


def test_undecidable_run_without_any_proof_is_never_killed():
    """Terminal run with neither pgid nor env proof — zero kill candidates, fail-closed."""
    table = [entry(9903, pgid=11111, command="some-daemon")]
    plan = run_reaper.plan_reap(
        [LEGACY_TERMINAL],  # no worker_pgid, not yet marked legacy
        table,
        env_index={},
        self_pid=1000,
        env={},
    )
    assert plan.doomed == ()
    assert LEGACY_TERMINAL["run_id"] in plan.ownership.undecidable
    assert plan.ownership.provable == ()


def test_reap_json_exposes_three_ownership_buckets():
    """reap --json must surface ownership.provable / .legacy / .undecidable explicitly."""
    plan = run_reaper.plan_reap(
        [LEGACY_MARKED, PROVABLE_TERMINAL, LEGACY_TERMINAL],
        table=[
            entry(7777, pgid=7777, command="voc survivor"),
            entry(8800, command="voc orphan"),
        ],
        env_index={8800: LEGACY_MARKED["run_id"]},
        self_pid=1000,
        env={},
    )
    payload = run_reaper.plan_json_payload(plan, dry_run=True)
    assert "ownership" in payload
    buckets = payload["ownership"]
    assert set(buckets.keys()) >= {"provable", "legacy", "undecidable"}
    assert PROVABLE_TERMINAL["run_id"] in buckets["provable"]
    assert LEGACY_MARKED["run_id"] in buckets["legacy"]
    assert LEGACY_TERMINAL["run_id"] in buckets["undecidable"]
    # process-level mirror: doomed processes appear under provable candidates
    assert any(row["pid"] == 7777 for row in payload["reaped"])
    assert any(row["pid"] == 8800 for row in payload["legacy"])
    # snapshot-stable key order surface for verifier
    assert payload["ownership"]["provable"]
    assert payload["ownership"]["legacy"]
    assert payload["ownership"]["undecidable"]


# --- F11: orphaned run-lock sweep -------------------------------------------


def _lock_file(tmp_path, name, body, *, age_seconds=0.0):
    import os
    import time as _time

    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    if age_seconds:
        stamp = _time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


def test_lock_sweep_removes_stale_running_lock_without_pid(tmp_path):
    """A month-old status=running lock with no PID is a zombie, not a run."""
    stale = _lock_file(
        tmp_path,
        "old.lock",
        "run_id=old\nstatus=running\n",
        age_seconds=30 * 24 * 3600,
    )
    result = run_reaper.sweep_orphaned_locks(lock_files=[stale])
    assert result["removed"] == [f"{stale}:stale_no_pid"]
    assert not stale.exists()


def test_lock_sweep_keeps_a_fresh_lock_and_a_live_pid(tmp_path):
    """Fresh starts and provably live launchers must survive the sweep."""
    fresh = _lock_file(tmp_path, "fresh.lock", "run_id=f\nstatus=running\n")
    live = _lock_file(
        tmp_path,
        "live.lock",
        "run_id=l\nstatus=running\nlauncher_pid=4242\n",
        age_seconds=3 * 3600,
    )
    result = run_reaper.sweep_orphaned_locks(
        lock_files=[fresh, live], alive_check=lambda pid: pid == 4242
    )
    assert result["removed"] == []
    assert fresh.exists() and live.exists()


def test_lock_sweep_removes_old_lock_with_dead_pid(tmp_path):
    dead = _lock_file(
        tmp_path,
        "dead.lock",
        "run_id=d\nstatus=running\nlauncher_pid=555\n",
        age_seconds=3 * 3600,
    )
    result = run_reaper.sweep_orphaned_locks(
        lock_files=[dead], alive_check=lambda pid: False
    )
    assert result["removed"] == [f"{dead}:pid_gone"]
    assert not dead.exists()


def test_lock_sweep_removes_terminal_status_lock(tmp_path):
    done = _lock_file(tmp_path, "done.lock", "run_id=x\nstatus=completed\n")
    result = run_reaper.sweep_orphaned_locks(lock_files=[done])
    assert result["removed"] == [f"{done}:terminal_status"]
    assert not done.exists()


def test_lock_sweep_dry_run_reports_without_deleting(tmp_path):
    stale = _lock_file(
        tmp_path,
        "old.lock",
        "run_id=old\nstatus=running\n",
        age_seconds=30 * 24 * 3600,
    )
    result = run_reaper.sweep_orphaned_locks(lock_files=[stale], dry_run=True)
    assert result["removed"] == [f"{stale}:stale_no_pid"]
    assert stale.exists()


def test_lock_sweep_never_raises_on_unreadable_entries(tmp_path):
    missing = tmp_path / "gone.lock"
    result = run_reaper.sweep_orphaned_locks(lock_files=[missing])
    assert result["removed"] == []
    assert result["kept"] == [str(missing)]
