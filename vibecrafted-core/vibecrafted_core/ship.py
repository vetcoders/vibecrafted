"""vc-ship CLI: lifecycle launcher, ship-prompt builder, and DeliverySeal issuer."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import loop, ui
from .delivery import seal
from .delivery.model import DeliverySeal, DeliveryState, ProofState
from .delivery.proof import ENGINE_VERSION
from .delivery.store import DeliveryStore, DeliveryStoreError, atomic_write_json
from .events import DeliveryEventKind, append_event
from .lifecycle_runner import (
    LifecycleRunSpec,
    _control_verbs,
    delivery_axes_for_receipt,
    run_lifecycle,
)

SUPPORTED_AGENTS = {"claude", "codex", "gemini", "agy", "junie", "grok", "cursor"}

DEFAULT_SHIP_PROMPT = (
    "Run the full Vibecrafted lifecycle for this repository. Load Context Atlas, "
    "start at the selected lifecycle checkpoint, preserve READ/WRITE phase "
    "boundaries, and hand off through the manifest runner."
)

SHIP_ISSUER = "vc-ship"
SHIP_SEAL_LAYOUT = seal.DEFAULT_SEAL_LAYOUT
SEAL_REFUSAL_PATH = Path("delivery-seal-refusal.json")
ZERO_DIGEST = "sha256:" + "0" * 64
TRACKER_NAME = "tracker.md"
DEFAULT_ROADMAP_REL = Path("docs/ROADMAP_4.2.0.md")
ROADMAP_BEGIN = "<!-- vibecrafted-ship-roadmap:begin -->"
ROADMAP_END = "<!-- vibecrafted-ship-roadmap:end -->"
CUT_STATE_TOKEN = re.compile(r"\[[ x~?!]\]")
EventSink = Callable[[str, str, str, dict[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class TrackerCut:
    """One cut row from the plan-root tracker — the only live cut-state writer."""

    wave: str
    cut_id: str
    state: str
    commit_sha: str
    gate: str


@dataclass(frozen=True)
class ShipSealResult:
    """Outcome of one seal-issuance attempt: either a sealed run or a refusal."""

    delivery_state: DeliveryState
    seal: DeliverySeal | None
    refusal_reasons: tuple[str, ...]
    event: Mapping[str, Any]


def _digest_if_file(path: Path) -> str:
    """Return the sha256 digest of *path*, or "" when it does not exist."""
    return seal.digest_file(path) if path.is_file() else ""


def _execution_digest(store: DeliveryStore, role: str) -> str | None:
    """Read one execution role's content digest, or None if it was never recorded."""
    try:
        return store.read_execution(role).content_digest()
    except DeliveryStoreError:
        return None


