"""Contract tests for ``vibecrafted reinstall --clean``.

No live process is signaled, no Frame session is created and nothing is
installed: every probe, the installer census and the subprocess runner are
doubles.  The doubles record calls so the tests can prove what did NOT happen.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import frame_layout, reinstall_clean
from vibecrafted_core.reinstall_clean import (
    MANIFEST_SCHEMA,
    PROVEN,
    UNKNOWN,
    AgentProcess,
    ProcessRecord,
    ProviderStores,
    ResurrectContext,
)

SOCKETS = Path("/tmp/vc-frame-test")
OLD = "/rt/releases/4.4.0+gold"
NEW = "/rt/releases/4.4.0+gnew"
CONFIG = "/srv/op/.config/vibecrafted/vc-frame"
REPO = "/work/vibecrafted"
T0 = 1_791_590_000  # process births, epoch seconds
CLAUDE_ID = "b564e45f-0616-4fe4-a8f0-9e1d5a0209ee"
HEADLESS_ID = "b3ec1232-7750-4e5e-9d7b-5902a98c9335"
CODEX_ID = "01a122bf-d6a9-7bc0-b535-b436fd7b75bd"
OTHER_CODEX = "01a1245c-c98f-73a1-9f4d-c277d77ff629"


def rec(pid: int, ppid: int, *argv: str, born: float = T0) -> ProcessRecord:
    sec = int(born)
    usec = round((born - sec) * 1_000_000)
    return ProcessRecord(pid, ppid, (f"darwin:{sec}:{usec}", 501, 8), tuple(argv))


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


class FakeProcs:
    def __init__(self, records: Sequence[ProcessRecord], cwds: dict[int, str]):
        self._table = {r.pid: r for r in records}
        self._cwds = cwds

    def table(self) -> dict[int, ProcessRecord]:
        return dict(self._table)

    def cwd(self, pids: Sequence[int]) -> dict[int, str]:
        return {p: self._cwds[p] for p in pids if p in self._cwds}


class FakeFrame:
    def __init__(self, sessions: dict[str, tuple[str, list[dict[str, Any]]]]):
        self.sessions = sessions
        self.calls: list[tuple[str, str]] = []

    def list_sessions(self) -> list[str]:
        return list(self.sessions)

    def dump_layout(self, session: str) -> str:
        self.calls.append(("dump-layout", session))
        return self.sessions[session][0]

    def list_panes(self, session: str) -> list[dict[str, Any]]:
        self.calls.append(("list-panes", session))
        return self.sessions[session][1]

    def save_session(self, session: str) -> None:
        self.calls.append(("save-session", session))


class FakeOwnership:
    """Installer census double that refuses every mutation unless armed."""

    def __init__(self, census: Sequence[ProcessRecord], *, armed: bool = False):
        self._census = list(census)
        self.armed = armed
        self.calls: list[Any] = []

    def census(self) -> list[ProcessRecord]:
        self.calls.append("census")
        return list(self._census)

    def caller_ancestors(self) -> frozenset[int]:
        return frozenset({4242})

    def roots(self) -> tuple[Path, ...]:
        return (Path("/rt/releases"), Path("/Applications/Vibecrafted.app"))

    def is_owned_argv(self, argv: Sequence[str]) -> bool:
        return bool(argv) and argv[0].startswith("/rt/releases/")

    def service_state(self) -> dict[str, Any]:
        return {"evidence": True, "launch_agent": "/LaunchAgents/x.plist"}

    def terminate(self, records: Sequence[ProcessRecord], *, label: str) -> None:
        if not self.armed:
            raise AssertionError(f"terminate called during a dry run: {label}")
        self.calls.append(("terminate", label, [r.pid for r in records]))
        killed = {r.pid for r in records}
        self._census = [r for r in self._census if r.pid not in killed]

    def stop_service(self) -> dict[str, Any]:
        if not self.armed:
            raise AssertionError("service stop called during a dry run")
        self.calls.append("stop-service")
        return {"action": "stopped", "was_live": True, "pair": "running"}


LAYOUT = f"""layout {{
    cwd "{REPO}"
    tab name="Start here" hide_floating_panes=true {{
        pane size=1 borderless=true {{
            plugin location="vc-frame:compact-bar" {{
                brand_text " Vibecrafted. "
            }}
        }}
        pane command="{OLD}/python/bin/python3.14" name="Start Here" {{
            args "{CONFIG}/vc-start-here.py"
            start_suspended true
        }}
    }}
    tab name="Shell" {{
        pane command="node" {{
            args "/usr/local/bin/codex" "--dangerously-bypass-approvals-and-sandbox" "resume"
            start_suspended true
        }}
    }}
    tab name="claude" focus=true {{
        pane command="bash" focus=true {{
            args "/art/tmp/vc-spawn-cmd.0704"
            start_suspended true
        }}
    }}
    tab name="codex" {{
        pane command="bash" {{
            args "/art/tmp/vc-spawn-cmd.0019"
            start_suspended true
        }}
    }}
    tab name="Tab #5" {{
        pane command="zsh" {{
            args "-l"
            start_suspended true
        }}
        pane command="vim" {{
            args "notes.md"
            start_suspended true
        }}
    }}
    new_tab_template {{
        pane
    }}
}}
"""

PANES = [
    {
        "id": 0,
        "tab_name": "Start here",
        "pane_command": f"{OLD}/python/bin/python3.14 {CONFIG}/vc-start-here.py",
        "pane_cwd": REPO,
    },
    {
        "id": 2,
        "tab_name": "Shell",
        "pane_command": "node /usr/local/bin/codex --dangerously-bypass-approvals-and-sandbox resume",
        "pane_cwd": REPO,
    },
    {
        "id": 5,
        "tab_name": "claude",
        "pane_command": "bash /art/tmp/vc-spawn-cmd.0704",
        "pane_cwd": REPO,
    },
    {
        "id": 3,
        "tab_name": "codex",
        "pane_command": "bash /art/tmp/vc-spawn-cmd.0019",
        "pane_cwd": REPO,
    },
    {"id": 4, "tab_name": "Tab #5", "pane_command": "zsh", "pane_cwd": REPO},
    {"id": 9, "tab_name": "Start here", "is_plugin": True},
]


def world_records() -> list[ProcessRecord]:
    server = rec(
        100,
        1,
        f"{OLD}/libexec/vc-frame",
        "--server",
        f"{SOCKETS}/contract_version_4/vibecrafted",
    )
    return [
        server,
        rec(110, 100, f"{OLD}/python/bin/python3.14", f"{CONFIG}/vc-start-here.py"),
        # Shell tab: codex resume picker typed into the shell
        rec(120, 100, "zsh", "-l", born=T0 + 10),
        rec(
            121,
            120,
            "node",
            "/usr/local/bin/codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "resume",
            born=T0 + 100,
        ),
        rec(
            122,
            121,
            "/vendor/bin/codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "resume",
            born=T0 + 100,
        ),
        # claude tab: spawn script -> interactive-launch -> claude --session-id
        rec(130, 100, "bash", "/art/tmp/vc-spawn-cmd.0704", born=T0 + 50),
        rec(
            131,
            130,
            f"{OLD}/python/bin/python3.14",
            "-m",
            "vibecrafted_core.spawn",
            "interactive-launch",
            "claude",
            "--admission-file",
            "/cp/runtime_runs/rsme-261010-070423-13091/admission.json",
            born=T0 + 50,
        ),
        rec(
            132,
            131,
            "/srv/op/.local/bin/claude",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
            "--session-id",
            CLAUDE_ID,
            "Read and follow the private task file: /cp/runtime_runs/rsme-261010-070423-13091/prompt.md",
            born=T0 + 51,
        ),
        # codex tab: fresh interactive codex born with its rollout
        rec(140, 100, "bash", "/art/tmp/vc-spawn-cmd.0019", born=T0 + 20),
        rec(
            141,
            140,
            f"{OLD}/python/bin/python3.14",
            "-m",
            "vibecrafted_core.spawn",
            "interactive-launch",
            "codex",
            born=T0 + 20,
        ),
        rec(
            142,
            141,
            "node",
            "/usr/local/bin/codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "Read and follow the private task file: /cp/runtime_runs/rsme-261010-001906-64375/prompt.md",
            born=T0 + 21,
        ),
        rec(150, 100, "zsh", born=T0 + 5),
        # headless worker outside Frame
        rec(
            200,
            1,
            f"{OLD}/python/bin/python3.14",
            "-m",
            "vibecrafted_core.dispatcher",
            "run",
            "--run-id",
            "work-261010-085255-54301",
            born=T0 + 200,
        ),
        rec(
            201,
            200,
            "/srv/op/.local/bin/claude",
            "--model",
            "claude-opus-5-5",
            "-p",
            "--output-format",
            "stream-json",
            born=T0 + 201,
        ),
        # Codescribe bus watcher borrowing our interpreter, and the App
        rec(
            300,
            1,
            f"{OLD}/python/bin/python3.14",
            "/srv/op/.local/bin/cs-bus",
            "--watch",
        ),
        rec(310, 1, "/Applications/Vibecrafted.app/Contents/MacOS/Vibecrafted"),
        # Founder's own claude in a foreign terminal: not ours
        rec(400, 1, "/srv/op/.local/bin/claude", born=T0 + 300),
    ]


CWDS = {121: REPO, 122: REPO, 132: REPO, 142: REPO, 201: "/work/wt", 400: "/srv/op"}


def make_stores(tmp_path: Path) -> ProviderStores:
    claude = tmp_path / ".claude" / "projects"
    codex = tmp_path / ".codex" / "sessions"
    project = claude / reinstall_clean.claude_project_slug(REPO)
    project.mkdir(parents=True)
    (project / f"{CLAUDE_ID}.jsonl").write_text('{"type":"user"}\n')
    day = codex / "2026" / "10" / "10"
    day.mkdir(parents=True)
    for sid, born, mtime in (
        (CODEX_ID, T0 + 22, T0 + 900),
        (OTHER_CODEX, T0 + 5000, T0 + 5001),
    ):
        path = day / f"rollout-x-{sid}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "timestamp": _iso(born),
                    "type": "session_meta",
                    "payload": {"id": sid, "cwd": REPO, "timestamp": _iso(born)},
                }
            )
            + "\n"
        )
        os.utime(path, (mtime, mtime))
    return ProviderStores(claude, codex)


def run_meta(run_id: str) -> dict[str, Any] | None:
    metas = {
        # requested id only (the spawn asked for it, the provider never confirmed)
        "rsme-261010-070423-13091": {
            "agent": "claude",
            "root": REPO,
            "provider_session_id": CLAUDE_ID,
            "agent_session_id": "",
            "native_identity_status": "unknown",
        },
        "rsme-261010-001906-64375": {
            "agent": "codex",
            "root": REPO,
            "provider_session_id": "9d4e77df-0ae0-4e00-b777-50762271ae92",
            "agent_session_id": "",
            "native_identity_status": "unknown",
        },
        "work-261010-085255-54301": {
            "agent": "claude",
            "root": "/work/wt",
            "agent_session_id": HEADLESS_ID,
            "session_id": HEADLESS_ID,
            "vibecrafted_session_id": "01a12496-15e7-7571-ac2e-b13cc936113e",
        },
    }
    return metas.get(run_id)


def take(
    tmp_path: Path,
    *,
    ownership: FakeOwnership | None = None,
    frame: FakeFrame | None = None,
    meta: Any = run_meta,
) -> dict[str, Any]:
    records = world_records()
    census = [
        r
        for r in records
        if r.argv[0].startswith("/rt/releases/")
        or r.argv[0].startswith("/Applications/")
    ]
    return reinstall_clean.snapshot_world(
        procs=FakeProcs(records, CWDS),
        frame=frame or FakeFrame({"vibecrafted": (LAYOUT, PANES)}),
        ownership=ownership or FakeOwnership(census),
        stores=make_stores(tmp_path),
        socket_dir=SOCKETS,
        config_dir=Path(CONFIG),
        run_meta=meta,
        active_root=NEW,
    )


def terminal_run_meta(run_id: str) -> dict[str, Any] | None:
    """Same world, but the headless run has already settled."""
    meta = run_meta(run_id)
    if meta and run_id.startswith("work-"):
        return {**meta, "status": "completed"}
    return meta


def agent_for(manifest: dict[str, Any], tab: str) -> dict[str, Any]:
    pane = next(
        p
        for s in manifest["frame"]["sessions"]
        for p in s["panes"]
        if p["tab_name"] == tab
    )
    return pane["agent"]


# --------------------------------------------------------------------- recipes


def test_recipe_a_claude_argv_session_id_needs_the_provider_transcript(
    tmp_path: Path,
) -> None:
    manifest = take(tmp_path)
    claude = agent_for(manifest, "claude")
    assert claude["identity"] == PROVEN
    assert claude["native_session_id"] == CLAUDE_ID
    assert claude["recipe"] == "claude-argv-session-id"
    assert claude["evidence"]["transcript"].endswith(f"{CLAUDE_ID}.jsonl")
    # the run meta only *requested* that id; it is reported, never trusted
    assert claude["evidence"]["run_meta"]["verified_session"] is None
    assert claude["evidence"]["run_meta"]["requested_session"] == CLAUDE_ID


def test_recipe_a_requested_id_without_transcript_stays_unknown(tmp_path: Path) -> None:
    stores = make_stores(tmp_path)
    (
        stores.claude_projects
        / reinstall_clean.claude_project_slug(REPO)
        / f"{CLAUDE_ID}.jsonl"
    ).unlink()
    agent = AgentProcess("claude", rec(1, 0, "claude", "--session-id", CLAUDE_ID), REPO)
    reinstall_clean.resolve_native_identities(
        [agent], stores=stores, run_meta=lambda _: None
    )
    assert agent.identity == UNKNOWN
    assert agent.native_session_id == ""
    assert "requested is not acknowledged" in agent.unknown_reason


def test_recipe_b_claude_headless_newest_transcript_and_run_meta(
    tmp_path: Path,
) -> None:
    stores = make_stores(tmp_path)
    project = stores.claude_projects / reinstall_clean.claude_project_slug("/work/solo")
    project.mkdir()
    old = project / "11111111-1111-4111-8111-111111111111.jsonl"
    new = project / "22222222-2222-4222-8222-222222222222.jsonl"
    for path, mtime in ((old, T0 - 1000), (new, T0 + 60)):
        path.write_text("{}\n")
        os.utime(path, (mtime, mtime))
    headless = AgentProcess(
        "claude",
        rec(1, 0, "claude", "-p", "--output-format", "stream-json", born=T0),
        "/work/solo",
    )
    reinstall_clean.resolve_native_identities(
        [headless], stores=stores, run_meta=lambda _: None
    )
    assert headless.identity == PROVEN
    assert headless.recipe == "claude-project-newest"
    assert (
        headless.native_session_id == new.stem
    )  # the stale transcript is not a candidate

    manifest = take(tmp_path / "w")
    worker = manifest["headless_runs"][0]["agent"]
    assert worker["recipe"] == "run-meta"
    assert worker["native_session_id"] == HEADLESS_ID


def test_recipe_c_codex_rollout_birth_and_honest_picker(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    fresh = agent_for(manifest, "codex")
    assert fresh["identity"] == PROVEN
    assert fresh["recipe"] == "codex-rollout-birth"
    assert fresh["native_session_id"] == CODEX_ID
    # the requested id in the run meta never stands in for the rollout
    assert fresh["evidence"]["run_meta"]["requested_session"].startswith("9d4e77df")
    picker = agent_for(manifest, "Shell")
    assert picker["identity"] == UNKNOWN
    assert picker["native_session_id"] is None
    assert picker["evidence"]["codex_mode"] == "picker"


def test_recipe_c_codex_explicit_resume_and_live_rollout(tmp_path: Path) -> None:
    stores = make_stores(tmp_path)
    explicit = AgentProcess(
        "codex", rec(1, 0, "codex", "resume", CODEX_ID, born=T0 + 4000), REPO
    )
    picker = AgentProcess("codex", rec(2, 0, "codex", "resume", born=T0 + 4000), REPO)
    reinstall_clean.resolve_native_identities(
        [explicit, picker], stores=stores, run_meta=lambda _: None, now=T0 + 5100
    )
    assert (
        explicit.recipe == "codex-argv-resume"
        and explicit.native_session_id == CODEX_ID
    )
    # one unproven process, one unclaimed live rollout in its cwd
    assert (
        picker.recipe == "codex-rollout-live"
        and picker.native_session_id == OTHER_CODEX
    )


def test_recipe_c_stale_rollout_never_proves_a_picker(tmp_path: Path) -> None:
    stores = make_stores(tmp_path)
    picker = AgentProcess("codex", rec(2, 0, "codex", "resume", born=T0 + 4000), REPO)
    reinstall_clean.resolve_native_identities(
        [picker], stores=stores, run_meta=lambda _: None, now=T0 + 9000
    )
    assert picker.identity == UNKNOWN  # OTHER_CODEX stopped being written at T0+5001


def test_recipe_d_unknown_never_claims_resume(tmp_path: Path) -> None:
    stores = make_stores(tmp_path)
    twins = [
        AgentProcess("codex", rec(1, 0, "codex", "resume", born=T0 + 4000), REPO),
        AgentProcess("codex", rec(2, 0, "codex", "resume", born=T0 + 4000), REPO),
    ]
    reinstall_clean.resolve_native_identities(
        twins, stores=stores, run_meta=lambda _: None, now=T0 + 5100
    )
    for agent in twins:
        assert agent.identity == UNKNOWN
        assert "share" in agent.unknown_reason
        payload = agent.to_json()
        assert payload["native_session_id"] is None
        command = reinstall_clean.resurrect_command(
            payload, python="py", root=REPO, pack_file="/pack.md"
        )
        assert "--session" not in command
        assert command[command.index("--file") + 1] == "/pack.md"
        assert reinstall_clean._resurrect_mode(payload) == "fresh-with-continuity"


def test_parent_session_is_never_copied_onto_the_child(tmp_path: Path) -> None:
    stores = make_stores(tmp_path)
    child = AgentProcess("claude", rec(1, 0, "claude", born=T0 + 9000), "/elsewhere")
    child.linked_run_id = "rsme-261010-000000-00000"
    meta = {"agent": "claude", "parent_session_id": CLAUDE_ID, "agent_session_id": ""}
    reinstall_clean.resolve_native_identities(
        [child], stores=stores, run_meta=lambda _: meta
    )
    assert child.identity == UNKNOWN
    assert child.native_session_id != CLAUDE_ID


def test_argv_and_meta_disagreement_is_unknown(tmp_path: Path) -> None:
    stores = make_stores(tmp_path)
    agent = AgentProcess("claude", rec(1, 0, "claude", "--session-id", CLAUDE_ID), REPO)
    agent.linked_run_id = "x"
    reinstall_clean.resolve_native_identities(
        [agent], stores=stores, run_meta=lambda _: {"agent_session_id": HEADLESS_ID}
    )
    assert agent.identity == UNKNOWN
    assert agent.evidence["conflict"] is True


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("node", "/usr/local/bin/codex", "resume"), "codex"),
        (("/vendor/aarch64/bin/codex", "exec", "-"), "codex"),
        (("codex", "app-server", "--listen", "unix://"), None),
        (("codex-code-mode-host",), None),
        (("claude", "mcp", "serve"), None),
        (("claude", "--version"), None),
        (
            (
                "/py/python3.14",
                "-m",
                "vibecrafted_core.spawn",
                "interactive-launch",
                "claude",
            ),
            None,
        ),
        (("cursor-agent", "--resume", "x"), "cursor"),
        (("/py/python3.14", "/srv/op/.local/bin/kimi"), "kimi"),
    ],
)
def test_classify_agent(argv: tuple[str, ...], expected: str | None) -> None:
    assert reinstall_clean.classify_agent(argv) == expected


def test_permissions_never_escalate() -> None:
    assert (
        reinstall_clean.infer_permissions(
            "claude", ["--permission-mode", "bypassPermissions"]
        )[0]
        == "bypass"
    )
    assert (
        reinstall_clean.infer_permissions(
            "codex", ["--dangerously-bypass-approvals-and-sandbox"]
        )[0]
        == "bypass"
    )
    assert (
        reinstall_clean.infer_permissions(
            "claude", ["--permission-mode", "acceptEdits"]
        )[0]
        == "accept-edits"
    )
    assert reinstall_clean.infer_permissions("codex", ["--some-new-flag"])[0] == "auto"


# -------------------------------------------------------------------- manifest


def test_manifest_schema_and_placement(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert set(manifest) >= {
        "runtime",
        "frame",
        "headless_runs",
        "ownership",
        "summary",
    }
    assert manifest["runtime"] == {
        "active_root": NEW,
        "running_roots": [OLD],
        "drift": True,
    }
    session = manifest["frame"]["sessions"][0]
    assert session["name"] == "vibecrafted"
    assert session["server"]["pid"] == 100
    assert session["cwd"] == REPO
    assert [p["id"] for p in session["panes"]] == [0, 2, 5, 3, 4]  # plugin pane dropped
    assert manifest["summary"] == {
        "sessions": 1,
        "panes": 5,
        "agents": 4,
        "agents_proven": 3,
        "agents_unknown": 1,
        "headless_runs": 1,
    }
    json.dumps(manifest)  # serializable as-is
    # the Founder's own claude in a foreign terminal is not part of the world
    pids = {
        a["process"]["pid"]
        for s in manifest["frame"]["sessions"]
        for p in s["panes"]
        if (a := p["agent"])
    }
    assert 400 not in pids


def test_manifest_roundtrip_keeps_layouts_as_files(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    run_dir = tmp_path / "run"
    reinstall_clean.store_manifest(run_dir, manifest)
    stored = json.loads((run_dir / "manifest.json").read_text())
    assert "layout" not in stored["frame"]["sessions"][0]
    assert Path(stored["frame"]["sessions"][0]["layout_file"]).read_text() == LAYOUT
    assert (
        reinstall_clean.load_manifest(run_dir)["frame"]["sessions"][0]["layout"]
        == LAYOUT
    )


# ------------------------------------------------------------ census and kill


def test_census_partition_spares_guests_on_our_interpreter() -> None:
    records = world_records()
    census = [
        r for r in records if r.argv[0].startswith(("/rt/releases/", "/Applications/"))
    ]
    split = reinstall_clean.partition_census(
        census,
        roots=(Path("/rt/releases"), Path("/Applications/Vibecrafted.app")),
        config_dir=Path(CONFIG),
    )
    assert [r.pid for r in split["guests"]] == [300]  # cs-bus
    assert 110 in {r.pid for r in split["kill"]}  # chrome script under the config dir
    assert 200 in {r.pid for r in split["kill"]}  # dispatcher module run


def test_kill_plan_lineage_and_caller_tree(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    ownership = manifest["ownership"]
    lineage = {r["pid"] for r in ownership["lineage_agents"]}
    # agents born under the Frame server; never the foreign claude, and never
    # the live headless worker (201 rides under spared dispatcher 200 —
    # decyzja Macieja 2026-10-10)
    assert lineage == {121, 122, 132, 142}
    assert 400 not in lineage
    assert ownership["caller_ancestors"] == [4242]
    assert [r["pid"] for r in ownership["headless_spared"]] == [200]
    assert ownership["headless_spared"][0]["run_id"] == "work-261010-085255-54301"
    assert {200, 201} <= set(ownership["headless_spared_pids"])
    assert 200 not in {r["pid"] for r in ownership["kill"]}
    plan = reinstall_clean.build_plan(
        manifest, source=tmp_path, pack=None, launcher=Path("/bin/vc")
    )
    assert plan["resurrect"]["relaunch_apps"] == ["/Applications/Vibecrafted.app"]
    assert plan["kill"]["spared_guests"][0]["pid"] == 300
    assert plan["kill"]["headless_spared"][0]["pid"] == 200


def test_terminal_headless_dispatcher_is_killed_and_resumed(tmp_path: Path) -> None:
    """A settled run's dispatcher is a zombie: census kill + native resume."""
    manifest = take(tmp_path, meta=terminal_run_meta)
    ownership = manifest["ownership"]
    assert ownership["headless_spared"] == []
    assert 200 in {r["pid"] for r in ownership["kill"]}
    assert 201 in {r["pid"] for r in ownership["lineage_agents"]}
    plan = reinstall_clean.build_plan(
        manifest, source=tmp_path, pack=None, launcher=Path("/bin/vc")
    )
    headless = plan["resurrect"]["headless"][0]
    assert headless["action"] == "resume --run-id"
    assert headless["command"][:5] == [
        "/bin/vc",
        "resume",
        "claude",
        "--run-id",
        "work-261010-085255-54301",
    ]


