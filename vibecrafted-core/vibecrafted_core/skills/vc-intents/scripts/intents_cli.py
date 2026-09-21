#!/usr/bin/env python3
"""vc-intents — the durable side of an intent hunt.

Everything that must survive a compaction, a second host, or a second fleet
lives in one working directory and is produced by this script, so every run
and every agent writes the same shape. Agents do the reading and the judging;
this script does the bookkeeping, the falsification that needs no judgment,
and the plans that hand the judging to a fleet.

    uv run intents_cli.py <workdir> <command> [options]

Commands, in procedure order:

    sweep          account for sources in a window: transcript dirs (shards
                   for extraction) and aicx (indexed intents); writes coverage
    verify-quotes  merge extraction shards, reject any quote that is not a
                   verbatim substring of its source file
    lanes          split the catalog into confrontation lanes by subject
    plan           write a vibecrafted.dispatch.v1 plan: --stage extract |
                   confront | implement
    merge          fold per-host verdicts into LEDGER.json, score agreement
    reclassify     apply the two mechanical rules; append to overrides.jsonl
    decide         append a human override (or import a review export)
    queue          stable, still-open queue → queue.json + STABLE-QUEUE.md
    render         LEDGER.md + intents.html (review surface)
    status         counts and closure verdict; exit 0 closed / 1 open

Stdlib only. Long output goes to files; stdout carries paths and counts.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "vc-intents.ledger.v2"
DISPATCH_SCHEMA = "vibecrafted.dispatch.v1"

# Verdicts a fleet may return. `landed` needs evidence; `absent` needs the
# question that returned nothing — the confrontation brief says so.
FLEET_DISPOSITIONS = (
    "landed",
    "partial",
    "absent",
    "superseded",
    "contradiction",
    "unclear",
)
# Mechanical reclassifications of `landed` (see references/ledger-schema.md).
DERIVED = ("landed-contested", "landed-by-doc")
# Human-only.
HUMAN = ("drop",)
DISPOSITIONS = FLEET_DISPOSITIONS + DERIVED + HUMAN
CLOSING = {"landed", "superseded", "drop"}
OPEN = set(DISPOSITIONS) - CLOSING
ACTIONABLE = {"partial", "absent", "landed-contested", "landed-by-doc"}

KINDS = ("task", "constraint", "preference", "decision", "complaint", "question")
RISKS = ("data-loss", "security", "none")
FLOWS = ("core", "adjacent", "peripheral")

CODE_EXT = re.compile(
    r"\.(rs|swift|py|ts|tsx|js|jsx|toml|sh|zsh|c|h|m|mm|go|kt|java|rb|sql|yaml|yml|json|plist|kdl|lua)\b"
)
DOC_ONLY = re.compile(r"\.(md|txt|rst|adoc)\b")
SOURCE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")
HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"


# ----------------------------------------------------------------------------
# small utilities
# ----------------------------------------------------------------------------


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, data) -> None:
    """Atomic write: a half-written ledger is worse than a stale one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def norm(s: str) -> str:
    """Collapse whitespace only. The verbatim promise is character-level."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def source_path(roots: list[Path], name: str) -> Path | None:
    """`2026-01-02_061017_x_raw.txt` lives at `<root>/2026-01-02/061017_x_raw.txt`
    or flat at `<root>/2026-01-02_061017_x_raw.txt`. Both layouts are real."""
    m = SOURCE_DATE.match(name)
    for root in roots:
        cands = [root / name]
        if m:
            cands.append(root / m.group(1) / m.group(2))
        for c in cands:
            if c.is_file():
                return c
    return None


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def ledger_path(work: Path) -> Path:
    return work / "LEDGER.json"


def load_ledger(work: Path) -> dict:
    p = ledger_path(work)
    if not p.exists():
        die(
            f"no ledger at {p} — run `merge` first (or `verify-quotes` to build the catalog)"
        )
    return read_json(p)


def overrides_path(work: Path) -> Path:
    return work / "overrides.jsonl"


# ----------------------------------------------------------------------------
# sweep — account for sources inside a window
# ----------------------------------------------------------------------------


def cmd_sweep(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    work.mkdir(parents=True, exist_ok=True)
    since, until = a.since or "0000-00-00", a.until or "9999-99-99"
    coverage: list[dict] = []
    shards_dir = work / "shards"
    total_files = 0

    for spec in a.source:
        if spec.startswith("dir:"):
            root = Path(spec[4:]).expanduser()
            if not root.is_dir():
                coverage.append(
                    {
                        "source": spec,
                        "state": "unavailable",
                        "reason": "directory not found",
                        "files": 0,
                    }
                )
                continue
            files = []
            for p in sorted(root.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in (".txt", ".md"):
                    continue
                rel = p.relative_to(root)
                name = rel.name if len(rel.parts) == 1 else f"{rel.parts[0]}_{rel.name}"
                m = SOURCE_DATE.match(name)
                if not m or not (since <= m.group(1) <= until):
                    continue
                files.append((name, p))
            total_files += len(files)
            chars = sum(p.stat().st_size for _, p in files)
            coverage.append(
                {
                    "source": spec,
                    "state": "read" if files else "empty",
                    "files": len(files),
                    "bytes": chars,
                    "first": files[0][0][:10] if files else None,
                    "last": files[-1][0][:10] if files else None,
                }
            )
            # Shards: one directory per N files, chronological. Extraction
            # agents read one shard each; the brief says how.
            if files and a.shard_size:
                shards_dir.mkdir(parents=True, exist_ok=True)
                for i in range(0, len(files), a.shard_size):
                    chunk = files[i : i + a.shard_size]
                    sid = f"s{i // a.shard_size + 1:02d}"
                    d = shards_dir / sid
                    d.mkdir(exist_ok=True)
                    for name, p in chunk:
                        shutil.copy2(p, d / name)
                    (d / "_manifest.json").write_text(
                        json.dumps(
                            {
                                "shard": sid,
                                "files": len(chunk),
                                "from": chunk[0][0][:10],
                                "to": chunk[-1][0][:10],
                            },
                            ensure_ascii=False,
                            indent=1,
                        )
                    )
        elif spec == "aicx":
            if not a.repo:
                coverage.append(
                    {
                        "source": "aicx",
                        "state": "unavailable",
                        "reason": "--repo required for aicx",
                    }
                )
                continue
            hours = a.aicx_hours
            cmd = [
                "aicx",
                "intents",
                "-p",
                f"/{Path(a.repo).name}",
                "--hours",
                str(hours),
                "--score",
                "50",
                "--sort",
                "newest",
                "--emit",
                "json",
            ]
            err = work / "aicx.sweep.err"
            try:
                with err.open("w") as ef:
                    out = subprocess.run(
                        cmd,
                        capture_output=False,
                        stdout=subprocess.PIPE,
                        stderr=ef,
                        text=True,
                        timeout=600,
                        check=False,
                    )
                rows = (
                    json.loads(out.stdout)
                    if out.stdout.strip().startswith(("[", "{"))
                    else []
                )
                if isinstance(rows, dict):
                    rows = rows.get("intents", rows.get("items", []))
                (work / "aicx.intents.json").write_text(
                    json.dumps(rows, ensure_ascii=False, indent=1)
                )
                truncated = "cap" in err.read_text().lower()
                coverage.append(
                    {
                        "source": "aicx",
                        "state": "read",
                        "rows": len(rows),
                        "exit": out.returncode,
                        "stderr": str(err),
                        "truncated": truncated,
                        "note": "aicx reports corpus truncation on stderr only"
                        if truncated
                        else "",
                    }
                )
            except FileNotFoundError:
                coverage.append(
                    {
                        "source": "aicx",
                        "state": "unavailable",
                        "reason": "aicx not on PATH",
                    }
                )
            except subprocess.TimeoutExpired:
                coverage.append(
                    {
                        "source": "aicx",
                        "state": "unavailable",
                        "reason": "aicx timed out (600 s)",
                    }
                )
        else:
            coverage.append(
                {
                    "source": spec,
                    "state": "unavailable",
                    "reason": "unknown source spec (use dir:<path> or aicx)",
                }
            )

    sweep = {
        "schema": "vc-intents.sweep.v1",
        "at": now(),
        "window": {"since": a.since, "until": a.until},
        "repo": a.repo,
        "sources": coverage,
        "shards": sorted(p.name for p in shards_dir.glob("s*"))
        if shards_dir.exists()
        else [],
    }
    write_json(work / "sweep.json", sweep)
    print(f"sweep → {work / 'sweep.json'}")
    for c in coverage:
        print(
            f"  {c['source']:<40} {c['state']:<12} {c.get('files', c.get('rows', ''))}"
            f"{'  TRUNCATED' if c.get('truncated') else ''}{'  ' + c['reason'] if c.get('reason') else ''}"
        )
    if sweep["shards"]:
        print(
            f"  shards: {len(sweep['shards'])} × ≤{a.shard_size} files → {shards_dir}"
        )
    if any(c["state"] == "unavailable" for c in coverage):
        print(
            "note: unavailable sources keep the goal open; they are a known unknown, not an absence",
            file=sys.stderr,
        )


# ----------------------------------------------------------------------------
# verify-quotes — extraction shards → catalog, verbatim or rejected
# ----------------------------------------------------------------------------


def cmd_verify_quotes(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    roots = [Path(r).expanduser() for r in a.transcripts] or [work / "shards"]
    if (work / "shards").exists() and work / "shards" not in roots:
        roots.append(work / "shards")
    # Shard copies live one level down (shards/sNN/<name>); include them.
    roots += (
        [p for p in (work / "shards").glob("s*") if p.is_dir()]
        if (work / "shards").exists()
        else []
    )

    rows, problems = [], []
    seen_ids: set[str] = set()
    for path in sorted(Path(a.extractions).glob("*.json")):
        shard = path.stem
        if shard.startswith("_"):  # _merged.json and friends are outputs, not shards
            continue
        try:
            data = read_json(path)
        except Exception as e:  # noqa: BLE001 — the report needs the reason, whatever it is
            problems.append((shard, "-", f"UNPARSEABLE: {e}"))
            continue
        if isinstance(data, dict):
            data = data.get("intents", data.get("items", []))
        for n, e in enumerate(data, 1):
            src = source_path(roots, e.get("source", ""))
            if not src:
                problems.append(
                    (shard, e.get("id") or n, f"source not found: {e.get('source')}")
                )
                continue
            body = norm(src.read_text(errors="ignore"))
            if not e.get("quote") or norm(e["quote"]) not in body:
                problems.append((shard, e.get("id") or n, "QUOTE NOT VERBATIM"))
                continue
            if e.get("kind") not in KINDS:
                problems.append(
                    (shard, e.get("id") or n, f"bad kind {e.get('kind')!r}")
                )
                continue
            ident = e.get("id") or f"{shard}-{n:03d}"
            if ident in seen_ids:
                ident = f"{shard}-{n:03d}"
            seen_ids.add(ident)
            m = SOURCE_DATE.match(e["source"])
            rows.append(
                {
                    "id": ident,
                    "date": e.get("date") or (m.group(1) if m else ""),
                    "source": e["source"],
                    "quote": e["quote"],
                    "topic": e.get("topic", ""),
                    "kind": e["kind"],
                    "subject": e.get("subject", "inne"),
                    "strength": int(e.get("strength") or 3),
                    "_shard": shard,
                }
            )
    rows.sort(key=lambda r: (r["date"], r["id"]))
    write_json(work / "intents.json", rows)
    (work / "verify-quotes.problems.txt").write_text(
        "\n".join(f"{s}\t{i}\t{why}" for s, i, why in problems)
        + ("\n" if problems else "")
    )
    print(f"verified {len(rows)} intents → {work / 'intents.json'}")
    print(f"rejected {len(problems)} → {work / 'verify-quotes.problems.txt'}")
    if problems and a.strict:
        sys.exit(1)


# ----------------------------------------------------------------------------
# lanes — split the catalog into confrontation lanes
# ----------------------------------------------------------------------------

DEFAULT_LANES = {
    "L1-overlay-ui": ["overlay", "ui"],
    "L2-stt-live": ["stt", "live"],
    "L3-agent": ["agent"],
    "L4-quality-lexicon": ["quality-loop", "lexicon"],
    "L5-settings-hotkey": ["settings", "hotkey", "cli"],
    "L6-build-release": ["build", "release", "inne"],
}


def parse_lanes(spec: list[str]) -> dict[str, list[str]]:
    if not spec:
        return dict(DEFAULT_LANES)
    out: dict[str, list[str]] = {}
    for s in spec:
        name, _, subjects = s.partition("=")
        out[name] = [x.strip() for x in subjects.split(",") if x.strip()]
    return out


def cmd_lanes(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    intents = read_json(work / "intents.json")
    lanes = parse_lanes(a.lane)
    where = {s: name for name, subs in lanes.items() for s in subs}
    fallback = next(iter(lanes))
    by_lane: dict[str, list[dict]] = defaultdict(list)
    for it in intents:
        by_lane[where.get(it["subject"], fallback)].append(it)
    out = work / "lanes"
    out.mkdir(exist_ok=True)
    for name in lanes:
        write_json(out / f"{name}.json", by_lane.get(name, []))
        print(f"  {name:<24} {len(by_lane.get(name, [])):>4}")
    write_json(
        out / "_lanes.json",
        {"lanes": lanes, "counts": {k: len(v) for k, v in by_lane.items()}},
    )
    print(f"lanes → {out}")


# ----------------------------------------------------------------------------
# plan — vibecrafted.dispatch.v1 for extract / confront / implement
# ----------------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def parse_agents(spec: list[str], default: str) -> dict[str, tuple[str, str]]:
    """`L1-overlay-ui=kimi:kimi-code/k3` → {lane: (agent, model)}; `*=codex:` sets the default."""
    out: dict[str, tuple[str, str]] = {"*": (default, "")}
    for s in spec:
        key, _, am = s.partition("=")
        agent, _, model = am.partition(":")
        out[key] = (agent or default, model)
    return out


def plan_header(
    name: str,
    description: str,
    repo: Path,
    reports_dir: Path,
    tracker: Path,
    concurrency: int,
    require_commit: bool,
    timeout_min: int,
    common: str,
) -> str:
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    head = git(repo, "rev-parse", "HEAD")
    return (
        f'schema = "{DISPATCH_SCHEMA}"\n\n[meta]\nname = {toml_str(name)}\ndescription = {toml_str(description)}\n'
        f"repo = {toml_str(str(repo))}\nbaseline = {{ branch = {toml_str(branch)}, head = {toml_str(head)} }}\n"
        f"reports_dir = {toml_str(str(reports_dir))}\ntracker = {toml_str(str(tracker))}\n\n"
        f'[policy]\nconcurrency = {concurrency}\nallow_concurrency = true\non_critical_fail = "continue"\n'
        f'on_timeout = "fail"\nrequire_commit = {"true" if require_commit else "false"}\n'
        f"await = {{ poll_s = 60, timeout_min = {timeout_min} }}\n\n[common]\ntext = '''\n{common}\n'''\n"
    )


def cut(
    id_: str,
    phase: str,
    agent: str,
    model: str,
    workflow: str,
    mode: str,
    mutation: str,
    prompt: str,
    verify_run: str | None,
    expect_contains: str | None,
) -> str:
    s = (
        f"\n[[cuts]]\nid = {toml_str(id_)}\nphase = {toml_str(phase)}\nagent = {toml_str(agent)}\n"
        + (f"model = {toml_str(model)}\n" if model else "")
        + f"workflow = {toml_str(workflow)}\nmode = {toml_str(mode)}\nmutation = {toml_str(mutation)}\n"
        + f"prompt = '''\n{prompt}\n'''\n"
    )
    if verify_run:
        # The dispatch parser hard-stop-scans raw `run` text; never put a cut id
        # literal here (`release` inside `L6-build-release` tripped it). {id}
        # is substituted by the runtime after the scan.
        s += f"\n  [[cuts.verify]]\n  run = '''{verify_run}'''\n"
        s += f"  expect = {{ contains = {toml_str(expect_contains or '')}, exit_code = 0 }}\n"
    return s


def cmd_plan(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    repo = Path(a.repo).resolve()
    art = (
        Path(a.artifacts).expanduser().resolve()
        if a.artifacts
        else (
            Path(os.environ.get("VIBECRAFTED_HOME", "~/.vibecrafted")).expanduser()
            / "artifacts"
            / repo.parent.name
            / repo.name
            / datetime.now(timezone.utc).strftime("%Y_%m%d")
        )
    )
    plans = art / "plans"
    reports = art / "reports"
    plans.mkdir(parents=True, exist_ok=True)
    host = a.host or os.uname().nodename.split(".")[0]
    agents = parse_agents(a.agent, a.default_agent)
    brief_dir = HERE.parent / "references"
    body = ""

    if a.stage == "extract":
        shards = sorted(p for p in (work / "shards").glob("s*") if p.is_dir())
        if not shards:
            die("no shards — run `sweep --source dir:<path> --shard-size N` first")
        out = work / "extractions"
        common = (
            f"TRYB: READ. Nie edytujesz repo. Jedyny zapis: Twój JSON pod ścieżką z cutu.\n"
            f"Brief ekstrakcji (cytat dosłowny, schemat, co pomijać): {brief_dir / 'extraction-brief.md'} — przeczytaj w całości.\n"
            f"Repo dla pytań strukturalnych (opcjonalnie): {{repo}} przez loctree-mcp, nie grepem."
        )
        body = plan_header(
            f"intents-extract-{host}",
            "Ekstrakcja intencji Foundera z korpusu transkrypcji, cytat dosłowny.",
            repo,
            reports,
            plans / f"intents-extract-{host}.tracker.md",
            a.concurrency,
            False,
            a.timeout_min,
            common,
        )
        body += '\n[[phases]]\ntitle = "Ekstrakcja"\ndetail = "Jeden shard na cut, równolegle, READ-only."\n'
        for sh in shards:
            n = len([p for p in sh.iterdir() if p.suffix == ".txt"])
            agent, model = agents.get(sh.name, agents["*"])
            prompt = (
                f"Shard: {{id}} ({n} plików).\nWejście: {sh}\nWyjście: {out}/{{id}}.json (tablica JSON wg briefu; utwórz katalog).\n"
                f"Nadaj id w formacie {{id}}-NNN. Po zapisie: raport 5–10 zdań po polsku wg sekcji 'Raport końcowy' briefu."
            )
            verify = (
                f"python3 -c \"import json,sys; v=json.load(open('{out}/{{id}}.json')); "
                f"bad=[x for x in v if not x.get('quote') or not x.get('source')]; "
                f"print('rows',len(v),'bad',len(bad)); sys.exit(0 if v and not bad else 1)\""
            )
            body += cut(
                sh.name,
                "Ekstrakcja",
                agent,
                model,
                "intents",
                "read",
                "allow-report-only",
                prompt,
                verify,
                "bad 0",
            )
        name = f"intents-extract.{host}.dispatch.toml"

    elif a.stage == "confront":
        lanes_meta = read_json(work / "lanes" / "_lanes.json")
        out = reports / f"verdicts-{host}"
        common = (
            f"TRYB: READ. Nie edytujesz żadnego pliku repo, nie commitujesz, nie zmieniasz gałęzi. "
            f"Jedyny zapis to Twój JSON werdyktów pod ścieżką z cutu.\n\n"
            f"Repo do konfrontacji: {{repo}} (worktree przypięty do baseline; ta ścieżka ZASTĘPUJE ścieżkę z briefu)\n"
            f"loctree-mcp: project={{repo}} — pierwszy ruch, nie ostatni. `landed` bez dowodu plik:linia jest nieważne.\n"
            f"Brief (dyspozycje, schemat wyjścia, pułapki): {brief_dir / 'confrontation-brief.md'} — przeczytaj w całości."
        )
        body = plan_header(
            f"intents-confront-{host}",
            "Konfrontacja katalogu intencji Foundera ze stanem repo. Ten sam wsad na drugim hoście = replikacja; rozjazd = dyspozycja niestabilna.",
            repo,
            reports,
            plans / f"intents-confront-{host}.tracker.md",
            a.concurrency,
            False,
            a.timeout_min,
            common,
        )
        body += '\n[[phases]]\ntitle = "Konfrontacja"\ndetail = "Tory po obszarach kodu, równolegle, READ-only."\n'
        for lane, n in lanes_meta["counts"].items():
            if not n:
                continue
            agent, model = agents.get(lane, agents["*"])
            # Lane names may contain a hard-stop word (`L6-build-release`): the
            # dispatch parser scans raw text, so every path in prompt and verify
            # is spelled through {id}, which the runtime substitutes afterwards.
            src = work / "lanes" / "{id}.json"
            prompt = (
                f"Tor: {{id}} ({n} pozycji).\nWejście:  {src}\nWyjście:  {out}/{{id}}.verdicts.json (utwórz katalog).\n"
                f"Zachowaj wszystkie {n} id z wejścia — żadna pozycja nie może wypaść.\n"
                f"Po zapisaniu JSON: raport 8–12 zdań po polsku wg sekcji 'Raport końcowy' briefu."
            )
            verify = (
                f"python3 -c \"import json,sys; v=json.load(open('{out}/{{id}}.verdicts.json')); "
                f"v=v.get('verdicts',v) if isinstance(v,dict) else v; ids={{x.get('id') for x in v}}; "
                f"src={{x['id'] for x in json.load(open('{src}'))}}; "
                f"noev=[x['id'] for x in v if x.get('disposition') in ('landed','partial','absent') and not x.get('evidence')]; "
                f"missing=src-ids; print('rows',len(v),'expected',len(src),'missing',len(missing),'noevidence',len(noev)); "
                f'sys.exit(0 if not missing and not noev else 1)"'
            )
            body += cut(
                lane,
                "Konfrontacja",
                agent,
                model,
                "intents",
                "read",
                "allow-report-only",
                prompt,
                verify,
                "missing 0 noevidence 0",
            )
        name = f"intents-confront.{host}.dispatch.toml"

    elif a.stage == "implement":
        q = read_json(work / "queue.json")
        items = q["queue"][: a.limit] if a.limit else q["queue"]
        if not items:
            die("queue is empty — nothing to plan")
        common = (
            "TRYB: WRITE, jedno cięcie = jedna intencja Foundera. Cytat poniżej to jego słowa, nie parafraza.\n"
            "Repo: {repo} (worktree cutu). loctree-mcp project={repo} przed edycją: slice/impact.\n"
            "Bramki repo muszą być zielone; czerwona bramka to nasza bramka, nie 'pre-existing'.\n"
            "Commit z trailerem `Authored-By: {agent} <agents@vetcoders.io>`. Nie mergujesz, nie pushujesz do trunka.\n"
            "Raport końcowy: co wylądowało (plik:linia), jak sprawdzić ze ścieżki użytkownika, czego świadomie nie zrobiłeś."
        )
        body = plan_header(
            f"intents-implement-{host}",
            "Wdrożenie stabilnie otwartych intencji Foundera z kolejki vc-intents.",
            repo,
            reports,
            plans / f"intents-implement-{host}.tracker.md",
            a.concurrency,
            True,
            a.timeout_min,
            common,
        )
        body += '\n[[phases]]\ntitle = "Wdrożenie"\ndetail = "Jedno cięcie na intencję, worktree per cut, verifier = bramki repo."\n'
        for it in items:
            agent, model = agents.get(it["id"], agents["*"])
            prompt = (
                f"Intencja {it['id']} ({it['date']}, siła {it['strength']}/5, obszar {it['subject']}, dyspozycja przed cięciem: {it['disposition']}).\n"
                f"Founder powiedział: „{it['quote']}”\nTemat: {it['topic']}\n"
                + (
                    f"Co dziś jest / czego brakuje wg floty: {it.get('gap', '')}\n"
                    if it.get("gap")
                    else ""
                )
                + (
                    f"Dowód floty: {it.get('evidence', '')}\n"
                    if it.get("evidence")
                    else ""
                )
                + "Kryterium: zachowanie osiągalne ze ścieżki, której użyłby Founder; nie sam symbol.\n"
                "Jeśli intencja wymaga guzika Foundera (merge, deploy, sekrety, kasowanie danych) — zatrzymaj się i opisz, nie naciskaj."
            )
            body += cut(
                it["id"],
                "Wdrożenie",
                agent,
                model,
                "implement",
                "write",
                "allow",
                prompt,
                a.gate if a.gate else None,
                a.gate_expect or None,
            )
        name = f"intents-implement.{host}.dispatch.toml"
    else:
        die(f"unknown stage {a.stage}")

    path = plans / name
    path.write_text(body)
    print(f"plan → {path}")
    print(f"doctor:  vibecrafted dispatch {path} --doctor --json")
    print(f"dry-run: vibecrafted dispatch {path} --dry-run --json")


# ----------------------------------------------------------------------------
# merge — per-host verdicts → LEDGER.json
# ----------------------------------------------------------------------------


def load_verdicts(dir_: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in sorted(dir_.glob("*.verdicts.json")):
        v = read_json(p)
        v = v.get("verdicts", v) if isinstance(v, dict) else v
        for row in v:
            if row.get("id"):
                out[row["id"]] = row
    return out


def cmd_merge(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    intents = read_json(work / "intents.json")
    hosts: dict[str, dict[str, dict]] = {}
    for spec in a.verdicts:
        host, _, d = spec.partition("=")
        if not d:
            die(f"--verdicts wants host=<dir>, got {spec!r}")
        hosts[host] = load_verdicts(Path(d).expanduser())
    primary = a.primary or next(iter(hosts))
    if primary not in hosts:
        die(f"primary host {primary!r} not among {list(hosts)}")

    existing = read_json(ledger_path(work)) if ledger_path(work).exists() else {}
    keep = {i["id"]: i for i in existing.get("intents", [])}
    rows = []
    for it in intents:
        prev = keep.get(it["id"], {})
        verdicts = {}
        for host, vs in hosts.items():
            v = vs.get(it["id"])
            if v:
                verdicts[host] = {
                    "disposition": v.get("disposition"),
                    "evidence": v.get("evidence", ""),
                    "gap": v.get("gap", ""),
                    "superseded_by": v.get("superseded_by", ""),
                    "conflicts_with": v.get("conflicts_with", ""),
                }
        rows.append(
            {
                **it,
                "verdicts": verdicts,
                "primary": primary,
                "after": prev.get("after", []),
                "risk": prev.get("risk", "none"),
                "flow": prev.get("flow", "peripheral"),
                "notes": prev.get("notes", []),
                "runtime_verified": prev.get("runtime_verified", "unknown"),
            }
        )

    ledger = {
        "schema": SCHEMA,
        "project": a.project or existing.get("project", ""),
        "repo": a.repo or existing.get("repo", ""),
        "created": existing.get("created", now()),
        "updated": now(),
        "hosts": sorted(hosts),
        "primary": primary,
        "sources": existing.get("sources", []),
        "intents": rows,
    }
    apply_overrides(ledger, read_jsonl(overrides_path(work)))
    write_json(ledger_path(work), ledger)

    # Agreement report: the only cheap falsifier a single fleet cannot give you.
    pairs = [
        (h1, h2) for i, h1 in enumerate(sorted(hosts)) for h2 in sorted(hosts)[i + 1 :]
    ]
    rep = {
        "hosts": sorted(hosts),
        "per_host_rows": {h: len(v) for h, v in hosts.items()},
        "pairs": {},
    }
    for h1, h2 in pairs:
        both = [r for r in rows if h1 in r["verdicts"] and h2 in r["verdicts"]]
        same = [
            r
            for r in both
            if r["verdicts"][h1]["disposition"] == r["verdicts"][h2]["disposition"]
        ]
        diff = Counter(
            f"{r['verdicts'][h1]['disposition']}→{r['verdicts'][h2]['disposition']}"
            for r in both
            if r["verdicts"][h1]["disposition"] != r["verdicts"][h2]["disposition"]
        )
        rep["pairs"][f"{h1}|{h2}"] = {
            "compared": len(both),
            "agree": len(same),
            "agreement": round(len(same) / len(both), 3) if both else None,
            "top_disagreements": diff.most_common(8),
        }
    write_json(work / "replication-report.json", rep)
    print(
        f"ledger → {ledger_path(work)}  ({len(rows)} intents, hosts {sorted(hosts)}, primary {primary})"
    )
    for k, v in rep["pairs"].items():
        print(f"  {k}: {v['agree']}/{v['compared']} agree ({v['agreement']})")
    missing = [(h, len([r for r in rows if h not in r["verdicts"]])) for h in hosts]
    for h, n in missing:
        if n:
            print(
                f"  {h}: {n} intents without a verdict (lane not delivered?) — they stay open",
                file=sys.stderr,
            )


# ----------------------------------------------------------------------------
# effective disposition = overrides (human > rule) over the primary verdict
# ----------------------------------------------------------------------------


def apply_overrides(ledger: dict, overrides: list[dict]) -> None:
    by_id: dict[str, list[dict]] = defaultdict(list)
    for o in overrides:
        by_id[o["id"]].append(o)
    for it in ledger["intents"]:
        hosts = ledger["hosts"]
        eff: dict[str, str | None] = {
            h: (it["verdicts"].get(h) or {}).get("disposition") for h in hosts
        }
        human = None
        for o in by_id.get(it["id"], []):
            if o.get("by") == "rule":
                targets = hosts if o.get("host") in (None, "*") else [o["host"]]
                for h in targets:
                    if eff.get(h) == o.get("from"):
                        eff[h] = o["to"]
            else:
                human = o
        it["effective_by_host"] = eff
        base = eff.get(ledger["primary"])
        it["stable"] = (
            len(hosts) > 1
            and len({v for v in eff.values() if v}) == 1
            and all(eff.values())
        )
        it["disposition"] = human["to"] if human else base
        it["decided_by"] = (
            human.get("by")
            if human
            else (
                "rule"
                if base
                != (it["verdicts"].get(ledger["primary"]) or {}).get("disposition")
                else None
            )
        )
        it["overrides"] = by_id.get(it["id"], [])


def cmd_reclassify(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    ledger = load_ledger(work)
    existing = read_jsonl(overrides_path(work))
    have = {
        (o["id"], o.get("host"), o.get("rule"))
        for o in existing
        if o.get("by") == "rule"
    }
    new: list[dict] = []
    for it in ledger["intents"]:
        for host, v in it["verdicts"].items():
            if v.get("disposition") != "landed":
                continue
            ev = v.get("evidence") or ""
            if (
                it["kind"] == "complaint"
                and (it["id"], host, "complaint∧landed") not in have
            ):
                new.append(
                    {
                        "id": it["id"],
                        "host": host,
                        "from": "landed",
                        "to": "landed-contested",
                        "rule": "complaint∧landed",
                        "by": "rule",
                        "at": today(),
                        "reason": "Founder mówi, że nie działa; flota znalazła kod. Runtime niezweryfikowany — skarga wygrywa do czasu sondy.",
                        "evidence": ev,
                    }
                )
            elif (
                not CODE_EXT.search(ev)
                and (it["id"], host, "docs-evidence-only") not in have
            ):
                new.append(
                    {
                        "id": it["id"],
                        "host": host,
                        "from": "landed",
                        "to": "landed-by-doc",
                        "rule": "docs-evidence-only",
                        "by": "rule",
                        "at": today(),
                        "reason": "Jedyny dowód to dokument lub brak pliku kodu. Dokument agenta = claim wykonania w przebraniu.",
                        "evidence": ev,
                    }
                )
    append_jsonl(overrides_path(work), new)
    apply_overrides(ledger, existing + new)
    ledger["updated"] = now()
    write_json(ledger_path(work), ledger)
    c = Counter(o["to"] for o in new)
    landed_before = sum(
        1
        for it in ledger["intents"]
        for v in it["verdicts"].values()
        if v.get("disposition") == "landed"
    )
    print(f"reclassify: +{len(new)} overrides → {overrides_path(work)}  ({dict(c)})")
    if landed_before:
        print(
            f"  {len(new)}/{landed_before} host-level `landed` verdicts fell to the two rules ({len(new) / landed_before:.0%})"
        )


def cmd_decide(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    ledger = load_ledger(work)
    rows: list[dict] = []
    if a.import_file:
        p = Path(a.import_file).expanduser()
        raw = read_jsonl(p) if p.suffix == ".jsonl" else read_json(p)
        if isinstance(raw, dict):  # a dump of the review page's decisions collection
            raw = [{"id": k, **v} for k, v in raw.items()]
        for r in raw:
            to = (
                "drop"
                if r.get("verdict") == "drop"
                else r.get("to") or r.get("disposition")
            )
            if not to:
                continue
            rows.append(
                {
                    "id": r["id"],
                    "host": "*",
                    "from": r.get("from") or r.get("from_div0"),
                    "to": to,
                    "by": r.get("by") or a.by,
                    "reason": r.get("reason") or r.get("note") or "",
                    "verdict": r.get("verdict"),
                    "at": r.get("at") or today(),
                }
            )
    else:
        if not (a.id and a.to):
            die("decide wants --id and --to, or --import-file")
        cur = next((it for it in ledger["intents"] if it["id"] == a.id), None)
        if not cur:
            die(f"no intent {a.id}")
        rows.append(
            {
                "id": a.id,
                "host": "*",
                "from": cur.get("disposition"),
                "to": a.to,
                "by": a.by,
                "reason": a.reason or "",
                "at": today(),
            }
        )
    for r in rows:
        if r["to"] not in DISPOSITIONS:
            die(f"{r['id']}: unknown disposition {r['to']!r}")
    append_jsonl(overrides_path(work), rows)
    apply_overrides(ledger, read_jsonl(overrides_path(work)))
    ledger["updated"] = now()
    write_json(ledger_path(work), ledger)
    print(f"decide: +{len(rows)} human overrides by {a.by} → {overrides_path(work)}")


# ----------------------------------------------------------------------------
# queue — stable, still open, ordered
# ----------------------------------------------------------------------------


def order(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Kahn over `after`, then risk, flow, strength desc, date asc, id."""
    by_id = {i["id"]: i for i in items}
    pending = {
        k: {d for d in v.get("after", []) if d in by_id} for k, v in by_id.items()
    }

    def key(k: str):
        it = by_id[k]
        return (
            RISKS.index(it.get("risk", "none")),
            FLOWS.index(it.get("flow", "peripheral")),
            -int(it.get("strength", 3)),
            it.get("date", ""),
            k,
        )

    out: list[dict] = []
    while True:
        ready = sorted((k for k, v in pending.items() if not v), key=key)
        if not ready:
            break
        for k in ready:
            out.append(by_id[k])
            del pending[k]
            for deps in pending.values():
                deps.discard(k)
    return out, sorted(pending)


