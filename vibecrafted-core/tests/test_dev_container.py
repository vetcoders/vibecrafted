"""Local container environment: recipe identity, preparation and refusals.

A fake container CLI stands in for Docker so every branch (missing engine,
stopped daemon, missing image, foreign mount, unshared path, restart loop) is
exercised without touching the host engine. The real engine run is recorded in
the worker report, not simulated here.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from vibecrafted_core import dev_container

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_DOCKER_STATE"]
with open(state_path) as handle:
    state = json.load(handle)
argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps({"argv": argv, "env": {k: os.environ.get(k, "") for k in (
        "VC_WORKSPACE_DIR", "VC_DEV_IMAGE", "VC_DEV_SKIP_SYNC")}}) + "\n")

def save():
    with open(state_path, "w") as handle:
        json.dump(state, handle)

def shared(path):
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in state["vm_shares"])

def listing(path):
    return "\n".join(sorted(os.listdir(path))) if shared(path) and os.path.isdir(path) else ""

def container(cid):
    for project, record in state["containers"].items():
        if record["id"] == cid:
            return project, record
    return None, None

if argv[:2] == ["context", "show"]:
    print(state.get("context", "colima-ci")); sys.exit(0)
if argv[:1] == ["info"]:
    if not state.get("daemon", True):
        print("Cannot connect to the Docker daemon", file=sys.stderr); sys.exit(1)
    print("29.5.2"); sys.exit(0)
if argv[:2] == ["compose", "version"]:
    print("2.30.0"); sys.exit(0)
if argv[:2] == ["image", "inspect"]:
    tag = argv[-1]
    if tag not in state["images"]:
        sys.exit(1)
    print(json.dumps(state["images"][tag])); sys.exit(0)
if argv[:1] == ["build"]:
    tag = argv[argv.index("-t") + 1]
    labels = {}
    for index, item in enumerate(argv):
        if item == "--label":
            key, value = argv[index + 1].split("=", 1)
            labels[key] = value
    print("Step 1/1 : FROM debian:trixie-slim")
    if state.get("build_fails"):
        print("E: network down"); sys.exit(7)
    state["images"][tag] = labels; save(); print("Successfully tagged " + tag); sys.exit(0)
if argv[:1] == ["ps"]:
    project = [a.rsplit("=", 1)[1] for a in argv if a.startswith("label=com.docker.compose.project=")][0]
    record = state["containers"].get(project)
    if record:
        print(record["id"])
    sys.exit(0)
if argv[:1] == ["inspect"]:
    cid = argv[-1]
    project, record = container(cid)
    if record is None:
        sys.exit(1)
    status = record.get("status", "running")
    started = record.get("started", "2026-10-10T05:00:00Z")
    if record.get("looping"):
        record["restarts"] = record.get("restarts", 0) + 1
        started = "2026-10-10T05:00:%02dZ" % (record["restarts"] % 60)
        save()
    st = {"Status": status, "Running": status == "running", "Restarting": False,
          "StartedAt": started, "ExitCode": 1 if record.get("looping") else 0}
    if "--format" in argv:
        print(json.dumps(st)); sys.exit(0)
    print(json.dumps([{"Id": cid, "Name": "/" + project + "-dev-1", "State": st,
        "Config": {"Image": record["image"]},
        "Mounts": [{"Destination": "/workspace", "Source": record["mount"]}]}])); sys.exit(0)
if argv[:1] == ["run"]:
    volume = argv[argv.index("-v") + 1]
    print(listing(volume.split(":")[0])); sys.exit(0)
if argv[:1] == ["compose"] and "up" in argv:
    project = argv[argv.index("-p") + 1]
    state["containers"][project] = {"id": "cid-" + project, "image": os.environ["VC_DEV_IMAGE"],
        "mount": os.environ["VC_WORKSPACE_DIR"], "status": "running",
        "looping": state.get("loop_after_up", False)}
    save(); print(" Container " + project + "-dev-1 Started"); sys.exit(0)
if argv[:1] == ["exec"]:
    rest = argv[1:]
    stdin_mode = rest and rest[0] == "-i"
    if stdin_mode:
        rest = rest[1:]
    cid = rest[0]
    project, record = container(cid)
    if rest[1:3] == ["ls", "-A"]:
        print(listing(record["mount"])); sys.exit(0)
    if stdin_mode and rest[1:3] == ["sh", "-c"]:
        state.setdefault("staged", {})[rest[-1]] = sys.stdin.read(); save(); sys.exit(0)
    if rest[1:3] == ["sh", "-c"] and "basename" in rest[3]:
        print("\n".join(state.get("live_providers", []))); sys.exit(0)
    if rest[1:3] == ["sh", "-c"] and "kill -TERM" in rest[3]:
        run = rest[5].rsplit("/", 1)[-1]
        if run in state.get("live_providers", []):
            state["live_providers"].remove(run); save(); print("terminated")
        else:
            print("exited")
        sys.exit(0)
    sys.exit(0)
if argv[:1] == ["logs"]:
    print("entry: aicx --version failed"); sys.exit(0)
print("unexpected " + " ".join(argv), file=sys.stderr)
sys.exit(64)
"""


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_container, "_engine_cache", {})
    monkeypatch.setattr(dev_container, "_image_cache", {})
    monkeypatch.setattr(dev_container, "_RUNNING_TIMEOUT_SECONDS", 4.0)
    monkeypatch.setattr(dev_container.time, "sleep", lambda _seconds: None)


