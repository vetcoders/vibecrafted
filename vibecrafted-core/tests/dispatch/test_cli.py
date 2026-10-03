from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core import cli as root_cli
from vibecrafted_core.dispatch import cli as dispatch_cli
from vibecrafted_core.dispatch import supervisor as supervisor_module
from vibecrafted_core.dispatch.supervisor import CellRun

pytestmark = pytest.mark.usefixtures("worker_claims")


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "agents@vetcoders.io"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "codex"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/vetcoders/fixture.git"],
        cwd=path,
        check=True,
    )


def _dispatch_file(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    reports = tmp_path / "reports"
    tracker = tmp_path / "plans" / "tracker.md"
    dispatch_file = tmp_path / "dispatch.toml"
    dispatch_file.write_text(
        f"""
schema = "vibecrafted.dispatch.v1"

[meta]
name = "cli-test"
repo = "{repo}"
reports_dir = "{reports}"
tracker = "{tracker}"

[policy]
repair_rounds = 0

[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "hello {{baton}}"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
""",
        encoding="utf-8",
    )
    return dispatch_file, reports, tracker


def test_cli_doctor_accepts_valid_dispatch(tmp_path: Path, capsys) -> None:
    dispatch_file, _, _ = _dispatch_file(tmp_path)

    assert dispatch_cli.main([str(dispatch_file), "--doctor"]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "dispatch-doctor: ok"


@pytest.mark.parametrize("mode", [["--doctor"], []])
def test_red_baseline_refuses_before_dispatch(
    tmp_path: Path, capsys, monkeypatch, mode: list[str]
) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    plan.write_text(
        plan.read_text().replace(
            'run = "echo ok"', 'run = "echo broken-baseline; exit 7"'
        )
    )

    def forbidden_launch(*args, **kwargs):
        pytest.fail("red baseline must refuse before the supervisor launches")

    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden_launch)
    assert dispatch_cli.main([str(plan), *mode, "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "echo broken-baseline; exit 7" in payload["errors"][0]["message"]
    assert "broken-baseline" in payload["errors"][0]["message"]


@pytest.mark.parametrize("expected", ["seed", "dirty"])
def test_baseline_verification_uses_clean_detached_head(
    tmp_path: Path, capsys, expected: str
) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    repo = tmp_path / "repo"
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "README.md").write_text("dirty\n")
    (repo / "untracked.txt").write_text("preserve me\n")
    command = "cat README.md; echo mutation > README.md; touch baseline-only.txt"
    plan.write_text(
        plan.read_text()
        .replace('run = "echo ok"', f"run = {json.dumps(command)}")
        .replace('contains = "ok"', f'contains = "{expected}"')
    )

    assert dispatch_cli.main([str(plan), "--doctor", "--json"]) == (
        0 if expected == "seed" else 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["baseline"]["head"] == head
    assert payload["baseline"]["verification"]["ok"] is (expected == "seed")
    assert (repo / "README.md").read_text() == "dirty\n"
    assert (repo / "untracked.txt").read_text() == "preserve me\n"
    assert not (repo / "baseline-only.txt").exists()
    worktrees = subprocess.check_output(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, text=True
    )
    assert worktrees.count("worktree ") == 1


@pytest.mark.parametrize("second_expect", ['contains = "ok"', 'not_contains = "ok"'])
def test_baseline_deduplicates_commands_and_keeps_every_matcher(
    tmp_path: Path, capsys, second_expect: str
) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    marker = tmp_path / "calls.txt"
    command = f"echo call >> {shlex.quote(str(marker))}; echo ok"
    plan.write_text(
        plan.read_text().replace('run = "echo ok"', f"run = {json.dumps(command)}")
        + f"""
[[cuts]]
id = "c2"
agent = "codex"
workflow = "implement"
prompt = "second"
  [[cuts.verify]]
  run = {json.dumps(command)}
  expect = {{ {second_expect} }}
"""
    )

    assert dispatch_cli.main([str(plan), "--doctor", "--json"]) == (
        1 if "not_contains" in second_expect else 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["baseline"]["verification"]["checks"]) == 1
    assert marker.read_text() == "call\n"


def test_doctor_override_keeps_red_evidence(tmp_path: Path, capsys) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    plan.write_text(
        plan.read_text().replace('run = "echo ok"', 'run = "echo red; exit 7"')
    )
    assert (
        dispatch_cli.main([str(plan), "--doctor", "--allow-red-baseline", "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["baseline"]["allow_red_baseline"] is True
    assert payload["baseline"]["verification"]["ok"] is False
    assert payload["baseline"]["verification"]["checks"][0]["exit_code"] == 7
    assert "--allow-red-baseline" in payload["warnings"][0]["message"]


def test_duplicate_command_retains_implicit_zero_exit(tmp_path: Path, capsys) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    plan.write_text(
        plan.read_text().replace('run = "echo ok"', 'run = "echo ok; exit 7"')
        + """
[[cuts]]
id = "negative-probe"
agent = "codex"
workflow = "implement"
prompt = "negative probe"
  [[cuts.verify]]
  run = "echo ok; exit 7"
  expect = { exit_code = 7 }
"""
    )
    assert dispatch_cli.main([str(plan), "--doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["baseline"]["verification"]["checks"]) == 1
    assert payload["baseline"]["verification"]["ok"] is False


def test_baseline_timeout_refuses_and_removes_checkout(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    plan.write_text(
        plan.read_text().replace('run = "echo ok"', 'run = "sleep 0.1; echo ok"')
    )
    real_executor = dispatch_cli.run_verifies

    def short_timeout(*args, **kwargs):
        return real_executor(*args, **kwargs, timeout_s=0.01)

    monkeypatch.setattr(dispatch_cli, "run_verifies", short_timeout)
    assert dispatch_cli.main([str(plan), "--doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["baseline"]["verification"]["checks"][0]["matcher_result"] == "timeout"
    )
    worktrees = subprocess.check_output(
        ["git", "worktree", "list", "--porcelain"], cwd=tmp_path / "repo", text=True
    )
    assert worktrees.count("worktree ") == 1


def test_baseline_commands_each_start_pristine(tmp_path: Path, capsys) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    plan.write_text(
        plan.read_text().replace(
            'run = "echo ok"',
            'run = "echo dirty > README.md; touch leftover.txt; echo ok"',
        )
        + """
[[cuts]]
id = "c2"
agent = "codex"
workflow = "implement"
prompt = "second"
  [[cuts.verify]]
  run = "test ! -e leftover.txt && cat README.md"
  expect = { equals = "seed" }
"""
    )
    assert dispatch_cli.main([str(plan), "--doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["baseline"]["verification"]["checks"]) == 2


def test_red_baseline_override_cannot_bypass_isolation_failure(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    plan, _, _ = _dispatch_file(tmp_path)

    def no_checkout(*args, **kwargs):
        raise RuntimeError("isolation unavailable")

    monkeypatch.setattr(dispatch_cli, "WorktreeManager", no_checkout)
    assert (
        dispatch_cli.main([str(plan), "--doctor", "--allow-red-baseline", "--json"])
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"][0]["path"] == "baseline"


def test_dry_run_does_not_execute_red_baseline(tmp_path: Path, capsys) -> None:
    plan, _, _ = _dispatch_file(tmp_path)
    marker = tmp_path / "ran.txt"
    command = f"touch {shlex.quote(str(marker))}; exit 7"
    plan.write_text(
        plan.read_text().replace('run = "echo ok"', f"run = {json.dumps(command)}")
    )
    assert dispatch_cli.main([str(plan), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not marker.exists()


def test_cli_dry_run_renders_prompts_and_machine_result(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    dispatch_file, _reports, _ = _dispatch_file(tmp_path)
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))

    assert dispatch_cli.main([str(dispatch_file), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    dry_run_dir = home / "artifacts" / "vetcoders" / "fixture"
    dry_run_dir = next(dry_run_dir.iterdir()) / "plans" / "dry-run"
    assert payload["dry_run"] is True
    assert payload["prompts"]["c1"] == str(dry_run_dir / "prompts" / "c1.md")
    assert (dry_run_dir / "validated-dispatch.toml").is_file()
    assert "baseline_head" in (dry_run_dir / "tracker.md").read_text(encoding="utf-8")
    assert '"last": null' in (dry_run_dir / "prompts" / "c1.md").read_text(
        encoding="utf-8"
    )


def test_cli_doctor_rejects_conductor_invalid_fixture(capsys) -> None:
    fixture = Path(__file__).parent / "fixtures" / "invalid-no-verify.dispatch.toml"

    assert dispatch_cli.main([str(fixture), "--doctor"]) == 1

    captured = capsys.readouterr()
    assert "cuts[0].verify" in captured.out


def test_root_cli_dispatch_subcommand_routes_to_dispatch_cli(
    tmp_path: Path, capsys
) -> None:
    dispatch_file, _, _ = _dispatch_file(tmp_path)

    assert root_cli.main(["dispatch", str(dispatch_file), "--doctor"]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "dispatch-doctor: ok"


@pytest.mark.parametrize("command", ["echo red", "echo ok; exit 7", "echo ok"])
def test_public_dispatch_contract_controls_next_worktree(
    tmp_path: Path, capsys, monkeypatch, command: str
) -> None:
    dispatch_file, _, _ = _dispatch_file(tmp_path)
    text = (
        dispatch_file.read_text()
        .replace(
            "repair_rounds = 0",
            "repair_rounds = 0\nawait = { poll_s = 0.02, timeout_min = 1 }",
        )
        .replace('run = "echo ok"', f'run = "{command}"')
    )
    dispatch_file.write_text(
        text
        + """
[[cuts]]
id = "c2"
agent = "codex"
workflow = "implement"
prompt = "next cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { contains = "ok" }
"""
    )
    launches: list[str] = []

    def stub_launcher_factory(*args, **kwargs):
        def launch(cut, prompt, kind):
            launches.append(cut.id)
            report = Path(cut.artifact_path) / "stub-report.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                ["bash", "-c", f"printf 'worker done' > {shlex.quote(str(report))}"],
                cwd=cut.runtime_root,
            )
            return CellRun(
                cut_id=cut.id,
                kind=kind,
                accepted=True,
                run_id=f"stub-{cut.id}",
                report_path=str(report),
                proc=process,
            )

        return launch

    # Only the provider is substituted: CLI validation, isolated worktree
    # admission, process await, verifiers, scheduler, receipts and exit are real.
    monkeypatch.setattr(
        supervisor_module, "workflow_cell_launcher", stub_launcher_factory
    )
    expected_green = command == "echo ok"
    # These negative cases exercise worker-time failures after explicit
    # baseline admission; ordinary red-baseline refusal is covered above.
    override = [] if expected_green else ["--allow-red-baseline"]
    assert root_cli.main(["dispatch", str(dispatch_file), "--json", *override]) == (
        0 if expected_green else 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["line_broken"] is not expected_green
    assert launches == (["c1", "c2"] if expected_green else ["c1"])
    home = Path(os.environ["VIBECRAFTED_HOME"])
    receipts_path = next(
        (home / "control_plane" / "dispatches").glob("*/receipts.json")
    )
    receipts = json.loads(receipts_path.read_text())
    assert receipts["cuts"]["c2"]["state"] == (
        "settled" if expected_green else "stopped"
    )
    if not expected_green:
        assert not list((home / "worktrees" / "vetcoders" / "fixture").glob("*/c2"))
    tracker = Path(payload["artifacts"]["tracker"]).read_text()
    assert f"- allow_red_baseline: {str(not expected_green).lower()}" in tracker
    assert '"ok": ' + str(expected_green).lower() in tracker
