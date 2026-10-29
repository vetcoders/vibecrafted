"""Opt-in Docker acceptance of the real container preparation and inbox owner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core import dev_container, message_control, package_resources, spawn


@pytest.mark.skipif(
    os.environ.get("VC_TEST_LOCAL_CONTAINER") != "1",
    reason="requires an isolated Linux ARM64 Docker image",
)
@pytest.mark.parametrize("source_kind", ["checkout", "installed"])
def test_local_container_preparation_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_kind: str
) -> None:
    docker = shutil.which("docker")
    assert docker
    image = os.environ["VC_TEST_CONTAINER_IMAGE"]
    context = os.environ.get("DOCKER_CONTEXT", "")
    env = dict(os.environ)
    if context:
        env["DOCKER_CONTEXT"] = context
    start = subprocess.run(
        [docker, "run", "-d", "--rm", "--entrypoint", "sleep", image, "600"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    cid = start.stdout.strip()
    relay = None
    try:
        root = tmp_path / "project"
        root.mkdir()
        home = tmp_path / "host-home"
        monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
        env["VIBECRAFTED_HOME"] = str(home)
        if source_kind == "installed":
            # A Runtime Pack contains the package and its source receipt, but
            # no checkout or wheel build project at its root.
            source = package_resources.package_root()
            generation = tmp_path / "installed-generation"
            package = generation / "vibecrafted-core" / "vibecrafted_core"
            package.parent.mkdir(parents=True)
            shutil.copytree(source, package, symlinks=True)
            revision = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            version = (package / "VERSION").read_text().strip().split("+", 1)[0]
            (package / "VERSION").write_text(f"{version}+g{revision[:8]}\n")
            (generation / "source-provenance.json").write_text(
                json.dumps({"source_revision": revision}), encoding="utf-8"
            )
            monkeypatch.setattr(package_resources, "package_root", lambda: package)
        local_tree = tmp_path / "overrides" / "versions" / "fixture" / "tree"
        selected = local_tree / "skills" / "vc-init" / "SKILL.md"
        selected.parent.mkdir(parents=True)
        selected.write_text("selected local skill\n", encoding="utf-8")
        (local_tree / "skills" / "DELEGATION_MATRIX.md").write_text(
            "local companion version\n", encoding="utf-8"
        )
        (local_tree / "runtime").mkdir()
        (local_tree / "runtime" / "companion.txt").write_text("local runtime\n")
        monkeypatch.setattr(spawn, "canonical_skill_file", lambda _: selected)
        target = dev_container.ContainerTarget(
            cli=docker,
            project="synthetic",
            container_id=cid,
            container_name=cid,
            image=image,
            recipe_digest="fixture",
            host_root=str(root),
            engine_context=context,
        )
        monkeypatch.setattr(dev_container, "ensure_container", lambda *_a, **_k: target)
        run_id = "init-synthetic-container-proof"
        prompt = spawn.compose_interactive_task_prompt(
            skill="init",
            source="",
            root=str(root),
            resume_block="",
            admission={"run_id": run_id},
        )
        launch = spawn.prepare_interactive_workspace_launch(
            provider="claude",
            runtime="local-vm",
            permissions="bypass",
            selected_root=root,
            prompt=prompt,
            run_id=run_id,
            executable=docker,
            publish=False,
            quota_policy=spawn.resolve_quota_policy("unmetered", runtime="local-vm"),
            usage_capability=spawn.container_usage_capability("claude"),
        )
        script = """set -eu
vibecrafted capabilities --json >/dev/null
vibecrafted message --help >/dev/null
vibecrafted init --help >/dev/null
id=$(vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --receive | python3 -c 'import json,sys; rows=json.load(sys.stdin); assert len(rows)==1; print(rows[0]["message_id"])')
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --ack "$id" >/dev/null
printf 'synthetic-receive-ack-ok\\n'
"""
        result = spawn._prepare_container_launch(
            launch,
            provider="claude",
            permissions="bypass",
            command=["sh", "-c", script],
            child_env=env,
            continuity_material=spawn.ContinuityMaterial(
                spawn.ContinuityPolicy("fresh", "fresh:synthetic"), prompt
            ),
        )
        assert not isinstance(result, int)
        argv, _, relay = result
        runtime = launch.receipt["container_runtime"]
        if source_kind == "installed":
            assert runtime["source_revision"] == revision
            assert runtime["source_dirty"] is None
        guest_skill = runtime["skill_paths"][str(selected)]
        staged = subprocess.run(
            [docker, "exec", cid, "cat", launch.receipt["container_prompt"]],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        ).stdout
        assert guest_skill in staged and str(selected) not in staged
        subprocess.run(
            [docker, "exec", cid, "test", "-r", guest_skill], env=env, check=True
        )
        skill_text = subprocess.run(
            [docker, "exec", cid, "cat", guest_skill],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        ).stdout
        assert skill_text == "selected local skill\n"
        arch = subprocess.run(
            [docker, "exec", cid, "uname", "-m"],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        ).stdout.strip()
        assert arch in {"aarch64", "arm64"}
        companion = subprocess.run(
            [
                docker,
                "exec",
                cid,
                "cat",
                str(Path(guest_skill).parent.parent / "DELEGATION_MATRIX.md"),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        assert companion.stdout == "local companion version\n"
        message = message_control.send_message(
            run_id=run_id, text="synthetic container proof"
        )
        argv[argv.index("-it")] = "-i"
        proof = subprocess.run(
            argv, env=env, text=True, capture_output=True, timeout=90, check=False
        )
        assert proof.returncode == 0, proof.stderr
        assert "synthetic-receive-ack-ok" in proof.stdout
        assert (
            message_control.inspect_message(message["message_id"])["delivery_state"]
            == "agent_acknowledged"
        )
        assert not relay.failure
        print(
            json.dumps(
                {
                    "arch": arch,
                    "container": cid,
                    "runtime": runtime,
                    "message_acknowledged": True,
                }
            )
        )
    finally:
        if relay is not None:
            relay.close()
        subprocess.run(
            [docker, "rm", "-f", cid], env=env, capture_output=True, check=False
        )
