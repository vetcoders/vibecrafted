import stat
from pathlib import Path

import pytest
from vibecrafted_core import workflow


def spec(tmp_path, **payload):
    return workflow.normalize_launch_spec(
        {"agent": "codex", "skill": "workflow", "root": str(tmp_path), **payload},
        tmp_path,
    )


@pytest.mark.parametrize("runtime", ["cloud-vm", "local-vm", "typo"])
def test_unsupported_runtime_refuses(tmp_path, runtime):
    with pytest.raises(ValueError, match="runtime"):
        spec(tmp_path, prompt="task", runtime=runtime)


@pytest.mark.parametrize(
    "field", ["null", "", "[]", "42", "true", "{}", "one\nmodel: two"]
)
def test_invalid_plan_model_refuses(tmp_path, field):
    plan = tmp_path / "plan.md"
    plan.write_text(f"---\nagent: codex\nmodel: {field}\n---\ntask\n")
    with pytest.raises(ValueError, match="frontmatter.*model|model.*frontmatter"):
        spec(tmp_path, file=str(plan))


@pytest.mark.parametrize("source", ["file", "prompt"])
def test_plan_model_preserves_exact_document(tmp_path, source):
    body = '\ufeff---\r\nagent: codex\r\nmodel: "gpt-6-astra"\r\n---\r\n Żółć "quotes"\r\n\r\n'
    plan = tmp_path / "plan.md"
    plan.write_bytes(body.encode())
    selected = spec(tmp_path, **{source: str(plan) if source == "file" else body})
    assert selected.model == "gpt-6-astra"
    assert selected.model_source == "plan_frontmatter"
    assert workflow._source_prompt(selected) == body
    override = spec(
        tmp_path,
        model="exact-provider-model",
        **{source: str(plan) if source == "file" else body},
    )
    assert override.model == "exact-provider-model"
    assert override.model_source == "cli"
    assert plan.read_bytes() == body.encode()


def test_provider_conflict_refuses_even_with_cli_model(tmp_path):
    with pytest.raises(ValueError, match="agent.*conflict|conflict.*agent"):
        spec(
            tmp_path,
            prompt="---\nagent: claude\nmodel: opus\n---\ntask",
            model="gpt-6-astra",
        )


def test_inline_whitespace_preserved(tmp_path):
    body = '  Żółć\n"hello" $(false)\n\n'
    assert spec(tmp_path, prompt=body).prompt == body


def test_private_prompt_no_overwrite_or_symlink(tmp_path):
    target = tmp_path / "run" / "prompt.md"
    body = "\ufeffone\r\n\n"
    workflow._write_prompt_file(target, body)
    assert target.read_bytes() == body.encode()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        workflow._write_prompt_file(target, "replacement")
    link = tmp_path / "symlink"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        workflow._write_prompt_file(link, "replacement")
    assert target.read_bytes() == body.encode()


def test_pinned_base_and_dirty_parent(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "-b", "trunk")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "tracked").write_text("first")
    git("add", "tracked")
    git("commit", "-m", "first")
    first = git("rev-parse", "HEAD")
    git("branch", "release")
    git("tag", "release")
    with pytest.raises(ValueError, match="ambiguous"):
        spec(tmp_path, prompt="task", base="release", worktree=True)
    selected = spec(tmp_path, prompt="task", base="refs/tags/release", worktree=True)
    assert selected.baseline_sha == first
    (tmp_path / "tracked").write_text("second")
    git("commit", "-am", "second")
    with pytest.raises(ValueError, match="Living Tree"):
        spec(tmp_path, prompt="task", base=first)
    (tmp_path / "tracked").write_text("dirty")
    (tmp_path / "untracked").write_text("private")
    index = (tmp_path / ".git" / "index").read_bytes()
    prepared, receipt = workflow._prepare_launch_worktree(selected, "work-pinned-test")
    assert Path(prepared.root, "tracked").read_text() == "first"
    assert not Path(prepared.root, "untracked").exists()
    assert (tmp_path / "tracked").read_text() == "dirty"
    assert (tmp_path / ".git" / "index").read_bytes() == index
    assert receipt["worktree_baseline_sha"] == first