class FakeEngine:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        cli = self.bin / "docker"
        cli.write_text(
            FAKE_DOCKER.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1),
            encoding="utf-8",
        )
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        self.state_path = tmp_path / "docker-state.json"
        self.log_path = tmp_path / "docker-log.jsonl"
        self.state: dict = {
            "images": {},
            "containers": {},
            "vm_shares": [str(tmp_path)],
            "context": "colima-ci",
        }
        self.save()
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(tmp_path),
            "FAKE_DOCKER_STATE": str(self.state_path),
            "FAKE_DOCKER_LOG": str(self.log_path),
        }

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def load(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def calls(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]


def _project(tmp_path: Path, name: str = "Smoke Project") -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "README.md").write_text("smoke\n", encoding="utf-8")
    return root


def test_recipe_ships_inside_core_and_installs_every_declared_provider() -> None:
    recipe = dev_container.recipe_dir()
    assert recipe == dev_container.RECIPE_DIR
    assert recipe.is_relative_to(Path(dev_container.__file__).resolve().parent)
    dockerfile = (recipe / "Dockerfile.dev").read_text(encoding="utf-8")
    installs = {
        "claude": "@anthropic-ai/claude-code@",
        "codex": "@openai/codex@",
        "kimi": "kimi-code/install.sh",
    }
    assert set(installs) == set(dev_container.CONTAINER_PROVIDERS)
    for provider, marker in installs.items():
        assert marker in dockerfile, provider
        assert f"{dev_container.CONTAINER_PROVIDERS[provider]} --version" in dockerfile
    # Every file the build context COPYs is part of the recipe identity.
    copied = set(re.findall(r"^COPY (\S+) ", dockerfile, flags=re.MULTILINE))
    assert copied <= set(dev_container.RECIPE_FILES)


def test_compose_never_defaults_the_mount_and_keeps_history_volumes() -> None:
    compose = (dev_container.RECIPE_DIR / "compose.dev.yaml").read_text(
        encoding="utf-8"
    )
    assert "${VC_WORKSPACE_DIR:?" in compose
    assert "${VC_WORKSPACE_DIR:-" not in compose
    assert "image: ${VC_DEV_IMAGE:-" in compose
    for volume in ("aicx:/root/.aicx", "claude:/root/.claude", "codex:/root/.codex"):
        assert volume in compose
    # The host HOME is never a credential shortcut.
    assert "${HOME}" not in compose and "~/" not in compose.split("services:", 1)[1]


def test_entrypoint_probe_cannot_crash_a_persistent_container() -> None:
    entry = (dev_container.RECIPE_DIR / "entry.sh").read_text(encoding="utf-8")
    probe = re.search(r'version="\$\("\$tool" --version[^\n]*', entry)
    assert probe is not None
    assert probe.group(0).rstrip().endswith('|| true)"')