def _seal_components(
    store: DeliveryStore, *, run_id: str, lifecycle_id: str, cut_id: str
) -> seal.SealComponents:
    """Assemble the full SealComponents evidence bundle from on-disk store artifacts."""
    envelope = store.read_execution_envelope()
    contract = store.read_proof_contract()
    proof = store.read_proof_result()
    record = store.read_delivery_record()
    subject_digest = _execution_digest(store, "subject") or ZERO_DIGEST
    oracle_digest = _execution_digest(store, "oracle")
    witness_raw = contract.witness.get("input")
    witness = Path(str(witness_raw or ""))
    if not witness.is_absolute():
        witness = Path(str(contract.subject.get("cwd") or envelope.root)) / witness
    report = store.path(SHIP_SEAL_LAYOUT.report)
    transcript = store.path(SHIP_SEAL_LAYOUT.transcript)
    control_plane = store.path(SHIP_SEAL_LAYOUT.control_plane)
    unverified = ["run_identity", "liveness"]
    for name, path in (
        ("report", report),
        ("transcript", transcript),
        ("control_plane_snapshot", control_plane),
    ):
        if not path.is_file():
            unverified.append(name)
    provenance = record.commit_provenance
    return seal.SealComponents(
        run_id=run_id,
        lifecycle_id=lifecycle_id,
        cut_id=cut_id,
        proof_id=proof.proof_id,
        run_identity_sha256=ZERO_DIGEST,
        liveness_evidence_sha256=(),
        execution_envelope_sha256=seal.digest_file(
            store.path(SHIP_SEAL_LAYOUT.envelope)
        ),
        delivery_proof_contract_sha256=seal.digest_file(
            store.path(SHIP_SEAL_LAYOUT.contract)
        ),
        proof_result_sha256=seal.digest_file(store.path(SHIP_SEAL_LAYOUT.proof_result)),
        executor_source_sha256=proof.executor_sha256,
        executor_version=ENGINE_VERSION,
        subject_evidence_sha256=subject_digest,
        witness_sha256=_digest_if_file(witness),
        oracle_evidence_sha256=oracle_digest,
        assertion_evidence_sha256=_digest_if_file(store.path("proof/assertions.json")),
        negative_control_evidence_sha256=(
            _digest_if_file(store.path("proof/negative-controls.json")),
        ),
        repo=envelope.repo,
        branch=envelope.branch,
        baseline_head=str(provenance.get("baseline_head") or envelope.expected_head),
        final_head=str(provenance.get("final_head") or envelope.expected_head),
        scoped_dirty_status_sha256=envelope.baseline_status_digest,
        commit_range=str(provenance.get("commit_range") or ""),
        report_sha256=_digest_if_file(report),
        transcript_sha256=_digest_if_file(transcript),
        control_plane_snapshot_sha256=_digest_if_file(control_plane),
        unverified_surfaces=tuple(unverified),
    )


def seal_delivery_run(
    run_dir: str | Path,
    *,
    run_id: str,
    lifecycle_id: str,
    cut_id: str,
    event_sink: EventSink = append_event,
) -> ShipSealResult:
    """Issue or explicitly refuse a seal from canonical on-disk inputs.

    This function is the shipping-authority boundary. Kernel consumers can
    produce passed proofs and delivered records, but only this vc-ship step
    calls ``seal.issue_seal`` and persists ``delivery-seal.json``.
    """

    store = DeliveryStore(run_dir)
    try:
        proof = store.read_proof_result()
        record = store.read_delivery_record()
        reasons = list(proof.refusal_reasons) + list(record.refusal_reasons)
        if proof.state is not ProofState.PASSED:
            reasons.insert(0, f"proof.{proof.state.value}: seal refused")
        if record.state is not DeliveryState.DELIVERED:
            reasons.insert(0, f"delivery.{record.state.value}: seal refused")
        if reasons:
            raise seal.SealRefusedError("; ".join(dict.fromkeys(reasons)))
        components = _seal_components(
            store, run_id=run_id, lifecycle_id=lifecycle_id, cut_id=cut_id
        )
        issued = seal.issue_seal(record, issuer=SHIP_ISSUER, components=components)
        store.write_delivery_seal(issued)
    except (DeliveryStoreError, seal.SealError, OSError, ValueError) as exc:
        reason = str(exc)
        refusal = {
            "schema": "vibecrafted.delivery-seal-refusal.v1",
            "run_id": run_id,
            "issuer": SHIP_ISSUER,
            "delivery_state": DeliveryState.UNVERIFIED.value,
            "reason": reason,
        }
        atomic_write_json(store.path(SEAL_REFUSAL_PATH), refusal)
        event = event_sink(
            "delivery.seal_refused", run_id, "vc-ship refused DeliverySeal", refusal
        )
        return ShipSealResult(
            delivery_state=DeliveryState.UNVERIFIED,
            seal=None,
            refusal_reasons=(reason,),
            event=event,
        )

    event_payload = {
        "seal_id": issued.seal_id,
        "issuer": issued.issuer,
        "delivery_state": DeliveryState.SEALED.value,
        "declared_scope": issued.declared_scope,
        "checked_scope": issued.checked_scope,
    }
    event = event_sink(
        DeliveryEventKind.DELIVERY_SEALED.value,
        run_id,
        "vc-ship issued DeliverySeal",
        event_payload,
    )
    return ShipSealResult(
        delivery_state=DeliveryState.SEALED,
        seal=issued,
        refusal_reasons=(),
        event=event,
    )