def cmd_queue(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    ledger = load_ledger(work)
    multi = len(ledger["hosts"]) > 1
    pool = [
        it
        for it in ledger["intents"]
        if it.get("disposition") in ACTIONABLE
        and int(it["strength"]) >= a.min_strength
        and (
            it.get("stable") or not multi or it.get("decided_by") not in (None, "rule")
        )
    ]
    queue, cyclic = order(pool)
    rows = [
        {
            k: it.get(k)
            for k in (
                "id",
                "date",
                "quote",
                "topic",
                "kind",
                "subject",
                "strength",
                "disposition",
                "risk",
                "flow",
                "after",
                "decided_by",
            )
        }
        | {
            "evidence": (it["verdicts"].get(ledger["primary"]) or {}).get(
                "evidence", ""
            ),
            "gap": (it["verdicts"].get(ledger["primary"]) or {}).get("gap", ""),
        }
        for it in queue
    ]
    write_json(
        work / "queue.json",
        {
            "schema": "vc-intents.queue.v1",
            "at": now(),
            "min_strength": a.min_strength,
            "stable_only": multi,
            "queue": rows,
            "cyclic": cyclic,
        },
    )
    md = [
        f"# Stable open queue — {ledger.get('project') or ledger.get('repo')}",
        "",
        f"{len(rows)} intents · strength ≥ {a.min_strength} · "
        + ("both hosts agree or a human decided" if multi else "single host")
        + f" · {now()}",
        "",
    ]
    for d in sorted(ACTIONABLE):
        n = sum(1 for r in rows if r["disposition"] == d)
        if n:
            md.append(f"- {d}: {n}")
    md += [
        "",
        "| # | id | date | siła | dyspozycja | temat | cytat |",
        "|---|---|---|---|---|---|---|",
    ]
    for n, r in enumerate(rows, 1):
        q = r["quote"].replace("|", "\\|")
        md.append(
            f"| {n} | {r['id']} | {r['date']} | {r['strength']} | {r['disposition']} | {r['topic']} | {q[:160]}{'…' if len(q) > 160 else ''} |"
        )
    if cyclic:
        md += ["", f"> unschedulable (dependency cycle): {', '.join(cyclic)}"]
    md += ["", "_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_", ""]
    (work / "STABLE-QUEUE.md").write_text("\n".join(md))
    print(
        f"queue → {work / 'queue.json'} · {work / 'STABLE-QUEUE.md'}  ({len(rows)} intents)"
    )
    if cyclic:
        print(f"UNSCHEDULABLE (cycle): {', '.join(cyclic)}", file=sys.stderr)


# ----------------------------------------------------------------------------
# render — LEDGER.md and the review page
# ----------------------------------------------------------------------------


def context_for(it: dict, roots: list[Path], width: int = 1800) -> str:
    p = source_path(roots, it["source"])
    if not p:
        return ""
    t = p.read_text(errors="ignore").replace("�", "").strip()
    if len(t) <= width:
        return t
    i = t.find(it["quote"][:40])
    a0 = max(0, (max(i, 0)) - width // 3)
    return (
        ("…" if a0 else "") + t[a0 : a0 + width] + ("…" if a0 + width < len(t) else "")
    )


def cmd_render(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    ledger = load_ledger(work)
    hosts = ledger["hosts"]
    counts = Counter(it.get("disposition") for it in ledger["intents"])
    md = [
        f"# Intent ledger — {ledger.get('project') or ledger.get('repo')}",
        "",
        f"- hosts: {', '.join(hosts)} (primary {ledger['primary']}) · updated {ledger['updated']}",
        f"- intents: {len(ledger['intents'])} · overrides: {len(read_jsonl(overrides_path(work)))}",
        "",
        "## Dispositions (effective)",
        "",
    ]
    md += [f"- {d}: {counts[d]}" for d in DISPOSITIONS if counts.get(d)]
    if counts.get(None):
        md.append(f"- (no verdict): {counts[None]}")
    md += ["", "## Intents", ""]
    for it in sorted(ledger["intents"], key=lambda i: (i["date"], i["id"])):
        md += [
            f"### {it['id']} — {it['topic']}",
            "",
            f"> {it['quote']}",
            "",
            f"- {it['date']} · {it['kind']} · {it['subject']} · strength {it['strength']} · **{it.get('disposition')}**"
            + (" · stable" if it.get("stable") else "")
            + (f" · decided by {it['decided_by']}" if it.get("decided_by") else ""),
        ]
        for h in hosts:
            v = it["verdicts"].get(h)
            if v:
                md.append(
                    f"- {h}: {v['disposition']} — {v.get('evidence') or '—'}"
                    + (f" · gap: {v['gap']}" if v.get("gap") else "")
                )
            else:
                md.append(f"- {h}: (no verdict)")
        for o in it.get("overrides", []):
            md.append(
                f"- override [{o.get('by')}] {o.get('from')} → {o.get('to')}: {o.get('reason', '')}"
            )
        md.append("")
    md += ["_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_", ""]
    (work / "LEDGER.md").write_text("\n".join(md))
    print(f"render → {work / 'LEDGER.md'}")

    if a.html:
        tpl = ASSETS / "intents.html"
        if not tpl.exists():
            die(f"template missing: {tpl}")
        roots = [Path(r).expanduser() for r in a.transcripts]
        roots += (
            [p for p in (work / "shards").glob("s*") if p.is_dir()]
            if (work / "shards").exists()
            else []
        )
        h0 = ledger["primary"]
        h1 = next((h for h in hosts if h != h0), None)
        data = []
        for it in ledger["intents"]:
            v0, v1 = (
                it["verdicts"].get(h0) or {},
                (it["verdicts"].get(h1) or {}) if h1 else {},
            )
            e = it.get("effective_by_host", {})
            data.append(
                {
                    "id": it["id"],
                    "date": it["date"],
                    "src": it["source"],
                    "quote": it["quote"],
                    "ctx": context_for(it, roots) if roots else "",
                    "topic": it["topic"],
                    "kind": it["kind"],
                    "subject": it["subject"],
                    "strength": it["strength"],
                    "div0": e.get(h0) or v0.get("disposition"),
                    "dragon": e.get(h1) if h1 else None,
                    "stable": it.get("stable", False),
                    "ev0": v0.get("evidence", ""),
                    "ev1": v1.get("evidence", ""),
                    "gap0": v0.get("gap", ""),
                    "gap1": v1.get("gap", ""),
                    "ov": [o for o in it.get("overrides", []) if o.get("by") == "rule"],
                    "decision": next(
                        (
                            {
                                "disposition": o["to"],
                                "verdict": o.get("verdict"),
                                "note": o.get("reason"),
                                "at": o.get("at"),
                                "by": o.get("by"),
                            }
                            for o in reversed(it.get("overrides", []))
                            if o.get("by") != "rule"
                        ),
                        None,
                    ),
                }
            )
        page = tpl.read_text()
        page = page.replace(
            "__DATA__",
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
                "</", "<\\/"
            ),
        )
        page = page.replace("__HOST0__", h0).replace("__HOST1__", h1 or "—")
        page = page.replace("__REPO_BLOB_BASE__", a.blob_base or "").replace(
            "__PROJECT__", ledger.get("project") or ledger.get("repo") or ""
        )
        out = work / "intents.html"
        out.write_text(page)
        print(f"render → {out}  ({len(data)} rows, context {'on' if roots else 'off'})")


# ----------------------------------------------------------------------------
# status — closure verdict
# ----------------------------------------------------------------------------


def cmd_status(a: argparse.Namespace) -> None:
    work: Path = a.workdir
    ledger = load_ledger(work)
    counts = Counter(it.get("disposition") for it in ledger["intents"])
    print(
        f"{ledger.get('project') or ledger.get('repo')}  ({len(ledger['intents'])} intents, hosts {ledger['hosts']})"
    )
    for d in DISPOSITIONS:
        if counts.get(d):
            print(f"  {d:<18} {counts[d]:>5}")
    if counts.get(None):
        print(f"  {'(no verdict)':<18} {counts[None]:>5}")
    reasons = [f"{counts[d]} intent(s) {d}" for d in sorted(OPEN) if counts.get(d)]
    if counts.get(None):
        reasons.append(f"{counts[None]} intent(s) without any verdict")
    sweep = work / "sweep.json"
    if sweep.exists():
        for s in read_json(sweep)["sources"]:
            if s["state"] == "unavailable":
                reasons.append(
                    f"source unavailable: {s['source']} ({s.get('reason', '')})"
                )
            if s.get("truncated"):
                reasons.append(f"source truncated: {s['source']}")
    unverified = sum(
        1
        for it in ledger["intents"]
        if it.get("disposition") == "landed" and it.get("runtime_verified") != "yes"
    )
    if unverified:
        reasons.append(
            f"{unverified} landed intent(s) with runtime_verified != yes (landed is a lower bound)"
        )
    print(f"\nverdict: {'CLOSED' if not reasons else 'OPEN'}")
    for r in reasons:
        print(f"  - {r}")
    sys.exit(0 if not reasons else 1)


# ----------------------------------------------------------------------------
# argparse
# ----------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "workdir",
        type=Path,
        help="working directory, e.g. ~/.vibecrafted/artifacts/<owner>/<repo>/intents",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sweep", help="account for sources in a window")
    p.add_argument(
        "--source",
        action="append",
        required=True,
        help="dir:<path> or aicx; repeatable",
    )
    p.add_argument("--since", help="YYYY-MM-DD inclusive")
    p.add_argument("--until", help="YYYY-MM-DD inclusive")
    p.add_argument("--repo", help="repo path (needed for aicx)")
    p.add_argument("--aicx-hours", type=int, default=8764)
    p.add_argument(
        "--shard-size",
        type=int,
        default=120,
        help="files per extraction shard; 0 = no shards",
    )
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser(
        "verify-quotes", help="merge extraction shards; reject non-verbatim quotes"
    )
    p.add_argument(
        "--extractions",
        required=True,
        help="dir of <shard>.json from the extraction fleet",
    )
    p.add_argument(
        "--transcripts",
        action="append",
        default=[],
        help="transcript root(s) to verify against; repeatable",
    )
    p.add_argument(
        "--strict", action="store_true", help="exit 1 when anything was rejected"
    )
    p.set_defaults(fn=cmd_verify_quotes)

    p = sub.add_parser("lanes", help="split the catalog into confrontation lanes")
    p.add_argument(
        "--lane",
        action="append",
        default=[],
        help="NAME=subject,subject; repeatable; default = six Codescribe lanes",
    )
    p.set_defaults(fn=cmd_lanes)

    p = sub.add_parser("plan", help="write a vibecrafted.dispatch.v1 plan")
    p.add_argument(
        "--stage", required=True, choices=("extract", "confront", "implement")
    )
    p.add_argument("--repo", required=True)
    p.add_argument(
        "--artifacts",
        help="artifact dir; default $VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>",
    )
    p.add_argument(
        "--host", help="host label in plan/report names; default = this machine"
    )
    p.add_argument(
        "--agent",
        action="append",
        default=[],
        help="LANE_OR_ID=agent:model, or *=agent:model; repeatable",
    )
    p.add_argument("--default-agent", default="codex")
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--timeout-min", type=int, default=45)
    p.add_argument("--limit", type=int, help="implement: first N of the queue")
    p.add_argument("--gate", help="implement: verifier command per cut (repo gates)")
    p.add_argument(
        "--gate-expect", help="implement: expected substring of the gate output"
    )
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("merge", help="fold per-host verdicts into LEDGER.json")
    p.add_argument(
        "--verdicts",
        action="append",
        required=True,
        help="host=<dir with *.verdicts.json>; repeatable",
    )
    p.add_argument(
        "--primary", help="host whose verdict is the base disposition; default first"
    )
    p.add_argument("--project")
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_merge)

    p = sub.add_parser("reclassify", help="apply the two mechanical rules to `landed`")
    p.set_defaults(fn=cmd_reclassify)

    p = sub.add_parser("decide", help="append a human override")
    p.add_argument("--id")
    p.add_argument("--to", choices=DISPOSITIONS)
    p.add_argument("--reason")
    p.add_argument("--by", default=os.environ.get("USER", "founder"))
    p.add_argument(
        "--import-file",
        help="review export: .jsonl of overrides or .json dump of decisions",
    )
    p.set_defaults(fn=cmd_decide)

    p = sub.add_parser("queue", help="stable open queue")
    p.add_argument("--min-strength", type=int, default=4)
    p.set_defaults(fn=cmd_queue)

    p = sub.add_parser("render", help="LEDGER.md (+ intents.html with --html)")
    p.add_argument("--html", action="store_true")
    p.add_argument(
        "--transcripts",
        action="append",
        default=[],
        help="transcript root(s) for the wider excerpt",
    )
    p.add_argument(
        "--blob-base",
        help="e.g. https://github.com/<org>/<repo>/blob/<sha>/ for evidence links",
    )
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("status", help="closure verdict; exit 0 closed / 1 open")
    p.set_defaults(fn=cmd_status)

    a = ap.parse_args()
    a.workdir = a.workdir.expanduser()
    a.fn(a)


if __name__ == "__main__":
    main()