def test_recipe_digest_is_content_identity(tmp_path: Path) -> None:
    copy = tmp_path / "recipe"
    copy.mkdir()
    for name in dev_container.RECIPE_FILES:
        (copy / name).write_bytes((dev_container.RECIPE_DIR / name).read_bytes())
    original = dev_container.recipe_digest(copy)
    assert original == dev_container.recipe_digest()
    assert dev_container.image_tag(original) == f"vibecrafted-dev:recipe-{original}"
    (copy / "entry.sh").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    assert dev_container.recipe_digest(copy) != original


def test_missing_cli_and_stopped_daemon_give_concrete_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev_container, "_EXTRA_CLI_DIRS", ())
    empty = {"PATH": str(tmp_path / "nowhere")}
    status = dev_container.engine_status(empty)
    assert not status.available
    assert "brew install colima" in status.reason

    engine = FakeEngine(tmp_path)
    engine.state["daemon"] = False
    engine.save()
    status = dev_container.engine_status(engine.env, refresh=True)
    assert not status.available
    assert "colima start --profile ci" in status.reason
    capability = dev_container.container_capability("codex", engine.env)
    assert capability["available"] is False
    assert "colima start --profile ci" in capability["reason"]


def test_capability_is_per_provider_not_global(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path)
    codex = dev_container.container_capability("codex", engine.env)
    agy = dev_container.container_capability("agy", engine.env)
    assert codex["available"] is True
    assert codex["image_ready"] is False
    assert agy["available"] is False
    assert "agy is not installed in the local container recipe" in agy["reason"]