def build_ship_prompt(agent: str, checkpoint: str, prompt: str) -> str:
    """Wrap a raw prompt in the vc-loop supervisor instructions for --loop-only mode."""
    return "\n".join(
        [
            "VC-SHIP interactive supervisor loop.",
            "",
            f"agent: {agent}",
            f"checkpoint: {checkpoint}",
            "",
            "Rules:",
            "- Keep LOOP active until the ship checkpoint is genuinely handled.",
            "- Before final answer, run: vc-loop next",
            "- Complete only with: vc-loop complete --promise VC_SHIP_DONE",
            "- Use Loctree + AICX as constant context when prior intent matters.",
            "",
            "--- INPUT ---",
            prompt,
        ]
    )


def resolve_plan_root(plan: str | Path) -> Path:
    """Accept a plan directory or a path to ``tracker.md``."""
    path = Path(plan).expanduser()
    if path.is_file() and path.name == TRACKER_NAME:
        return path.parent
    return path


def display_plan_path(plan_root: Path, *, original: str = "") -> str:
    """Render a plan path without leaking a host home prefix into a shipped doc."""
    raw = original.strip()
    if raw.startswith("~"):
        return Path(raw).as_posix()
    resolved = plan_root.expanduser().resolve()
    try:
        return "~/" + resolved.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator_row(cells: Sequence[str]) -> bool:
    if not cells:
        return False
    return all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "") or "") for cell in cells
    )


def parse_tracker_cuts(text: str) -> list[TrackerCut]:
    """Parse the first markdown table that has both Cut and state columns."""
    lines = text.splitlines()
    header_index = -1
    header: list[str] = []
    for index, line in enumerate(lines):
        cells = _table_cells(line)
        lowered = [cell.lower() for cell in cells]
        if "cut" in lowered and "state" in lowered:
            header_index = index
            header = lowered
            break
    if header_index < 0:
        raise ValueError("tracker has no cut/state table")

    columns = {name: index for index, name in enumerate(header)}
    cuts: list[TrackerCut] = []
    for line in lines[header_index + 1 :]:
        cells = _table_cells(line)
        if not cells:
            break
        if _is_separator_row(cells):
            continue
        cut_index = columns["cut"]
        state_index = columns["state"]
        if len(cells) <= max(cut_index, state_index):
            continue
        cut_id = cells[cut_index]
        if not cut_id or cut_id.lower() == "cut":
            continue
        match = CUT_STATE_TOKEN.search(cells[state_index])
        if match is None:
            continue
        wave = ""
        if "wave" in columns and len(cells) > columns["wave"]:
            wave = cells[columns["wave"]]
        commit_key = "commit sha" if "commit sha" in columns else "commit"
        commit = ""
        if commit_key in columns and len(cells) > columns[commit_key]:
            commit = cells[columns[commit_key]]
        gate = ""
        if "gate" in columns and len(cells) > columns["gate"]:
            gate = cells[columns["gate"]]
        cuts.append(
            TrackerCut(
                wave=wave or "—",
                cut_id=cut_id,
                state=match.group(0),
                commit_sha=commit or "—",
                gate=gate or "—",
            )
        )
    if not cuts:
        raise ValueError("tracker has no cut rows")
    return cuts


def dou_index(cuts: Sequence[TrackerCut]) -> tuple[int, int]:
    """Return ``(delivered, total)`` from tracker states. Render never flips them."""
    delivered = sum(1 for cut in cuts if cut.state == "[x]")
    return delivered, len(cuts)