def test_kill_clean_order_and_zero_leftover(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    records = world_records()
    census = [
        r for r in records if r.argv[0].startswith(("/rt/releases/", "/Applications/"))
    ]
    ownership = FakeOwnership(census, armed=True)
    detail = reinstall_clean.kill_clean(
        manifest, ownership, table={r.pid: r for r in records}, config_dir=Path(CONFIG)
    )
    steps = [c[0] if isinstance(c, tuple) else c for c in ownership.calls]
    assert steps.index("stop-service") < steps.index("terminate")
    labels = [c[1] for c in ownership.calls if isinstance(c, tuple)]
    assert labels == ["lineage agent process", "owned runtime process"]
    assert detail["guests_spared"] == [300]
    assert detail["leftover"] == []


def test_kill_clean_refuses_when_owned_processes_survive(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    records = world_records()
    census = [r for r in records if r.argv[0].startswith("/rt/releases/")]

    class Stubborn(FakeOwnership):
        def terminate(self, records: Sequence[ProcessRecord], *, label: str) -> None:
            self.calls.append(("terminate", label, [r.pid for r in records]))

    detail: dict[str, Any] = {}
    with pytest.raises(reinstall_clean.ReinstallError, match="remain after teardown"):
        reinstall_clean.kill_clean(
            manifest,
            Stubborn(census, armed=True),
            table={r.pid: r for r in records},
            config_dir=Path(CONFIG),
            detail=detail,
        )
    assert detail["service"]["action"] == "stopped"  # progress survives the refusal


def test_no_second_census_and_no_name_based_kill() -> None:
    """Reuse, not fork: every signal goes through the installer's verifier."""
    source = Path(reinstall_clean.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "os.kill" not in calls and "os.killpg" not in calls
    for forbidden in ("pkill", "killall"):
        assert forbidden not in source


def test_installer_still_owns_every_reused_primitive() -> None:
    from vibecrafted_core.doctor import _installer_module

    installer = _installer_module()
    for name in (
        "_darwin_process_ids",
        "_darwin_process_birth",
        "_darwin_process_arguments",
        "_darwin_process_parent_pid",
        "_owned_runtime_process_census",
        "_darwin_caller_ancestor_pids",
        "_owned_runtime_process_roots",
        "_runtime_process_argv_is_owned",
        "_terminate_verified_runtime_processes",
        "_RetiredVcFrameProcess",
        "_runtime_service_has_evidence",
        "_current_tools_link",
        "_tools_install_lease",
        "_inherited_tools_install_lease",
        "_assert_runtime_loaded_service_owner",
        "_runtime_service_snapshot",
        "_run_runtime_service_command",
    ):
        assert hasattr(installer, name), name


def test_installer_ownership_delegates_terminate_with_identity_records() -> None:
    seen: dict[str, Any] = {}

    @dataclass(frozen=True)
    class Retired:
        pid: int
        birth: tuple[str, int, int]
        argv: tuple[str, ...]

    class Installer:
        _RetiredVcFrameProcess = Retired

        def _owned_runtime_process_census(self) -> tuple[()]:
            seen["census"] = True
            return ()

        def _terminate_verified_runtime_processes(
            self, records: Any, *, label: str
        ) -> None:
            seen["records"] = records
            seen["label"] = label

    ownership = reinstall_clean.InstallerOwnership(Installer())
    ownership.census()
    record = rec(7, 1, "/rt/releases/x/bin/vc-server")
    ownership.terminate([record], label="owned runtime process")
    assert seen["census"] is True
    assert seen["records"] == [Retired(7, record.birth, record.argv)]
    assert seen["label"] == "owned runtime process"


# --------------------------------------------------------------------- dry run


def test_dry_run_mutates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = FakeFrame({"vibecrafted": (LAYOUT, PANES)})
    ownership = FakeOwnership(
        [r for r in world_records() if r.argv[0].startswith("/rt/releases/")]
    )
    stores = make_stores(tmp_path)

    def snapshot(*, mode: str, save_sessions: bool) -> dict[str, Any]:
        assert save_sessions is False
        return reinstall_clean.snapshot_world(
            procs=FakeProcs(world_records(), CWDS),
            frame=frame,
            ownership=ownership,
            stores=stores,
            socket_dir=SOCKETS,
            config_dir=Path(CONFIG),
            run_meta=run_meta,
            active_root=NEW,
            mode=mode,
        )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"dry run tried to mutate: {args[:1]}")

    monkeypatch.setattr(reinstall_clean, "take_live_snapshot", snapshot)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(os, "execve", forbidden)
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "Makefile").write_text("install:\n")
    (checkout / "scripts" / "vetcoders_install.py").write_text("")

    rc = reinstall_clean.main(["--clean", "--dry-run", "--source", str(checkout)])

    assert rc == 0
    assert ("save-session", "vibecrafted") not in frame.calls
    assert ownership.calls == ["census"]
    runs = list(reinstall_clean.reinstall_runs_root().glob("*-dry-run-*"))
    assert len(runs) == 1
    receipts = sorted(p.name for p in (runs[0] / "receipts").iterdir())
    assert receipts == ["phase-1-snapshot.json"]  # nothing past the snapshot ran
    plan = json.loads((runs[0] / "plan.json").read_text())
    assert plan["install"]["command"] == ["make", "-C", str(checkout), "install"]
    assert plan["ready"] is True


def test_cli_requires_clean_and_refuses_without_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert reinstall_clean.main([]) == 2
    monkeypatch.setattr(
        reinstall_clean, "take_live_snapshot", lambda **_: take(tmp_path)
    )
    rc = reinstall_clean.main(
        ["--clean", "--dry-run", "--source", str(tmp_path / "nowhere")]
    )
    assert rc == 2  # preflight refusal is a non-zero dry run


def test_plan_names_install_and_native_resume(tmp_path: Path) -> None:
    manifest = take(tmp_path)
    pack = tmp_path / "pack.tar.gz"
    pack.write_bytes(b"x")
    plan = reinstall_clean.build_plan(
        manifest,
        source=tmp_path,
        pack=pack,
        launcher=Path("/bin/vc"),
        run_meta=run_meta,
    )
    assert plan["install"]["command"] == [
        "make",
        "-C",
        str(tmp_path),
        "install",
        f"RUNTIME_PACK={pack.resolve()}",
    ]
    agents = {a["tab"]: a for a in plan["resurrect"]["sessions"][0]["agents"]}
    claude = agents["claude"]["command"]
    assert claude[claude.index("--session") + 1] == CLAUDE_ID
    assert claude[claude.index("--root") + 1] == REPO
    assert agents["Shell"]["mode"] == "fresh-with-continuity"
    headless = plan["resurrect"]["headless"][0]
    # live headless run: spared by kill clean, so nothing to resume
    # (decyzja Macieja 2026-10-10)
    assert headless["action"] == "left-running (live dispatcher spared)"
    assert headless["command"] is None


# ---------------------------------------------------------------- layout rewrite


def launch(key: str, pane_command: str, tab: str) -> frame_layout.PaneLaunch:
    return frame_layout.PaneLaunch(
        key=key,
        pane_command=pane_command,
        tab_name=tab,
        argv=(
            f"{NEW}/bin/python3",
            "-m",
            "vibecrafted_core.spawn",
            "interactive-launch",
            "x",
            "--admission-file",
            f"/adm/{key}.json",
        ),
        cwd=REPO,
    )


@pytest.mark.parametrize("entry_name", ["Start here", "Launchpad"])
def test_rewrite_layout_boots_from_the_new_generation(entry_name: str) -> None:
    out, report = frame_layout.rewrite_layout(
        LAYOUT.replace('tab name="Start here"', f'tab name="{entry_name}"'),
        launches=[
            launch("5", "bash /art/tmp/vc-spawn-cmd.0704", "claude"),
            launch(
                "2",
                "node /usr/local/bin/codex --dangerously-bypass-approvals-and-sandbox resume",
                "Shell",
            ),
        ],
        config_dir=CONFIG,
        old_roots=[OLD],
        new_root=NEW,
    )
    root = frame_layout.parse_layout(out)
    tabs = {t.prop("name"): t for t in root.children if t.name == "tab"}
    commands = {
        name: [p for _, p in frame_layout._command_panes(root) if _ in (tabs[name],)]
        for name in tabs
    }
    start = commands[entry_name][0]
    assert start.prop("command") == f"{CONFIG}/pane-python"
    assert start.child("start_suspended") is None
    claude = commands["claude"][0]
    assert claude.prop("cwd") == REPO
    assert claude.prop("focus") == "true"
    assert frame_layout.pane_signature(claude)[-1] == "/adm/5.json"
    assert claude.child("start_suspended") is None
    shell_pane, vim = commands["Tab #5"]
    assert shell_pane.child("start_suspended") is None  # a login shell is safe
    assert vim.child("start_suspended") is not None  # the Founder decides about vim
    codex_tab = commands["codex"][0]  # no launch for it: stays as recorded
    assert codex_tab.child("start_suspended") is not None
    assert OLD not in out
    assert {r["key"] for r in report if r.get("matched")} == {"5", "2"}


def test_rewrite_appends_a_tab_for_an_unmatched_launch() -> None:
    out, report = frame_layout.rewrite_layout(
        LAYOUT,
        launches=[launch("u0", "", "grok")],
        config_dir=CONFIG,
        old_roots=[],
        new_root=None,
    )
    root = frame_layout.parse_layout(out)
    names = [t.prop("name") for t in root.children if t.name == "tab"]
    assert names[-1] == "grok (resumed)"
    assert root.child("new_tab_template") is not None
    assert {"key": "u0", "appended_tab": True} in report


def test_rewrite_refuses_unknown_shape_and_minimal_layout_parses() -> None:
    with pytest.raises(frame_layout.KdlShapeError):
        frame_layout.parse_layout('layout { pane command="x" { args "y" } }\n}\n')
    text = frame_layout.minimal_layout(REPO, [launch("a", "", "claude")])
    root = frame_layout.parse_layout(text)
    assert [t.prop("name") for t in root.children if t.name == "tab"] == [
        "Shell",
        "claude",
    ]


def test_kdl_strings_roundtrip_quotes_and_braces() -> None:
    tricky = 'echo "{x}" \\ done'
    text = (
        'layout {\n    tab name="t" {\n        pane command="bash" {\n            args "-lc" '
        + frame_layout._q(tricky)
        + "\n        }\n    }\n}\n"
    )
    root = frame_layout.parse_layout(text)
    pane = frame_layout._command_panes(root)[0][1]
    assert frame_layout.pane_signature(pane) == ["bash", "-lc", tricky]


# ------------------------------------------------------------------- resurrect


class Runner:
    def __init__(self, refuse: set[str] | None = None):
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.refuse = refuse or set()

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        if "interactive-command" in argv:
            session = argv[argv.index("--session") + 1] if "--session" in argv else ""
            if session in self.refuse:
                return subprocess.CompletedProcess(
                    argv, 2, "", "native session already has an active executor"
                )
            provider = argv[argv.index("interactive-command") + 1]
            return subprocess.CompletedProcess(
                argv,
                0,
                f"/new/py -m vibecrafted_core.spawn interactive-launch {provider} --admission-file /adm/{provider}-{session or 'fresh'}.json\n",
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")


def ctx_for(
    tmp_path: Path, runner: Runner, *, live: list[str] | None = None
) -> ResurrectContext:
    created: list[str] = list(live or [])

    def live_sessions() -> list[str]:
        for argv, _ in runner.calls:
            if "--create-background" in argv:
                created.append(argv[-1])
        return created

    return ResurrectContext(
        run_dir=tmp_path,
        python="/new/py",
        frame_bin="/new/vc-frame",
        socket_dir=SOCKETS,
        config_dir=Path(CONFIG),
        new_root=NEW,
        launcher=Path("/bin/vc"),
        env={
            "PATH": "/usr/bin",
            "PYTHONPATH": "/old/core",
            "VC_FRAME_PANE_ID": "3",
            "HOME": "/srv/op",
        },
        runner=runner,
        run_meta=run_meta,
        live_sessions=live_sessions,
        wait_seconds=1.0,
    )


def test_resurrect_resumes_proven_and_freshens_unknown(tmp_path: Path) -> None:
    manifest = take(tmp_path / "w")
    runner = Runner()
    report = reinstall_clean.resurrect(manifest, ctx_for(tmp_path, runner))
    session = report["sessions"][0]
    assert session["result"] == "live"
    results = {p["tab"]: p["result"] for p in session["panes"]}
    assert results == {
        "claude": "native-resume",
        "codex": "native-resume",
        "Shell": "fresh-with-continuity",
    }
    admissions = [argv for argv, _ in runner.calls if "interactive-command" in argv]
    sessions = [
        argv[argv.index("--session") + 1] for argv in admissions if "--session" in argv
    ]
    assert sorted(sessions) == sorted([CLAUDE_ID, CODEX_ID])
    create, kwargs = next((a, k) for a, k in runner.calls if "--create-background" in a)
    assert create[:3] == [
        "/new/vc-frame",
        "--layout",
        str(tmp_path / "resurrect-layouts" / "vibecrafted.kdl"),
    ]
    assert create[3:] == ["attach", "--create-background", "vibecrafted"]
    env = kwargs["env"]
    assert "PYTHONPATH" not in env and "VC_FRAME_PANE_ID" not in env
    assert env["VC_FRAME_SOCKET_DIR"] == str(SOCKETS)
    layout = (tmp_path / "resurrect-layouts" / "vibecrafted.kdl").read_text()
    assert f"/adm/claude-{CLAUDE_ID}.json" in layout
    headless = report["headless"][0]
    assert headless["result"] == "resumed"


def test_resurrect_records_a_refused_resume_and_keeps_going(tmp_path: Path) -> None:
    manifest = take(tmp_path / "w")
    runner = Runner(refuse={CLAUDE_ID})
    report = reinstall_clean.resurrect(manifest, ctx_for(tmp_path, runner))
    panes = {p["tab"]: p for p in report["sessions"][0]["panes"]}
    assert panes["claude"]["result"] == "refused"
    assert "active executor" in panes["claude"]["reason"]
    assert panes["codex"]["result"] == "native-resume"


def test_resurrect_never_clobbers_a_live_session(tmp_path: Path) -> None:
    manifest = take(tmp_path / "w")
    runner = Runner()
    report = reinstall_clean.resurrect(
        manifest, ctx_for(tmp_path, runner, live=["vibecrafted"])
    )
    assert report["sessions"][0]["result"] == "already-live"
    assert not [
        a
        for a, _ in runner.calls
        if "--create-background" in a or "interactive-command" in a
    ]


def test_scrubbed_env_drops_pane_and_run_identity() -> None:
    env = reinstall_clean.scrubbed_env(
        {
            "PATH": "/bin",
            "NO_COLOR": "1",
            "VC_FRAME_PANE_ID": "2",
            "ZELLIJ": "0",
            "VIBECRAFTED_RUN_ID": "work-x",
            "VIBECRAFTED_RUNTIME_ROOT": NEW,
            "CLAUDE_CODE_SESSION_ID": CLAUDE_ID,
            "VC_FRAME_SERVER_IDLE_EXIT_SECS": "30",
        }
    )
    assert env == {"PATH": "/bin", "VIBECRAFTED_RUNTIME_ROOT": NEW}
    assert reinstall_clean.without_python_path({"PYTHONPATH": "x", "A": "b"}) == {
        "A": "b"
    }


def test_resolve_source_requires_a_vibecrafted_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "vc"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "Makefile").write_text("install:\n")
    (checkout / "scripts" / "vetcoders_install.py").write_text("")
    nested = checkout / "docs" / "deep"
    nested.mkdir(parents=True)
    assert reinstall_clean.resolve_source(None, nested) == checkout
    assert reinstall_clean.resolve_source(str(checkout), tmp_path) == checkout
    assert reinstall_clean.resolve_source(str(tmp_path / "missing"), checkout) is None
