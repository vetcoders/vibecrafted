#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<'EOF_USAGE'
Usage: await.sh [claude|codex|agy|junie|grok] [--last] [--run-id <id>] [--research] [--auto-synthesize] [--describe] [--interval <sec>] [--timeout <sec>] [--startup-grace <sec>] [targets...]

Targets may be:
  - *.meta.json
  - *.transcript.log
  - *.md report path
  - generated launcher *.sh

Examples:
  await.sh codex --last
  await.sh claude --run-id impl-123456
  await.sh --research --run-id rsch-123456
  await.sh --describe /tmp/vc-research-claude.sh /tmp/vc-research-codex.sh /tmp/vc-research-agy.sh
  await.sh /path/to/report.meta.json /path/to/other.meta.json
EOF_USAGE
}

root="${VIBECRAFTED_ROOT:-$(spawn_repo_root)}"
store_dir="${VIBECRAFTED_AWAIT_STORE_DIR:-$(spawn_store_dir "$root")}"
reports_dir="${VIBECRAFTED_AWAIT_REPORTS_DIR:-$store_dir/reports}"
export VIBECRAFTED_AWAIT_STORE_DIR="$store_dir"
export VIBECRAFTED_AWAIT_REPORTS_DIR="$reports_dir"
export VIBECRAFTED_AWAIT_REPO_ROOT="$root"

exec python3 - "$@" <<'PY'
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def usage() -> None:
    print(
        "Usage: await.sh [claude|codex|agy|junie|grok] [--last] [--run-id <id>] "
        "[--research] [--auto-synthesize] [--describe] [--interval <sec>] [--timeout <sec>] "
        "[--startup-grace <sec>] [targets...]"
    )


argv = sys.argv[1:]
agent = ""
use_last = False
describe_only = False
research_mode = False
auto_synthesize = False
run_id = ""
interval = 30
timeout = 0
startup_grace = int(os.environ.get("VIBECRAFTED_AWAIT_STARTUP_GRACE", "90") or "90")
targets: list[str] = []

i = 0
while i < len(argv):
    arg = argv[i]
    if arg in {"claude", "codex", "agy", "junie", "grok", "cursor"} and not agent:
        agent = arg
    elif arg == "--last":
        use_last = True
    elif arg == "--describe":
        describe_only = True
    elif arg == "--research":
        research_mode = True
    elif arg == "--auto-synthesize":
        auto_synthesize = True
        research_mode = True
    elif arg == "--run-id":
        i += 1
        if i >= len(argv):
            print("Missing value for --run-id", file=sys.stderr)
            sys.exit(1)
        run_id = argv[i]
    elif arg == "--interval":
        i += 1
        if i >= len(argv):
            print("Missing value for --interval", file=sys.stderr)
            sys.exit(1)
        interval = max(int(argv[i]), 1)
    elif arg == "--timeout":
        i += 1
        if i >= len(argv):
            print("Missing value for --timeout", file=sys.stderr)
            sys.exit(1)
        timeout = max(int(argv[i]), 0)
    elif arg == "--startup-grace":
        i += 1
        if i >= len(argv):
            print("Missing value for --startup-grace", file=sys.stderr)
            sys.exit(1)
        startup_grace = max(int(argv[i]), 0)
    elif arg in {"-h", "--help", "help"}:
        usage()
        sys.exit(0)
    else:
        targets.append(arg)
    i += 1

store_dir = Path(os.environ.get("VIBECRAFTED_AWAIT_STORE_DIR", "")).expanduser()
reports_dir = Path(os.environ.get("VIBECRAFTED_AWAIT_REPORTS_DIR", "")).expanduser()