def test_missing_image_builds_by_recipe_then_starts_the_project_container(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    lines: list[str] = []

    target = dev_container.ensure_container(root, env=engine.env, log=lines.append)

    digest = dev_container.recipe_digest()
    tag = dev_container.image_tag(digest)
    assert target.image == tag
    assert target.recipe_digest == digest
    assert target.project == "vc-smoke-project"
    assert target.host_root == str(root.resolve())
    calls = [entry["argv"] for entry in engine.calls()]
    build = next(argv for argv in calls if argv[0] == "build")
    assert f"{dev_container.RECIPE_LABEL}={digest}" in build
    assert build[build.index("-t") + 1] == tag
    up = next(
        entry
        for entry in engine.calls()
        if entry["argv"][0] == "compose" and "up" in entry["argv"]
    )
    assert up["argv"][-5:] == ["up", "-d", "--no-build", "--pull", "never"]
    assert up["env"] == {
        "VC_WORKSPACE_DIR": str(root.resolve()),
        "VC_DEV_IMAGE": tag,
        "VC_DEV_SKIP_SYNC": "1",
    }
    # Stage and progress are visible, and history is never wiped.
    assert lines[0].startswith("[1/3] Image " + tag)
    assert any(line.startswith("[3/3] ") for line in lines)
    flat = [" ".join(argv) for argv in calls]
    assert not any(
        "down" in item or "volume rm" in item or " -v" == item[-3:] for item in flat
    )


def test_ready_image_and_running_container_are_reused_without_rebuild(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    dev_container.ensure_container(root, env=engine.env, log=lambda _line: None)
    before = len(engine.calls())

    target = dev_container.ensure_container(
        root, env=engine.env, log=lambda _line: None
    )

    later = [entry["argv"] for entry in engine.calls()[before:]]
    assert not any(argv[0] == "build" for argv in later)
    assert not any(argv[0] == "compose" and "up" in argv for argv in later)
    assert target.container_id == "cid-vc-smoke-project"


def test_stopped_container_on_an_older_recipe_is_upgraded_with_volumes_kept(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    engine.state["images"]["vibecrafted-dev:debian13"] = {}
    engine.state["containers"]["vc-smoke-project"] = {
        "id": "cid-old",
        "image": "vibecrafted-dev:debian13",
        "mount": str(root.resolve()),
        "status": "exited",
    }
    engine.save()

    target = dev_container.ensure_container(
        root, env=engine.env, log=lambda _line: None
    )

    argv = [entry["argv"] for entry in engine.calls()]
    assert any(item[0] == "build" for item in argv)
    assert any(item[0] == "compose" and "up" in item for item in argv)
    assert not any("down" in item or "rm" in item for item in argv)
    assert target.image == dev_container.image_tag(dev_container.recipe_digest())


def test_running_container_on_another_recipe_is_kept_not_recreated(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    engine.state["images"]["vibecrafted-dev:recipe-older"] = {
        dev_container.RECIPE_LABEL: "older"
    }
    running = {
        "id": "cid-running-owned",
        "image": "vibecrafted-dev:recipe-older",
        "mount": str(root.resolve()),
        "status": "running",
    }
    engine.state["containers"]["vc-smoke-project"] = dict(running)
    engine.save()

    with pytest.raises(dev_container.ContainerError) as raised:
        dev_container.ensure_container(root, env=engine.env, log=lambda _line: None)

    # Agents working inside are never ended by an upgrade: no build, no
    # compose up/recreate, the running container stays exactly as it was.
    assert raised.value.stage == "drift"
    message = str(raised.value)
    assert "recipe older" in message and dev_container.recipe_digest() in message
    assert "stop dev" in message and "history volumes are kept" in message
    argv = [entry["argv"] for entry in engine.calls()]
    assert not any(item[0] == "build" for item in argv)
    assert not any(item[0] == "compose" and "up" in item for item in argv)
    assert engine.load()["containers"]["vc-smoke-project"] == running


def test_running_container_with_the_same_recipe_under_another_tag_is_reused(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    digest = dev_container.recipe_digest()
    engine.state["images"]["vibecrafted-dev:retagged"] = {
        dev_container.RECIPE_LABEL: digest
    }
    engine.state["containers"]["vc-smoke-project"] = {
        "id": "cid-same-recipe",
        "image": "vibecrafted-dev:retagged",
        "mount": str(root.resolve()),
        "status": "running",
    }
    engine.save()
    lines: list[str] = []

    target = dev_container.ensure_container(root, env=engine.env, log=lines.append)

    argv = [entry["argv"] for entry in engine.calls()]
    assert not any(item[0] == "build" for item in argv)
    assert not any(item[0] == "compose" and "up" in item for item in argv)
    assert target.container_id == "cid-same-recipe"
    # The receipt names the image actually running, with its real recipe.
    assert target.image == "vibecrafted-dev:retagged"
    assert any(f"recipe {digest}" in line for line in lines)


def test_foreign_mount_never_reuses_another_projects_container(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    other = _project(tmp_path / "elsewhere", "Smoke Project")
    engine.state["containers"]["vc-smoke-project"] = {
        "id": "cid-other",
        "image": "x",
        "mount": str(other.resolve()),
        "status": "running",
    }
    engine.save()

    target = dev_container.ensure_container(
        root, env=engine.env, log=lambda _line: None
    )

    assert target.project == dev_container.project_candidates(root)[1]
    assert engine.load()["containers"]["vc-smoke-project"]["id"] == "cid-other"


def test_unshared_path_is_refused_before_any_container_mounts_it(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    engine.state["vm_shares"] = ["/srv/unshared-elsewhere"]
    engine.save()

    with pytest.raises(dev_container.ContainerError) as raised:
        dev_container.ensure_container(root, env=engine.env, log=lambda _line: None)

    assert raised.value.stage == "mount"
    assert "colima start --profile ci --mount" in str(raised.value)
    assert not any(
        entry["argv"][0] == "compose" and "up" in entry["argv"]
        for entry in engine.calls()
    )


def test_restart_loop_is_reported_with_logs_not_as_ready(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path)
    root = _project(tmp_path)
    engine.state["loop_after_up"] = True
    engine.save()

    with pytest.raises(dev_container.ContainerError) as raised:
        dev_container.ensure_container(root, env=engine.env, log=lambda _line: None)

    assert raised.value.stage == "start"
    assert "does not stay running" in str(raised.value)
    assert "aicx --version failed" in str(raised.value)


def test_failed_build_keeps_the_exact_cause(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path)
    engine.state["build_fails"] = True
    engine.save()

    with pytest.raises(dev_container.ContainerError) as raised:
        dev_container.ensure_container(
            _project(tmp_path), env=engine.env, log=lambda _l: None
        )

    assert raised.value.stage == "build"
    assert "exit 7" in str(raised.value) and "network down" in str(raised.value)


def test_exec_passes_credentials_by_name_and_stages_private_files(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(tmp_path)
    target = dev_container.ensure_container(
        _project(tmp_path), env=engine.env, log=lambda _line: None
    )
    secret_env = {"OPENAI_API_KEY": "sk-secret-value", "ANTHROPIC_API_KEY": ""}

    names = dev_container.credential_names("codex", secret_env)
    argv = dev_container.exec_argv(
        target,
        ["codex", "--dangerously-bypass-approvals-and-sandbox", "go"],
        run_id="init-1",
        pass_names=names,
        extra_env={"VIBECRAFTED_RUN_ID": "init-1"},
    )

    assert names == ("OPENAI_API_KEY",)
    assert "sk-secret-value" not in " ".join(argv)
    assert argv[:5] == [target.cli, "exec", "-it", "-w", "/workspace"]
    assert "COLORTERM" in argv and "OPENAI_API_KEY" in argv
    wrapper = argv[argv.index(target.container_id) + 1 :]
    # The provider records its in-container identity, then becomes that process.
    assert wrapper[:2] == ["sh", "-c"]
    assert wrapper[2] == dev_container._RECORD_SCRIPT
    assert "start:%s boot:%s" in wrapper[2] and wrapper[2].endswith('exec "$@"')
    assert wrapper[3:5] == ["sh", "/root/.vibecrafted/agent-runs/init-1"]
    assert wrapper[5:] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "go"]

    staged = dev_container.stage_text(
        target, "init-1", "prompt.md", "private task", engine.env
    )
    assert staged == "/root/.vibecrafted/agent-runs/init-1/prompt.md"
    assert engine.load()["staged"]["prompt.md"] == "private task"
    with pytest.raises(dev_container.ContainerError):
        dev_container.stage_text(target, "../escape", "prompt.md", "x", engine.env)


def test_teardown_ends_the_provider_group_left_by_a_closed_tab(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path)
    target = dev_container.ensure_container(
        _project(tmp_path), env=engine.env, log=lambda _line: None
    )

    outcome = dev_container.terminate_provider(
        target, "init-1", engine.env, grace_seconds=1
    )

    teardown = [
        e["argv"]
        for e in engine.calls()
        if e["argv"][:2] == ["exec", target.container_id]
    ]
    assert teardown, engine.calls()
    script = teardown[-1][4]
    assert script == dev_container._TEARDOWN_SCRIPT
    assert teardown[-1][-3:] == ["/root/.vibecrafted/agent-runs/init-1", "1", "/proc"]
    assert outcome == "exited"
    with pytest.raises(dev_container.ContainerError):
        dev_container.exec_argv(target, ["codex"], run_id="../x")


def test_sweep_ends_only_providers_whose_host_owner_is_gone(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path)
    target = dev_container.ensure_container(
        _project(tmp_path), env=engine.env, log=lambda _line: None
    )
    state = engine.load()
    state["live_providers"] = ["init-closed-tab", "init-still-open"]
    engine.state = state
    engine.save()

    ended = dev_container.sweep_orphan_providers(
        target, is_live=lambda run_id: run_id == "init-still-open", env=engine.env
    )

    assert ended == ["init-closed-tab"]
    assert engine.load()["live_providers"] == ["init-still-open"]


def test_signal_teardown_runs_in_its_own_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = FakeEngine(tmp_path)
    target = dev_container.ensure_container(
        _project(tmp_path), env=engine.env, log=lambda _line: None
    )
    started: list[dict] = []

    class Recorder:
        def __init__(self, argv, **kwargs) -> None:
            started.append({"argv": argv, **kwargs})

    monkeypatch.setattr(dev_container.subprocess, "Popen", Recorder)

    assert dev_container.terminate_provider_detached(target, "init-9", engine.env)

    # A closed tab kills the owner's process group; this teardown is not in it.
    assert started[0]["start_new_session"] is True
    assert started[0]["argv"][5] == dev_container._TEARDOWN_SCRIPT
    assert started[0]["argv"][-3:] == [
        "/root/.vibecrafted/agent-runs/init-9",
        "3",
        "/proc",
    ]


# --- identity-bound teardown against real disposable processes ------------

PASSTHROUGH = r"""#!/usr/bin/env python3
import os, sys
argv = sys.argv[1:]
assert argv[0] == "exec", argv
rest = argv[1:]
if rest and rest[0] == "-i":
    rest = rest[1:]
cid, shell, flag, script, name, *args = rest
assert (shell, flag, name) == ("sh", "-c", "sh"), rest
staging, proc = os.environ["FAKE_STAGING"], os.environ["FAKE_PROC"]
mapped = []
for arg in args:
    if arg.startswith("/root/.vibecrafted/agent-runs"):
        arg = staging + arg[len("/root/.vibecrafted/agent-runs"):]
    elif arg == "/proc":
        arg = proc
    mapped.append(arg)
os.execvp("sh", ["sh", "-c", script, "sh", *mapped])
"""


class IdentityWorld:
    """Local stand-in for a container: real processes, a fake /proc view."""

    def __init__(self, tmp_path: Path) -> None:
        self.staging = tmp_path / "staging"
        self.proc = tmp_path / "proc"
        (self.proc / "sys" / "kernel" / "random").mkdir(parents=True)
        self.boot("boot-A")
        cli = tmp_path / "bin" / "docker"
        cli.parent.mkdir()
        cli.write_text(
            PASSTHROUGH.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1),
            encoding="utf-8",
        )
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        self.env = {
            "PATH": f"{cli.parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "FAKE_STAGING": str(self.staging),
            "FAKE_PROC": str(self.proc),
        }
        self.target = dev_container.ContainerTarget(
            cli=str(cli),
            project="vc-identity",
            container_id="cid-identity",
            container_name="vc-identity-dev-1",
            image="vibecrafted-dev:recipe-test",
            recipe_digest="test",
            host_root=str(tmp_path),
        )
        self.processes: list[subprocess.Popen] = []

    def boot(self, boot_id: str) -> None:
        (self.proc / "sys" / "kernel" / "random" / "boot_id").write_text(
            boot_id + "\n", encoding="utf-8"
        )

    def spawn(self, ignore_term: bool = False) -> subprocess.Popen:
        argv = (
            ["sh", "-c", "trap '' TERM; while :; do sleep 0.2; done"]
            if ignore_term
            else ["sleep", "300"]
        )
        process = subprocess.Popen(argv, start_new_session=True)
        self.processes.append(process)
        return process

    def kernel_view(self, pid: int, starttime: str) -> None:
        folder = self.proc / str(pid)
        folder.mkdir(exist_ok=True)
        fields = ["S", *(["0"] * 18), starttime, *(["0"] * 10)]
        (folder / "stat").write_text(f"{pid} (fake proc) {' '.join(fields)}\n")

    def record(
        self, run_id: str, pid: int, starttime: str, boot_id: str = "boot-A"
    ) -> Path:
        run_dir = self.staging / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / dev_container.IDENTITY_FILE
        path.write_text(f"{pid} start:{starttime} boot:{boot_id}\n", encoding="utf-8")
        return path

    def wait_gone(self, path: Path, timeout: float = 8.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if not path.exists():
                return True
            time.sleep(0.05)
        return False

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


@pytest.fixture
def world(tmp_path: Path):
    instance = IdentityWorld(tmp_path)
    yield instance
    instance.close()


def _alive(process: subprocess.Popen, settle: float = 0.3) -> bool:
    time.sleep(settle)
    return process.poll() is None


def test_stale_record_never_reaches_a_distinct_live_process(
    world: IdentityWorld,
) -> None:
    bystander = world.spawn()
    world.kernel_view(bystander.pid, "222")  # the live process the PID names now

    record = world.record("init-old", bystander.pid, "111")
    assert (
        dev_container.terminate_provider(world.target, "init-old", world.env) == "stale"
    )
    assert not record.exists() and _alive(bystander)

    record = world.record("init-old", bystander.pid, "111")
    assert dev_container.terminate_provider_detached(
        world.target, "init-old", world.env
    )
    assert world.wait_gone(record) and _alive(bystander)

    record = world.record("init-old", bystander.pid, "111")
    ended = dev_container.sweep_orphan_providers(
        world.target, is_live=lambda _run: False, env=world.env
    )
    assert ended == [] and not record.exists() and _alive(bystander)


def test_container_restart_reused_pid_keeps_boot_id_but_not_starttime(
    world: IdentityWorld,
) -> None:
    # `docker restart` keeps the VM kernel (same boot id) and starts PIDs over:
    # the recorded PID now names a different live process with a new starttime.
    reused = world.spawn()
    world.kernel_view(reused.pid, "1200")
    record = world.record("init-before-restart", reused.pid, "300", boot_id="boot-A")

    assert (
        dev_container.sweep_orphan_providers(
            world.target, is_live=lambda _run: False, env=world.env
        )
        == []
    )
    assert not record.exists() and _alive(reused)
    record = world.record("init-before-restart", reused.pid, "300", boot_id="boot-A")
    assert (
        dev_container.terminate_provider(world.target, "init-before-restart", world.env)
        == "stale"
    )
    record = world.record("init-before-restart", reused.pid, "300", boot_id="boot-A")
    assert dev_container.terminate_provider_detached(
        world.target, "init-before-restart", world.env
    )
    assert world.wait_gone(record) and _alive(reused)


def test_kernel_reboot_invalidates_recorded_identity(world: IdentityWorld) -> None:
    # A Docker VM reboot changes the boot id; starttime values may repeat.
    bystander = world.spawn()
    world.kernel_view(bystander.pid, "333")
    world.record("init-before-reboot", bystander.pid, "333", boot_id="boot-A")
    world.boot("boot-B")

    ended = dev_container.sweep_orphan_providers(
        world.target, is_live=lambda _run: False, env=world.env
    )
    outcome = dev_container.terminate_provider(
        world.target, "init-before-reboot", world.env
    )

    assert ended == [] and outcome == "none" and _alive(bystander)


def test_legitimate_orphan_is_ended_by_the_sweep(world: IdentityWorld) -> None:
    orphan = world.spawn()
    world.kernel_view(orphan.pid, "444")
    record = world.record("init-closed-tab", orphan.pid, "444")
    owner_alive = {"init-closed-tab": False}

    ended = dev_container.sweep_orphan_providers(
        world.target, is_live=lambda run: owner_alive[run], env=world.env
    )

    assert ended == ["init-closed-tab"]
    assert orphan.wait(timeout=5) is not None
    assert not record.exists()


def test_live_owner_keeps_its_provider_through_the_sweep(world: IdentityWorld) -> None:
    provider = world.spawn()
    world.kernel_view(provider.pid, "555")
    record = world.record("init-open-tab", provider.pid, "555")

    assert (
        dev_container.sweep_orphan_providers(
            world.target, is_live=lambda _run: True, env=world.env
        )
        == []
    )
    assert record.exists() and _alive(provider)


def test_no_kill_escalation_once_the_original_identity_disappears(
    world: IdentityWorld,
) -> None:
    stubborn = world.spawn(ignore_term=True)
    world.kernel_view(stubborn.pid, "666")
    world.record("init-stubborn", stubborn.pid, "666")
    outcome: list[str] = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            dev_container.terminate_provider(
                world.target, "init-stubborn", world.env, grace_seconds=4
            )
        )
    )
    worker.start()
    time.sleep(1.2)
    world.kernel_view(stubborn.pid, "777")  # the PID now names something else
    worker.join(timeout=15)

    assert outcome == ["stale"]
    assert _alive(stubborn)


def test_escalation_kills_only_while_identity_still_holds(world: IdentityWorld) -> None:
    stubborn = world.spawn(ignore_term=True)
    world.kernel_view(stubborn.pid, "888")
    world.record("init-ignores-term", stubborn.pid, "888")

    outcome = dev_container.terminate_provider(
        world.target, "init-ignores-term", world.env, grace_seconds=1
    )

    assert outcome == "killed"
    assert stubborn.wait(timeout=5) is not None
