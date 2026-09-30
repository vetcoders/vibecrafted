from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vibecrafted_core import cli as root_cli
from vibecrafted_core.dispatch import cli as dispatch_cli


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