def parse_launcher(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {"launcher": str(path)}
    if not path.is_file():
        return payload
    wanted = {"meta", "report", "transcript", "SPAWN_RUN_ID", "SPAWN_AGENT"}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if key not in wanted:
            continue
        raw = raw.strip()
        try:
            parts = shlex.split(raw)
            value = parts[0] if parts else raw
        except ValueError:
            value = raw.strip("'\"")
        if key == "meta":
            payload["meta"] = value
        elif key == "report":
            payload["report"] = value
        elif key == "transcript":
            payload["transcript"] = value
        elif key == "SPAWN_RUN_ID":
            payload["run_id"] = value
        elif key == "SPAWN_AGENT":
            payload["agent"] = value
    return payload


def backfill_from_meta(descriptor: dict[str, str]) -> dict[str, str]:
    meta_path = descriptor.get("meta", "")
    if not meta_path:
        return descriptor
    path = Path(meta_path)
    if not path.is_file():
        return descriptor
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return descriptor
    for key in ("agent", "status", "mode", "model", "input", "report", "transcript", "launcher", "exit_code", "updated_at", "completed_at", "run_id", "launcher_pid", "liveness"):
        value = data.get(key)
        if value is None:
            continue
        descriptor[key] = str(value)
    return descriptor


def descriptor_from_target(raw: str) -> dict[str, str]:
    path = Path(raw).expanduser()
    desc: dict[str, str] = {"source": raw}
    if raw.endswith(".meta.json"):
        desc["meta"] = str(path)
    elif raw.endswith(".transcript.log"):
        desc["transcript"] = str(path)
        desc["meta"] = str(path).replace(".transcript.log", ".meta.json")
    elif raw.endswith(".md"):
        desc["report"] = str(path)
        legacy_meta = Path(str(path).rsplit(".md", 1)[0] + ".meta.json")
        research_meta = path.parent.parent / "logs" / f"{path.stem}.meta.json"
        desc["meta"] = str(research_meta if research_meta.is_file() else legacy_meta)
    elif raw.endswith(".sh"):
        desc.update(parse_launcher(path))
    else:
        desc["meta"] = str(path)
    return backfill_from_meta(resolve_missing_meta(desc))


def list_legacy_meta_files() -> list[Path]:
    if not reports_dir.is_dir():
        return []
    return sorted(reports_dir.glob("*.meta.json"))


def list_research_meta_files() -> list[Path]:
    if not store_dir.is_dir():
        return []
    metas = [
        *store_dir.glob("research/*/logs/*.meta.json"),
        *store_dir.glob("research/*/reports/*.meta.json"),
    ]
    return sorted(dict.fromkeys(metas))


def list_meta_files(*, include_research: bool = False) -> list[Path]:
    metas = list_legacy_meta_files()
    if include_research:
        metas.extend(list_research_meta_files())
    return sorted(dict.fromkeys(metas))


def load_meta(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def extract_field_from_artifact(path: Path, field: str) -> str:
    if not path.is_file():
        return ""
    prefix = f"{field}:"
    try:
        for line in path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[:80]:
            stripped = line.strip()
            if stripped.startswith(prefix):
                return stripped.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    return ""


def resolve_missing_meta(descriptor: dict[str, str]) -> dict[str, str]:
    meta_path = descriptor.get("meta", "")
    if not meta_path or Path(meta_path).is_file():
        return descriptor

    probes: list[Path] = []
    for key in ("report", "transcript"):
        raw = descriptor.get(key, "")
        if raw:
            probes.append(Path(raw).expanduser())

    expected = Path(meta_path).expanduser()
    if not probes and expected.name.endswith(".meta.json"):
        stem = expected.name[: -len(".meta.json")]
        probes.extend(
            [
                expected.with_name(f"{stem}.md"),
                expected.with_name(f"{stem}.transcript.log"),
            ]
        )

    wanted_run_id = ""
    wanted_session_id = ""
    for probe in probes:
        wanted_run_id = wanted_run_id or extract_field_from_artifact(probe, "run_id")
        wanted_session_id = wanted_session_id or extract_field_from_artifact(
            probe, "session_id"
        )
        if wanted_run_id and wanted_session_id:
            break

    if not wanted_run_id and not wanted_session_id:
        return descriptor

    for candidate in list_meta_files(include_research=True):
        payload = load_meta(candidate)
        if not payload:
            continue
        if wanted_run_id and str(payload.get("run_id") or "") == wanted_run_id:
            descriptor["meta"] = str(candidate)
            return descriptor
        if wanted_session_id and str(payload.get("session_id") or "") == wanted_session_id:
            descriptor["meta"] = str(candidate)
            return descriptor

    return descriptor


def is_empty(value: object) -> bool:
    return value in (None, "", "None", "null")


def meta_age_seconds(item: dict[str, str]) -> float:
    updated_at = item.get("updated_at", "")
    if updated_at:
        try:
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - updated_dt.astimezone(timezone.utc)).total_seconds())
        except ValueError:
            pass
    meta_path = item.get("meta", "")
    if meta_path and Path(meta_path).is_file():
        return max(0.0, time.time() - Path(meta_path).stat().st_mtime)
    return 0.0