@pytest.mark.parametrize(
    "model,plan,expected,origin",
    [
        ("new-cli", "---\nmodel: new-plan\n---\ntask", "new-cli", "cli"),
        ("", "---\nmodel: new-plan\n---\ntask", "new-plan", "plan_frontmatter"),
        ("", "continuation note", "old-effective", "resume_previous"),
    ],
)
def test_resume_new_selection_and_immutable_parent(
    monkeypatch, tmp_path, model, plan, expected, origin
):
    parent = {
        "run_id": "work-260909-010101-12345",
        "agent": "claude",
        "state": "stopped",
        "root": str(tmp_path),
        "agent_model": "old-effective",
        "model_requested": "old-requested",
        "agent_session_id": "11111111-2222-4333-8444-555555555555",
    }
    captured = {}
    monkeypatch.setattr(workflow, "lookup_run", lambda _: dict(parent))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_: dict(parent))
    monkeypatch.setattr(workflow, "_worker_process_alive", lambda _: False)
    monkeypatch.setattr(workflow, "_operator_continue_prompt", lambda *a, **kw: plan)
    monkeypatch.setattr(
        workflow,
        "manual_resume_session",
        lambda *a, **kw: captured.update(kw) or {"accepted": True},
    )
    result = workflow.operator_continue_run(
        parent["run_id"], tmp_path, model=model, plan_text=plan
    )
    assert result["accepted"]
    assert captured["model"] == expected
    assert captured["model_source"] == origin
    assert captured["launch_meta"]["parent_run_id"] == parent["run_id"]
    assert parent["agent_model"] == "old-effective"


def test_identity_catalog_bare_remote_refresh_and_provenance(tmp_path, monkeypatch):
    import json
    import subprocess

    def git(root, *args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "unusual")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "tracked").write_text("one")
    git(source, "add", ".")
    git(source, "commit", "-m", "one")
    remote = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(remote)],
        check=True,
        capture_output=True,
    )
    config_root = tmp_path / "config"
    config = config_root / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[repositories."team/project"]\nremote = ' + json.dumps(str(remote)) + "\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    payload = {
        "root": "",
        "repo": "team/project",
        "repo_selector": True,
        "worktree": True,
        "prompt": "task",
    }
    first = spec(tmp_path, **payload)
    assert first.repo_kind == "identity"
    assert first.resolved_ref == "refs/remotes/origin/unusual"
    assert first.baseline_sha == git(source, "rev-parse", "HEAD")
    git(source, "checkout", "-b", "next")
    (source / "tracked").write_text("two")
    git(source, "commit", "-am", "two")
    git(source, "push", str(remote), "next")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/next")
    second = spec(tmp_path, **payload)
    assert second.resolved_ref == "refs/remotes/origin/next"
    assert second.baseline_sha != first.baseline_sha
    # A commit that exists only in the cache must not masquerade as source truth.
    tree = git(Path(first.root), "rev-parse", "HEAD^{tree}")
    orphan = git(
        Path(first.root),
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit-tree",
        tree,
        "-m",
        "cache-only",
    )
    with pytest.raises(ValueError, match="not reachable"):
        spec(tmp_path, **payload, base=orphan)
    assert (
        spec(tmp_path, **payload, base=first.baseline_sha).baseline_sha
        == first.baseline_sha
    )
    git(Path(first.root), "remote", "set-url", "origin", str(source))
    with pytest.raises(ValueError, match="provenance"):
        spec(tmp_path, **payload)


def test_public_repo_does_not_use_build_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Git repository"):
        spec(tmp_path, repo_selector=True, root="", prompt="task")


