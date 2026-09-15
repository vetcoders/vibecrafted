"""Per-cut ``base`` (sha | branch | cut:<id>) for stacked dispatch worktrees."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.dispatch import cli as dispatch_cli
from vibecrafted_core.dispatch.doctor import diagnose_file
from vibecrafted_core.dispatch.model import classify_base
from vibecrafted_core.dispatch.receipts import DispatchReceiptStore
from vibecrafted_core.dispatch.schema import (
    DispatchSchemaError,
    doctor_dispatch,
    parse_dispatch,
)
from vibecrafted_core.dispatch.supervisor import CellRun, run_dispatch
from vibecrafted_core.dispatch.worktrees import (
    WorktreeManager,
    canonical_artifact_root,
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _repo(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "runtime-test")
    (path / ".gitignore").write_text("target/\n", encoding="utf-8")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return _git(path, "rev-parse", "HEAD")


_PLAN = """schema = "vibecrafted.dispatch.v1"
[meta]
name = "cut-base-test"
repo = "{repo}"
[policy]
await = {{ poll_s = 0.01, timeout_min = 1.0 }}
{cuts}
"""


def _cut(
    cut_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    base: str = "",
    integrator: bool = False,
) -> str:
    dependency = (
        f"depends_on = {list(depends_on)!r}\n".replace("'", '"') if depends_on else ""
    )
    declared = f'base = "{base}"\n' if base else ""
    return f'''[[cuts]]
id = "{cut_id}"
agent = "codex"
workflow = "implement"
integrator = {str(integrator).lower()}
{dependency}{declared}prompt = "run {cut_id}"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
'''


def _dispatch(repo: Path, cuts: str):
    return parse_dispatch(_PLAN.format(repo=repo, cuts=cuts))


# --------------------------------------------------------------------- parse


def test_classify_base_covers_the_three_forms_and_absent() -> None:
    assert classify_base("") == "plan"
    assert classify_base("a" * 40) == "sha"
    assert classify_base("b" * 64) == "sha"
    assert classify_base("cut:w1-02") == "cut"
    assert classify_base("feature/stack-base") == "branch"


def test_parse_base_defaults_empty_and_keeps_declared_forms(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sha = _repo(repo)
    dispatch = _dispatch(
        repo,
        _cut("plain")
        + _cut("pinned", base=sha)
        + _cut("branched", base="main")
        + _cut("stacked", depends_on=("plain",), base="cut:plain"),
    )

    plain, pinned, branched, stacked = dispatch.cuts
    assert plain.base == ""
    assert pinned.base == sha
    assert branched.base == "main"
    assert stacked.base == "cut:plain"


def test_cut_base_without_depends_on_membership_fails_parse(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _repo(repo)

    with pytest.raises(DispatchSchemaError) as exc:
        _dispatch(repo, _cut("a") + _cut("b", base="cut:a"))

    assert any(
        "cuts[1].base" in error and "requires 'a' in depends_on" in error
        for error in exc.value.errors
    ), exc.value.errors


def test_cut_base_unknown_target_fails_parse(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)

    with pytest.raises(DispatchSchemaError) as exc:
        _dispatch(repo, _cut("b", depends_on=("ghost",), base="cut:ghost"))

    errors = "\n".join(exc.value.errors)
    assert "cuts[0].base: unknown cut 'ghost'" in errors
    assert "cuts[0].depends_on: unknown cut 'ghost'" in errors


def test_cut_base_cycle_fails_parse_like_depends_on(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)

    with pytest.raises(DispatchSchemaError) as exc:
        _dispatch(
            repo,
            _cut("a", depends_on=("b",), base="cut:b")
            + _cut("b", depends_on=("a",), base="cut:a"),
        )

    assert any("dependency cycle" in error for error in exc.value.errors)


def test_integrator_cannot_declare_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sha = _repo(repo)

    with pytest.raises(DispatchSchemaError) as exc:
        _dispatch(repo, _cut("root", integrator=True, base=sha))

    assert any(
        "integrators work on the main checkout" in error for error in exc.value.errors
    )


# -------------------------------------------------------------------- doctor


def test_doctor_accepts_reachable_sha_and_branch_bases(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sha = _repo(repo)
    _git(repo, "branch", "stack-base")
    text = _PLAN.format(
        repo=repo, cuts=_cut("pinned", base=sha) + _cut("branched", base="stack-base")
    )

    result = doctor_dispatch(text)

    assert result.ok is True, result.errors


def test_doctor_rejects_unreachable_sha_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    text = _PLAN.format(repo=repo, cuts=_cut("pinned", base="0" * 40))

    result = doctor_dispatch(text)

    assert result.ok is False
    assert any(
        "cuts[0].base" in error and "base not reachable in meta.repo" in error
        for error in result.errors
    ), result.errors


def test_doctor_rejects_unknown_branch_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    text = _PLAN.format(repo=repo, cuts=_cut("branched", base="no-such-branch"))

    result = doctor_dispatch(text)

    assert result.ok is False
    assert any(
        "cuts[0].base" in error and "base not reachable in meta.repo" in error
        for error in result.errors
    ), result.errors


def test_doctor_skips_reachability_for_cut_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    text = _PLAN.format(
        repo=repo, cuts=_cut("a") + _cut("b", depends_on=("a",), base="cut:a")
    )

    result = doctor_dispatch(text)

    assert result.ok is True, result.errors


# ----------------------------------------------------------------- worktrees


def test_prepare_creates_worktree_from_the_given_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    first = _repo(repo)
    (repo / "second.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    assert _git(repo, "rev-parse", "HEAD") != first

    geometry = WorktreeManager(repo, day="2026_0915").prepare("stacked", first)

    assert _git(Path(geometry.worktree_path), "rev-parse", "HEAD") == first
    assert not (Path(geometry.worktree_path) / "second.txt").exists()


# ---------------------------------------------------------------- supervisor


def _launcher_harness(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIBECRAFTED_HOME", str(repo.parent / ".vibecrafted"))
    artifact_root = canonical_artifact_root(repo)
    reports = artifact_root / "reports" / "cut-base"
    reports.mkdir(parents=True)
    return artifact_root, reports


def test_stacked_cut_base_builds_on_the_settled_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    artifact_root, reports = _launcher_harness(repo, monkeypatch)
    dispatch = _dispatch(repo, _cut("a") + _cut("b", depends_on=("a",), base="cut:a"))

    def launcher(cut, _prompt: str, kind: str) -> CellRun:
        root = Path(cut.runtime_root)
        report = reports / f"{cut.id}.md"
        if cut.id == "a":
            body = (
                "printf alpha > a.txt\n"
                "git add a.txt\n"
                "git commit --quiet -m 'a delivers the stacked base'\n"
            )
        else:
            # The stacked cut must see its dependency's delivery as an
            # ancestor, not just in time: the file a committed exists here.
            body = "test -f a.txt\n"
        script = (
            "set -eu\n"
            + body
            + f"printf '%s\\n' {shlex.quote('completed ' + cut.id)} > "
            + shlex.quote(str(report))
            + "\n"
        )
        proc = subprocess.Popen(["bash", "-c", script], cwd=root)
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"run-{cut.id}",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )

    result = run_dispatch(
        dispatch,
        launcher=launcher,
        artifacts_dir=artifact_root / "plans" / "dispatch" / "stacked",
        run_id="stacked-cut-base",
        manage_worktrees=True,
    )

    assert result.states == {"a": "[x]", "b": "[x]"}, result.to_dict()
    store = DispatchReceiptStore("stacked-cut-base", dispatch.cuts, create=False)
    receipt_a = store.cut("a")
    receipt_b = store.cut("b")
    delivered_a = receipt_a["delivered_commit_sha"]
    assert delivered_a
    assert receipt_b["base_ref"] == "cut:a"
    assert receipt_b["base_source"] == "cut"
    assert receipt_b["base_sha"] == delivered_a
    assert receipt_b["baseline_sha"] == delivered_a
    assert receipt_a["base_source"] == "plan"
    # Git truth, not receipt text: a's delivered commit is an ancestor of b's
    # worktree HEAD.
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", delivered_a, "HEAD"],
        cwd=receipt_b["worktree_path"],
        check=False,
    )
    assert ancestor.returncode == 0


def test_explicit_branch_base_is_frozen_against_live_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    seed = _repo(repo)
    _git(repo, "branch", "stack-base")
    (repo / "moved.txt").write_text("main moved\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "advance main beyond the branch")
    moved_head = _git(repo, "rev-parse", "HEAD")
    assert moved_head != seed
    artifact_root, reports = _launcher_harness(repo, monkeypatch)
    dispatch = _dispatch(repo, _cut("frozen", base="stack-base"))

    def launcher(cut, _prompt: str, kind: str) -> CellRun:
        report = reports / f"{cut.id}.md"
        proc = subprocess.Popen(
            ["bash", "-c", f"printf done > {shlex.quote(str(report))}"]
        )
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"run-{cut.id}",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )

    result = run_dispatch(
        dispatch,
        launcher=launcher,
        artifacts_dir=artifact_root / "plans" / "dispatch" / "frozen",
        run_id="frozen-branch-base",
        manage_worktrees=True,
    )

    assert result.states == {"frozen": "[x]"}, result.to_dict()
    store = DispatchReceiptStore("frozen-branch-base", dispatch.cuts, create=False)
    receipt = store.cut("frozen")
    # local-069/071 would have advanced a plan baseline to the live head; an
    # explicit branch base stays pinned at the branch tip from resolution.
    assert receipt["base_ref"] == "stack-base"
    assert receipt["base_source"] == "branch"
    assert receipt["base_sha"] == seed
    assert receipt["baseline_sha"] == seed
    assert receipt["baseline_selection_reason"] == "explicit_branch_base"
    assert _git(Path(receipt["worktree_path"]), "rev-parse", "HEAD") == seed


def test_explicit_sha_base_is_frozen_against_live_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    seed = _repo(repo)
    (repo / "moved.txt").write_text("main moved\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "advance main beyond the sha")
    artifact_root, reports = _launcher_harness(repo, monkeypatch)
    dispatch = _dispatch(repo, _cut("pinned", base=seed))

    def launcher(cut, _prompt: str, kind: str) -> CellRun:
        report = reports / f"{cut.id}.md"
        proc = subprocess.Popen(
            ["bash", "-c", f"printf done > {shlex.quote(str(report))}"]
        )
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"run-{cut.id}",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )

    result = run_dispatch(
        dispatch,
        launcher=launcher,
        artifacts_dir=artifact_root / "plans" / "dispatch" / "pinned",
        run_id="frozen-sha-base",
        manage_worktrees=True,
    )

    assert result.states == {"pinned": "[x]"}, result.to_dict()
    store = DispatchReceiptStore("frozen-sha-base", dispatch.cuts, create=False)
    receipt = store.cut("pinned")
    assert receipt["base_ref"] == seed
    assert receipt["base_source"] == "sha"
    assert receipt["base_sha"] == seed
    assert _git(Path(receipt["worktree_path"]), "rev-parse", "HEAD") == seed


# ------------------------------------------------------------------- dry-run


def test_dry_run_json_shows_the_resolved_base_per_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    seed = _repo(repo)
    _git(repo, "branch", "stack-base")
    dispatch_file = tmp_path / "stacked.dispatch.toml"
    dispatch_file.write_text(
        _PLAN.format(
            repo=repo,
            cuts=_cut("a")
            + _cut("pinned", base=seed)
            + _cut("branched", base="stack-base")
            + _cut("b", depends_on=("a",), base="cut:a"),
        ),
        encoding="utf-8",
    )

    assert dispatch_cli.main([str(dispatch_file), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bases = payload["bases"]
    assert bases["a"] == {
        "base_ref": "",
        "base_sha": seed,
        "base_source": "plan",
    }
    assert bases["pinned"] == {
        "base_ref": seed,
        "base_sha": seed,
        "base_source": "sha",
    }
    assert bases["branched"] == {
        "base_ref": "stack-base",
        "base_sha": seed,
        "base_source": "branch",
    }
    assert bases["b"] == {
        "base_ref": "cut:a",
        "base_sha": "<pending: a>",
        "base_source": "cut",
    }


# ------------------------------------------------------------------- example


def test_stacked_cuts_example_doctors_clean_as_shipped() -> None:
    example = (
        Path(__file__).resolve().parents[3]
        / "examples"
        / "dispatch"
        / "stacked-cuts.dispatch.toml"
    )

    report = diagnose_file(example)

    assert report.ok is True, report.errors
    assert report.dispatch is not None
    by_id = {cut.id: cut for cut in report.dispatch.cuts}
    assert by_id["schema-layer"].base == ""
    assert by_id["supervisor-layer"].base == "cut:schema-layer"
    assert by_id["docs-layer"].base == "cut:supervisor-layer"
    assert by_id["supervisor-layer"].depends_on == ("schema-layer",)
    assert by_id["docs-layer"].depends_on == ("supervisor-layer",)