def render_roadmap_block(
    cuts: Sequence[TrackerCut],
    *,
    plan_display: str,
) -> str:
    """Markdown projection of tracker cut states. Not a delivery certificate."""
    delivered, total = dou_index(cuts)
    counts = {
        token: sum(1 for cut in cuts if cut.state == token)
        for token in ("[x]", "[?]", "[ ]", "[~]", "[!]")
    }
    rows = [
        "| Wave | Cut | State | Commit SHA | Gate |",
        "| ---- | --- | ----- | ---------- | ---- |",
    ]
    for cut in cuts:
        rows.append(
            f"| {cut.wave} | {cut.cut_id} | {cut.state} | {cut.commit_sha} | {cut.gate} |"
        )
    return "\n".join(
        [
            "## Cut states (from tracker)",
            "",
            f"Source of truth: `{plan_display}/{TRACKER_NAME}`",
            "Rendered by: `vibecrafted ship roadmap --render --plan <plan_root>`",
            "This block is a projection of the dispatcher-written tracker.",
            "It is not a delivery certificate and does not flip cut states.",
            "",
            *rows,
            "",
            (
                f"**dou-index:** {delivered}/{total} — "
                f"`[x]` {counts['[x]']} · `[?]` {counts['[?]']} · "
                f"`[ ]` {counts['[ ]']} · `[~]` {counts['[~]']} · "
                f"`[!]` {counts['[!]']}"
            ),
            "",
            (
                "Only a delivery-verifier flips `[~]→[x]`. Stage snapshots below, "
                "if any, are historical notes — not a second live writer."
            ),
        ]
    )


def splice_generated_block(existing: str, block: str) -> str:
    """Replace or insert the generated block. Leave every other line untouched."""
    payload = f"{ROADMAP_BEGIN}\n{block.rstrip()}\n{ROADMAP_END}"
    if ROADMAP_BEGIN in existing and ROADMAP_END in existing:
        prefix, remainder = existing.split(ROADMAP_BEGIN, 1)
        _, suffix = remainder.split(ROADMAP_END, 1)
        return prefix + payload + suffix
    if not existing.strip():
        return payload + "\n"
    lines = existing.splitlines(keepends=True)
    insert_at: int | None = None
    seen_title = False
    for index, line in enumerate(lines):
        if line.startswith("# "):
            seen_title = True
            continue
        if seen_title and line.startswith("## "):
            insert_at = index
            break
    if insert_at is None:
        return existing.rstrip() + "\n\n" + payload + "\n"
    before = "".join(lines[:insert_at])
    after = "".join(lines[insert_at:])
    if before and not before.endswith("\n"):
        before += "\n"
    if before and not before.endswith("\n\n"):
        before += "\n"
    return before + payload + "\n\n" + after