def test_changed_plan_refuses_after_model_selection(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("---\nmodel: exact\n---\ntask")
    selected = spec(tmp_path, file=str(plan))
    plan.write_text("---\nmodel: different\n---\ntask")
    with pytest.raises(ValueError, match="changed after admission"):
        workflow._source_prompt(selected)


def test_deck_inline_transport_uses_stdin_and_no_prompt_argv(tmp_path):
    import json
    import subprocess
    import sys

    deck = Path(__file__).resolve().parents[2] / "scripts" / "vibecrafted"
    text = deck.read_text()
    function = (
        "cmd_core_workflow_skill() {"
        + text.split("cmd_core_workflow_skill() {", 1)[1].split("\ncmd_init()", 1)[0]
    )
    capture = tmp_path / "capture.json"
    spy = tmp_path / "python-spy"
    spy.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(capture)!r}).write_text(json.dumps([sys.argv[1:],sys.stdin.read()]))\n"
    )
    spy.chmod(0o700)
    body = '\ufeff---\r\nmodel: exact\r\n---\r\n Żółć "quoted" $(false)\n\n'
    script = (
        function
        + '\n_dispatcher_core_dir() { printf "%s" "$1"; }\n_dispatcher_core_dir() { printf /source; }\n_vibecrafted_python() { printf "%s" "$SPY"; }\n_script_repo_root() { printf /source; }\ncmd_core_workflow_skill workflow codex --model exact --prompt "$1" --runtime headless\n'
    )
    import os

    result = subprocess.run(
        args=["bash", "-c", script, "fixture", body],
        check=False,
        env={**os.environ, "SPY": str(spy)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    argv, transported = json.loads(capture.read_text())
    assert body not in argv
    assert "--prompt" not in argv
    assert "--prompt-stdin" in argv
    assert transported == body
    assert body not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "override,expected,origin",
    [
        ("", "from-plan", "plan_frontmatter"),
        ('model = "from-toml"\n', "from-toml", "cli"),
    ],
)
def test_dispatch_plan_model_selection(tmp_path, override, expected, origin):
    import json

    from vibecrafted_core.dispatch.schema import parse_dispatch

    plan = tmp_path / "plan.md"
    plan.write_text("---\nagent: codex\nmodel: from-plan\n---\ntask")
    source = (
        Path(__file__).parent / "dispatch" / "fixtures" / "minimal.dispatch.toml"
    ).read_text()
    source = source.replace(
        'prompt = "Implement parser cut {id} in {repo}."',
        override + "brief = " + json.dumps(str(plan)),
    )
    cut = parse_dispatch(source).cuts[0]
    assert cut.model == expected
    assert cut.model_source == origin


def test_agy_private_transport_refuses_before_mutation(tmp_path, monkeypatch):
    selected = spec(tmp_path, agent="agy", prompt="private")
    monkeypatch.setattr(
        workflow, "_sweep_stale_runs", lambda: pytest.fail("mutated before refusal")
    )
    with pytest.raises(ValueError, match="private prompt transport"):
        workflow.launch_workflow(selected, tmp_path)


def test_deck_global_flags_do_not_consume_prompt_bytes():
    import subprocess

    deck = Path(__file__).resolve().parents[2] / "scripts" / "vibecrafted"
    text = deck.read_text()
    main = "main() {" + text.split("main() {", 1)[1].rsplit('main "$@"', 1)[0]
    script = (
        main
        + '\nrun_skill() { printf "%s\\n" "$@"; }\nmain workflow codex --prompt --verbose\n'
    )
    result = subprocess.run(
        args=["bash", "-c", script, "vibecrafted"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["workflow", "codex", "--prompt", "--verbose"]


@pytest.mark.parametrize("identity", ["--run-id", "--session"])
def test_public_resume_model_and_private_input_reach_core(tmp_path, identity):
    import json
    import os
    import subprocess
    import sys

    shell = Path(__file__).resolve().parents[1] / "vibecrafted_core/runtime/shell/lib"
    spy = tmp_path / "spy.py"
    capture = tmp_path / "capture.json"
    spy.write_text(
        "import json,sys\n"
        + f"open({str(capture)!r},'w').write(json.dumps([sys.argv[1:],sys.stdin.read()]))\n"
    )
    script = (
        f'source "{shell}/prompts.sh"\nsource "{shell}/marbles.sh"\n'
        + "_vetcoders_normalize_declared_contract_root() { :; }\n"
        + "_vetcoders_looks_like_run_id() { return 1; }\n"
        + '_vetcoders_run_core_cli() { "$TEST_PYTHON" "$SPY" "$@"; }\n'
        + '_vetcoders_resume_agent claude "$1" identity --prompt "$2" --model exact-opus\n'
    )
    body = '  Zażółć\n"quoted" $(false)\n\n'
    result = subprocess.run(
        ["bash", "-c", script, "fixture", identity, body],
        env={**os.environ, "TEST_PYTHON": sys.executable, "SPY": str(spy)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    argv, transported = json.loads(capture.read_text())
    assert argv[argv.index("--model") + 1] == "exact-opus"
    assert "--prompt-stdin" in argv
    assert body not in argv
    assert transported == body
    assert body not in result.stdout + result.stderr


def test_explicit_false_execution_conflict(tmp_path):
    with pytest.raises(ValueError, match="conflict"):
        spec(tmp_path, prompt="task", worktree=False, runtime_class="local-worktrees")


def test_core_stdin_preserves_crlf(monkeypatch, tmp_path):
    import io
    import sys

    from vibecrafted_core import cli

    body = "\ufeff---\r\nagent: codex\r\nmodel: exact\r\n---\r\nZażółć\r\n\r\n"
    selected = {}
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(body.encode()), encoding="utf-8")
    )
    monkeypatch.setattr(
        cli,
        "normalize_launch_spec",
        lambda payload, _: selected.update(payload) or object(),
    )
    monkeypatch.setattr(cli, "launch_workflow", lambda *_: {"accepted": True})
    cli.main(["workflow", "codex", "--repo", str(tmp_path), "--prompt-stdin", "--json"])
    assert selected["prompt"].encode() == body.encode()
    assert selected["worktree"] in (None, "")


def test_dispatch_freezes_source_with_model(tmp_path):
    import json

    from vibecrafted_core.dispatch.schema import parse_dispatch, render_cell_prompt

    path = tmp_path / "brief.md"
    original = "---\r\nagent: codex\r\nmodel: original\r\n---\r\n  Żółć\r\n\r\n"
    path.write_bytes(original.encode())
    source = (
        Path(__file__).parent / "dispatch/fixtures/minimal.dispatch.toml"
    ).read_text()
    source = source.replace(
        'prompt = "Implement parser cut {id} in {repo}."',
        "brief = " + json.dumps(str(path)),
    )
    dispatch = parse_dispatch(source)
    path.write_text("---\nmodel: changed\n---\nchanged task")
    rendered = render_cell_prompt(dispatch, dispatch.cuts[0])
    assert original in rendered
    assert "changed task" not in rendered
    assert dispatch.cuts[0].model == "original"


@pytest.mark.parametrize("providers", [["codex", "claude"], "codex"])
def test_research_model_has_provider_role(tmp_path, providers):
    selected = spec(
        tmp_path,
        skill="research",
        agent=providers,
        prompt="---\nagent: codex\nmodel: exact-codex\n---\ntask",
    )
    assert selected.agent == "swarm"
    assert selected.research_model_agent == "codex"
    assert selected.model == "exact-codex"


@pytest.mark.parametrize("skill", ["init", "partner", "operator", "resume"])
@pytest.mark.parametrize("input_kind", ["prompt", "file"])
def test_interactive_admission_snapshot_and_private_command(
    tmp_path, monkeypatch, skill, input_kind
):
    import hashlib
    import json
    import subprocess

    from vibecrafted_core import spawn

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in [
        ("init", "-b", "trunk"),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "base",
        ),
    ]:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    monkeypatch.setattr(
        spawn,
        "resolve_provider_usage_capability",
        lambda *_: spawn.ProviderUsageCapability("codex", False),
    )
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", "work-parent")
    body = '\ufeff---\r\nagent: codex\r\nmodel: exact-codex\r\n---\r\n  Żółć "quoted"\r\n\r\n'
    plan = tmp_path / "plan.md"
    plan.write_bytes(body.encode())
    cmd = spawn.interactive_workspace_command(
        "codex",
        body if input_kind == "prompt" else "",
        "local-native",
        "bypass",
        repo,
        token_budget="unmetered",
        skill=skill,
        source_file=str(plan) if input_kind == "file" else "",
    )
    assert body not in " ".join(cmd)
    assert "--prompt" not in cmd
    admission_path = Path(cmd[cmd.index("--admission-file") + 1])
    admission = json.loads(admission_path.read_bytes())
    assert admission["run_id"] != "work-parent"
    assert admission["parent_run_id"] == "work-parent"
    assert admission["skill"] == skill
    assert admission["model_requested"] == "exact-codex"
    assert admission["model_source"] == "plan_frontmatter"
    snapshot = Path(admission["source_snapshot"])
    assert snapshot.parent.name == admission["run_id"]
    assert snapshot.read_bytes() == body.encode()
    assert admission["source_digest"] == hashlib.sha256(body.encode()).hexdigest()
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert stat.S_IMODE(admission_path.stat().st_mode) == 0o600
    assert body not in admission_path.read_text()


@pytest.mark.parametrize("skill", ["init", "partner", "operator", "resume"])
@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("provider_exit", [0, 7])
def test_public_interactive_shell_pty_admission(
    tmp_path, monkeypatch, skill, attached, provider_exit
):
    import json
    import os
    import pty
    import shlex
    import subprocess
    import sys

    from vibecrafted_core import control_plane

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "base",
        ],
        check=True,
    )
    core = Path(__file__).resolve().parents[1]
    lib = core / "vibecrafted_core/runtime/shell/lib"
    capture = tmp_path / "provider.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    provider = bin_dir / "codex"
    provider.write_text(
        f"#!{sys.executable}\n"
        + """import json,os,sys
from pathlib import Path
Path(os.environ['PROVIDER_CAPTURE']).write_text(json.dumps({'argv': sys.argv, 'run_id': os.environ['VIBECRAFTED_RUN_ID'], 'tty': [os.isatty(i) for i in (0,1,2)]}))
print('fixture-provider-completed', flush=True)
raise SystemExit(int(os.environ['PROVIDER_EXIT']))
"""
    )
    provider.chmod(0o700)
    body = '\ufeff---\r\nagent: codex\r\nmodel: exact-codex\r\n---\r\n Żółć "quoted"\r\n\r\n'
    plan = tmp_path / "plan.md"
    plan.write_bytes(body.encode())
    script = "\n".join(
        "source " + shlex.quote(str(path))
        for path in [
            lib.parent.parent / "helpers/vetcoders-runtime-core.sh",
            lib / "prompts.sh",
            lib / "vc_frame.sh",
            lib / "operator.sh",
            lib / "operator_entrypoints.sh",
            lib / "marbles.sh",
        ]
    )
    script += '\n_vetcoders_core_python_spec() { printf "%s\\t%s\\n" "$FIXTURE_PYTHON" "$FIXTURE_CORE"; }\n'
    script += """
_vetcoders_needs_vc_terminal_entry() { return 1; }
_vetcoders_vc_frame_bin() { printf /fixture-frame; }
_vetcoders_require_vc_frame() { return 0; }
_vetcoders_operator_face_tab() { printf fixture; }
_vetcoders_launch_interactive_declaration() { _vetcoders_exec_admitted_interactive "$4"; }
_vetcoders_ensure_canonical_workspace_identity() { return 0; }
_vetcoders_prepare_operator_runtime() { export VIBECRAFTED_OPERATOR_SESSION=fixture; }
_vetcoders_spawn_into_operator_session() { _vetcoders_exec_admitted_interactive "$2"; }
_vetcoders_attach_prepared_vc_frame_session() { return 0; }
"""
    if skill == "resume":
        script += '_vetcoders_resume_agent codex --session 11111111-2222-4333-8444-555555555555 --repo "$FIXTURE_REPO" --model exact-codex --token-budget unmetered\n'
    else:
        script += f'_vetcoders_skill_{skill} codex --repo "$FIXTURE_REPO" --file "$FIXTURE_PLAN" --token-budget unmetered\n'
    env = {
        **os.environ,
        "FIXTURE_PYTHON": sys.executable,
        "FIXTURE_CORE": str(core),
        "FIXTURE_REPO": str(repo),
        "FIXTURE_PLAN": str(plan),
        "PROVIDER_CAPTURE": str(capture),
        "PROVIDER_EXIT": str(provider_exit),
        "VIBECRAFTED_RUN_ID": "work-parent",
        "VIBECRAFTED_RUNTIME_BIN": str(bin_dir),
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
    }
    env.pop("PYTHONPATH", None)
    if attached:
        env.update(VC_FRAME="1", VC_FRAME_SESSION_NAME="fixture", VC_FRAME_PANE_ID="7")
    parent_paths = [
        control_plane.control_plane_home() / "runtime_runs/work-parent/meta.json",
        control_plane.control_plane_home() / "runs/work-parent.json",
    ]
    parent_bytes = b'{"run_id":"work-parent","agent":"claude","state":"active","sentinel":"parent-owned"}'
    for parent_path in parent_paths:
        parent_path.parent.mkdir(parents=True, exist_ok=True)
        parent_path.write_bytes(parent_bytes)
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["bash", "-c", script], env=env, stdin=slave, stdout=slave, stderr=slave
        )
        os.close(slave)
        slave = -1
        # Drain the real PTY so no child can block behind its output buffer.
        import select

        output = bytearray()
        import time

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                output.extend(data)
            if proc.poll() is not None:
                break
        assert (proc.wait(timeout=2) == 0) == (provider_exit == 0), output.decode(
            errors="replace"
        )
        assert all(
            parent_path.read_bytes() == parent_bytes for parent_path in parent_paths
        )
        result = json.loads(capture.read_text())
        assert result["tty"] == [True, True, True]
        assert result["run_id"] != "work-parent"
        assert body not in " ".join(result["argv"])
        assert result["argv"][result["argv"].index("-m") + 1] == "exact-codex"
        run_dir = control_plane.control_plane_home() / "runtime_runs" / result["run_id"]
        meta = json.loads((run_dir / "meta.json").read_text())
        assert meta["skill"] == skill
        assert meta["status"] == ("completed" if provider_exit == 0 else "failed")
        assert "fixture-provider-completed" in Path(meta["transcript"]).read_text()
        assert body not in Path(meta["transcript"]).read_text()
        projection = json.loads(
            (
                control_plane.control_plane_home()
                / "runs"
                / (result["run_id"] + ".json")
            ).read_text()
        )
        assert projection["agent"] == "codex"
        assert projection["state"] == ("completed" if provider_exit == 0 else "failed")
        if skill == "resume":
            assert "resume" in result["argv"]
            assert "11111111-2222-4333-8444-555555555555" in result["argv"]
    finally:
        if "proc" in locals() and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)
        if slave >= 0:
            os.close(slave)
        os.close(master)


