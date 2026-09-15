from __future__ import annotations

from pathlib import Path

from vibecrafted_core.dispatch.schema import parse_dispatch
from vibecrafted_core.dispatch.supervisor import DispatchSupervisor
from vibecrafted_core.ship import parse_tracker_cuts

TABLE_HEADER = "| Cut | Phase | Agent | State | Scheduler | Supervisor evidence |"


def _never_launch(cut, prompt: str, kind: str) -> None:
    raise AssertionError("cell launcher must not run in tracker-frontmatter tests")


def _supervisor(
    tmp_path: Path,
    *,
    reports_dir: Path,
    repo: Path | None = None,
) -> DispatchSupervisor:
    repo_dir = repo if repo is not None else tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = tmp_path / "artifacts-run"
    text = f"""
schema = "vibecrafted.dispatch.v1"

[meta]
name = "vc-scaffold-gate-0915"
repo = "{repo_dir}"
reports_dir = "{reports_dir}"

[policy]
repair_rounds = 0

[[cuts]]
id = "w1-02-tracker-frontmatter"
agent = "grok"
workflow = "implement"
prompt = "write tracker frontmatter"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
"""
    dispatch = parse_dispatch(text, base_dir=tmp_path)
    return DispatchSupervisor(
        dispatch,
        launcher=_never_launch,
        artifacts_dir=artifacts_dir,
        manage_worktrees=False,
    )


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), text[:80]
    body, _sep, _rest = text[4:].partition("\n---\n")
    fields: dict[str, str] = {}
    for line in body.splitlines():
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def test_write_tracker_starts_with_package_frontmatter(tmp_path: Path) -> None:
    reports_dir = (
        tmp_path / "artifacts" / "vetcoders" / "vibecrafted" / "2026_0915" / "reports"
    )
    supervisor = _supervisor(tmp_path, reports_dir=reports_dir)
    supervisor._write_tracker()

    text = supervisor.tracker_path.read_text(encoding="utf-8")
    fields = _frontmatter(text)
    assert fields["plan_id"] == "vc-scaffold-gate-0915"
    assert fields["role"] == "tracker"
    assert fields["agent"] == "dispatcher"
    assert fields["date"]
    assert fields["project"] == "vetcoders/vibecrafted"
    assert fields["run_id"] == supervisor.run_id
    assert fields["session_id"] == supervisor.run_id
    assert TABLE_HEADER in text
    assert "# dispatch tracker — vc-scaffold-gate-0915" in text


def test_write_tracker_table_still_parses(tmp_path: Path) -> None:
    reports_dir = (
        tmp_path / "artifacts" / "vetcoders" / "vibecrafted" / "2026_0915" / "reports"
    )
    supervisor = _supervisor(tmp_path, reports_dir=reports_dir)
    supervisor._write_tracker()

    text = supervisor.tracker_path.read_text(encoding="utf-8")
    cuts = parse_tracker_cuts(text)
    assert [cut.cut_id for cut in cuts] == ["w1-02-tracker-frontmatter"]
    assert cuts[0].state == "[ ]"


def test_write_tracker_project_falls_back_to_checkout_name(tmp_path: Path) -> None:
    repo = tmp_path / "checkout-name"
    supervisor = _supervisor(tmp_path, reports_dir=tmp_path / "reports", repo=repo)
    supervisor._write_tracker()

    fields = _frontmatter(supervisor.tracker_path.read_text(encoding="utf-8"))
    assert fields["project"] == "checkout-name"