def render_roadmap_from_tracker(
    *,
    plan_root: Path,
    roadmap_path: Path | None = None,
    stdout: bool = False,
    original_plan: str = "",
) -> str:
    """Read ``tracker.md`` and project it into ROADMAP (or return the block)."""
    root = resolve_plan_root(plan_root)
    tracker = root / TRACKER_NAME
    if not tracker.is_file():
        raise FileNotFoundError(f"tracker not found: {tracker}")
    cuts = parse_tracker_cuts(tracker.read_text(encoding="utf-8"))
    block = render_roadmap_block(
        cuts, plan_display=display_plan_path(root, original=original_plan)
    )
    if stdout or roadmap_path is None:
        return block
    existing = (
        roadmap_path.read_text(encoding="utf-8") if roadmap_path.is_file() else ""
    )
    rendered = splice_generated_block(existing, block)
    roadmap_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = roadmap_path.with_name(roadmap_path.name + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(roadmap_path)
    return block


def _roadmap_main(argv: Sequence[str]) -> int:
    """``vc-ship roadmap --render --plan`` — project tracker cut states into ROADMAP."""
    parser = argparse.ArgumentParser(prog="vc-ship roadmap")
    parser.add_argument(
        "--render",
        action="store_true",
        required=True,
        help="write or refresh the generated ROADMAP block from tracker.md",
    )
    parser.add_argument(
        "--plan",
        required=True,
        help="plan root directory (or path to tracker.md)",
    )
    parser.add_argument(
        "--roadmap",
        default="",
        help=f"ROADMAP path (default: {DEFAULT_ROADMAP_REL})",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the generated block and do not write ROADMAP",
    )
    args = parser.parse_args(list(argv))
    plan_root = resolve_plan_root(args.plan)
    roadmap_path = (
        Path(args.roadmap).expanduser()
        if args.roadmap
        else Path.cwd() / DEFAULT_ROADMAP_REL
    )
    try:
        block = render_roadmap_from_tracker(
            plan_root=plan_root,
            roadmap_path=None if args.stdout else roadmap_path,
            stdout=args.stdout,
            original_plan=args.plan,
        )
    except (OSError, ValueError) as exc:
        ui.err(str(exc), fix="pass --plan to a directory that contains tracker.md")
        return 1
    if args.stdout:
        print(block)
        return 0
    delivered, total = dou_index(
        parse_tracker_cuts((plan_root / TRACKER_NAME).read_text(encoding="utf-8"))
    )
    ui.ok(f"roadmap projected from tracker ({delivered}/{total})")
    ui.next_step(str(roadmap_path), "live cut states; do not hand-edit the block")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: roadmap projection, control verbs, --loop-only, or a run."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "roadmap":
        return _roadmap_main(args_list[1:])
    if args_list and args_list[0] in _control_verbs():
        from .lifecycle_control import lifecycle_control_main

        return lifecycle_control_main(args_list, workflow_id="vc-ship")
    parser = argparse.ArgumentParser(prog="vc-ship")
    parser.add_argument("agent", nargs="?", default="codex")
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("-f", "--file", default="")
    parser.add_argument("-p", "--prompt", default="")
    parser.add_argument("--runtime", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--start-stage", default="")
    parser.add_argument("--await-stages", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--loop-only", action="store_true")
    args = parser.parse_args(args_list)

    if args.agent not in SUPPORTED_AGENTS:
        ui.err(
            f"unknown agent: {args.agent}",
            fix="use one of: claude · codex · gemini · agy · junie · grok",
        )
        return 1
    if args.loop_only:
        prompt = args.prompt or DEFAULT_SHIP_PROMPT
        if args.file:
            prompt = Path(args.file).expanduser().read_text(encoding="utf-8")
        loop_prompt = build_ship_prompt(
            args.agent, args.checkpoint or "scaffold", prompt
        )
        return loop.main(
            [
                "start",
                "--prompt",
                loop_prompt,
                "--completion-promise",
                "VC_SHIP_DONE",
                "--max-iterations",
                str(args.max_iterations),
            ]
        )

    # Stage workers are headless by default so their lifecycle does not depend on
    # vc-frame/Zellij. An explicit --runtime terminal remains an operator opt-in,
    # and continuations inherit the selected runtime via the baton.
    from .cli import _default_runtime

    root = args.root or str(Path.cwd())
    state = run_lifecycle(
        LifecycleRunSpec(
            workflow_id="vc-ship",
            agent=args.agent,
            # The default prompt must not shadow --file: the runner resolves
            # `spec.prompt or read(spec.file)`, so a --file mission needs an
            # empty prompt to actually reach the stage workers.
            prompt=args.prompt or ("" if args.file else DEFAULT_SHIP_PROMPT),
            file=args.file,
            root=root,
            runtime=_default_runtime(args.runtime, root),
            await_stages=args.await_stages,
            start_stage=args.start_stage or args.checkpoint or "scaffold",
        )
    )
    print("==================== VC-SHIP LIFECYCLE RECEIPT ====================")
    print(f"run_id:     {state.get('run_id')}")
    print(f"workflow:   {state.get('workflow')}")
    print(f"status:     {state.get('status')}")
    axes = delivery_axes_for_receipt(str(state.get("status") or ""), state)
    print(f"execution:  {axes['execution_state']}")
    print(f"proof:      {axes['proof_state']}")
    print(f"delivery:   {axes['delivery_state']}")
    print(f"state:      {state.get('state_path')}")
    print(f"report:     {state.get('report_path')}")
    print("===================================================================")
    return 0 if state.get("status") in {"launching", "completed"} else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