def test_prompt_redaction_across_every_boundary():
    from vibecrafted_core.runtime_transcript import PrivatePromptFilter

    secret = "Zażółć\r\nsecret".encode()
    for split in range(len(secret) + 1):
        redactor = PrivatePromptFilter(secret)
        result = (
            redactor.feed(b"before " + secret[:split])
            + redactor.feed(secret[split:] + b" after")
            + redactor.finish()
        )
        assert result == b"before [private launch input redacted] after"


def test_event_author_cannot_replace_admitted_provider(tmp_path, monkeypatch):
    import json

    from vibecrafted_core import control_plane as cp

    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(cp, "event_stream_path", lambda: events)
    events.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "run_id": "work-provider",
                    "kind": "launch",
                    "payload": {
                        "agent": "claude",
                        "root": str(tmp_path),
                        "model_requested": "claude-exact",
                        "model_effective": "claude-exact",
                        "model_source": "plan_frontmatter",
                        "baseline_sha": "a" * 40,
                        "presentation": "visible",
                    },
                },
                {
                    "run_id": "work-provider",
                    "kind": "lifecycle:completed",
                    "payload": {"agent": "guardian"},
                },
            ]
        )
    )
    result = cp._merge_event_stream({})["work-provider"]
    assert result.agent == "claude"
    assert result.extra["model_effective"] == "claude-exact"
    assert result.extra["model_source"] == "plan_frontmatter"
    assert result.extra["baseline_sha"] == "a" * 40