def transcript_empty(item: dict[str, str]) -> bool:
    transcript = item.get("transcript", "")
    if not transcript:
        return True
    path = Path(transcript)
    return not path.is_file() or path.stat().st_size == 0


def descriptors_for_last() -> list[dict[str, str]]:
    metas = list_meta_files(include_research=research_mode)
    if research_mode:
        last_research = None
        for meta_path in reversed(metas):
            payload = load_meta(meta_path)
            if not payload:
                continue
            if payload.get("skill_code") == "rsch":
                last_research = str(payload.get("run_id") or "")
                if last_research:
                    break
        if last_research:
            return descriptors_for_run_id(last_research)
        return []

    if agent:
        for meta_path in reversed(metas):
            payload = load_meta(meta_path)
            if not payload:
                continue
            if payload.get("agent") == agent:
                return [backfill_from_meta({"meta": str(meta_path)})]
        return []

    if metas:
        return [backfill_from_meta({"meta": str(metas[-1])})]
    return []


def descriptors_for_run_id(target_run_id: str) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    include_research = research_mode or target_run_id.startswith("rsch-")
    for meta_path in list_meta_files(include_research=include_research):
        payload = load_meta(meta_path)
        if not payload:
            continue
        if str(payload.get("run_id") or "") == target_run_id:
            matches.append(backfill_from_meta({"meta": str(meta_path)}))
    return matches


def resolve_descriptors() -> list[dict[str, str]]:
    if targets:
        return [descriptor_from_target(t) for t in targets]
    if run_id:
        return descriptors_for_run_id(run_id)
    if use_last or (not targets and not run_id):
        return descriptors_for_last()
    return []


def print_card(items: list[dict[str, str]]) -> None:
    print("⚒  Await")
    print("─────────────────────────────────────────")
    if reports_dir:
        print(f"  reports: {reports_dir}")
    print(f"  tracks:  {len(items)}")
    print("─────────────────────────────────────────")
    for idx, item in enumerate(items, start=1):
        print()
        print(f"--- Track {idx} ---")
        for key in ("agent", "run_id", "status", "mode", "model", "meta", "report", "transcript", "launcher", "exit_code", "updated_at"):
            value = item.get(key, "")
            if value:
                print(f"  {key:10s} {value}")


def heartbeat_line(item: dict[str, str]) -> str:
    return (
        "heartbeat "
        f"run_id={item.get('run_id', '-') or '-'} "
        f"status={item.get('status', '-') or '-'} "
        f"liveness={item.get('liveness', '-') or '-'} "
        f"updated_at={item.get('updated_at', '-') or '-'} "
        f"report={item.get('report', '-') or '-'} "
        f"transcript={item.get('transcript', '-') or '-'}"
    )


def print_heartbeat(items: list[dict[str, str]]) -> None:
    for item in items:
        print(heartbeat_line(item), flush=True)


def false_launched(items: list[dict[str, str]]) -> list[dict[str, str]]:
    stale: list[dict[str, str]] = []
    live_statuses = {"launching", "running", "in-progress"}
    for item in items:
        current = backfill_from_meta(dict(item))
        if current.get("status") not in live_statuses:
            continue
        if current.get("exit_code", "") not in {"", "None", "null"}:
            continue
        launcher_pid = current.get("launcher_pid", "")
        liveness = current.get("liveness", "")
        if not (is_empty(launcher_pid) or liveness == "pid_pending"):
            continue
        if not transcript_empty(current):
            continue
        if meta_age_seconds(current) < startup_grace:
            continue
        stale.append(current)
    return stale


def print_false_launch(items: list[dict[str, str]]) -> None:
    print("Detected false-launched run: launch metadata exists but no worker became observable.", file=sys.stderr)
    for item in items:
        run = item.get("run_id", "-") or "-"
        meta = item.get("meta", "-") or "-"
        launcher = item.get("launcher", "") or ""
        print(f"run_id={run} status={item.get('status', '-') or '-'} liveness={item.get('liveness', '-') or '-'} updated_at={item.get('updated_at', '-') or '-'}", file=sys.stderr)
        print(f"meta={meta}", file=sys.stderr)
        if item.get("report"):
            print(f"report={item['report']}", file=sys.stderr)
        if item.get("transcript"):
            print(f"transcript={item['transcript']}", file=sys.stderr)
        if launcher:
            print(f"Recovery: bash {shlex.quote(launcher)}", file=sys.stderr)
        else:
            print("Recovery: launcher path unavailable in metadata; inspect the meta path above.", file=sys.stderr)


def all_completed(items: list[dict[str, str]]) -> tuple[bool, list[dict[str, str]]]:
    resolved: list[dict[str, str]] = []
    for item in items:
        current = backfill_from_meta(dict(item))
        meta_path = current.get("meta", "")
        if not meta_path or not Path(meta_path).is_file():
            return False, items
        exit_code = current.get("exit_code", "")
        if exit_code in {"", "None"}:
            return False, items
        resolved.append(current)
    return True, resolved


def emit_event(kind: str, target_run_id: str, message: str, payload: dict[str, object]) -> None:
    home = Path(os.environ.get("VIBECRAFTED_HOME", str(Path.home() / ".vibecrafted"))).expanduser()
    stream = home / "control_plane" / "events.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": target_run_id,
        "kind": kind,
        "message": message,
        "payload": payload,
    }
    with stream.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def trigger_synthesis(items: list[dict[str, str]]) -> None:
    if not auto_synthesize or not items:
        return
    target_run_id = run_id or items[0].get("run_id", "")
    if not target_run_id:
        return
    last = max(items, key=lambda item: item.get("completed_at") or item.get("updated_at") or "")
    reports = [item.get("report", "") for item in items if item.get("report")]
    emit_event(
        "synthesize-trigger",
        target_run_id,
        "all research tracks completed; synthesis spawn requested",
        {
            "last_agent": last.get("agent", ""),
            "reports": reports,
            "metas": [item.get("meta", "") for item in items if item.get("meta")],
        },
    )
    synth = Path(os.environ.get("VIBECRAFTED_AWAIT_REPO_ROOT", "")) / "bin" / "vc-research-synthesize"
    if os.environ.get("VIBECRAFTED_AUTO_SYNTHESIZE_NO_SPAWN") == "1":
        return
    if synth.is_file():
        subprocess.Popen(
            [str(synth), "--run-id", target_run_id],
            cwd=str(synth.parent.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


deadline = time.time() + timeout if timeout > 0 else None
items = resolve_descriptors()

if describe_only:
    if not items:
        print("No matching launchers or metadata found yet.", file=sys.stderr)
        sys.exit(1)
    print_card(items)
    sys.exit(0)

if not items:
    print("No matching launchers or metadata found yet. Waiting...", file=sys.stderr)

while True:
    items = resolve_descriptors()
    if items:
        print_heartbeat([backfill_from_meta(dict(item)) for item in items])
        false_launches = false_launched(items)
        if false_launches:
            print_false_launch(false_launches)
            sys.exit(2)
        done, resolved = all_completed(items)
        if done:
            trigger_synthesis(resolved)
            print_card(resolved)
            all_zero = all(str(item.get("exit_code", "1")) == "0" for item in resolved)
            sys.exit(0 if all_zero else 1)
    if deadline is not None and time.time() >= deadline:
        print("Timed out while waiting for metadata completion.", file=sys.stderr)
        sys.exit(124)
    time.sleep(interval)
PY
