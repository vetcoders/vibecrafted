#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
GitHub Repository Transfer Tool
================================

Universal CLI for repository management: list, transfer, delete, clean transfer.
Generates interactive HTML forms and can execute bulk operations from JSON.

Usage:
    # Generate HTML form and serve it (recommended)
    ./repo-transfer.py <source-owner> LibraxisAI Loctree Vetcoders

    # Generate HTML and open in browser
    ./repo-transfer.py <source-owner> LibraxisAI --open

    # Execute operations from JSON
    ./repo-transfer.py --execute cleanup.json

    # List repos (non-interactive)
    ./repo-transfer.py --list <source-owner> --json
    ./repo-transfer.py --list <source-owner> --filter "cli-*"

    # Direct operations
    ./repo-transfer.py --delete owner/repo --yes
    ./repo-transfer.py --transfer owner/repo --to NewOwner --yes
    ./repo-transfer.py --transfer owner/repo --to NewOwner --clean --yes

Created by Vetcoders (c)2026
"""

from __future__ import annotations

import argparse
import fnmatch
import http.server
import json
import re
import socketserver
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

# =============================================================================
# ANSI Colors
# =============================================================================


class C:
    """ANSI color/style codes used for CLI output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def success(msg: str):
    """Print ``msg`` prefixed with a green checkmark."""
    print(f"  {C.GREEN}✔{C.RESET} {msg}")


def error(msg: str):
    """Print ``msg`` prefixed with a red cross."""
    print(f"  {C.RED}✖{C.RESET} {msg}")


def warning(msg: str):
    """Print ``msg`` prefixed with a yellow warning glyph."""
    print(f"  {C.YELLOW}⚠{C.RESET} {msg}")


def info(msg: str):
    """Print ``msg`` prefixed with a blue info glyph."""
    print(f"  {C.BLUE}ℹ{C.RESET} {msg}")


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class Repository:
    """A GitHub repository record as fetched from ``gh repo list``."""

    name: str
    full_name: str
    owner: str
    is_private: bool = False
    is_fork: bool = False
    is_archived: bool = False
    stars: int = 0
    url: str = ""
    description: str = ""
    pushed_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to the camelCase JSON shape consumed by the HTML form's JS."""
        return {
            "name": self.name,
            "full_name": self.full_name,
            "owner": self.owner,
            "isPrivate": self.is_private,
            "isFork": self.is_fork,
            "isArchived": self.is_archived,
            "stargazerCount": self.stars,
            "url": self.url,
            "description": self.description,
            "pushedAt": self.pushed_at,
        }


# =============================================================================
# GitHub CLI Wrapper
# =============================================================================


def run_gh(args: list[str]) -> tuple[int, str, str]:
    """Run the ``gh`` CLI with ``args``; returns ``(returncode, stdout, stderr)``.

    Returns ``(1, "", "gh CLI not found")`` when ``gh`` is not on PATH instead
    of raising.
    """
    try:
        result = subprocess.run(
            ["gh"] + args, capture_output=True, text=True, check=False
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 1, "", "gh CLI not found"


def check_gh_auth() -> bool:
    """Return True when ``gh auth status`` succeeds (CLI authenticated)."""
    code, _, _ = run_gh(["auth", "status"])
    return code == 0


def fetch_repos(owner: str) -> list[Repository]:
    """Fetch all repos for owner using gh repo list (includes private)."""
    code, out, _ = run_gh(
        [
            "repo",
            "list",
            owner,
            "--limit",
            "500",
            "--json",
            "name,isPrivate,isFork,isArchived,stargazerCount,url,description,pushedAt",
        ]
    )
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
        repos = []
        for r in data:
            repos.append(
                Repository(
                    name=r.get("name", ""),
                    full_name=f"{owner}/{r.get('name', '')}",
                    owner=owner,
                    is_private=r.get("isPrivate", False),
                    is_fork=r.get("isFork", False),
                    is_archived=r.get("isArchived", False),
                    stars=r.get("stargazerCount", 0),
                    url=r.get("url", ""),
                    description=r.get("description", "") or "",
                    pushed_at=r.get("pushedAt", "") or "",
                )
            )
        return repos
    except json.JSONDecodeError:
        return []


def check_repo_exists(repo: str) -> bool:
    """Return True when ``gh repo view`` resolves ``repo`` (``owner/name``)."""
    code, _, _ = run_gh(["repo", "view", repo])
    return code == 0


def delete_repo(repo: str) -> tuple[bool, str]:
    """Delete ``repo`` via ``gh repo delete --yes``; returns ``(ok, message)``."""
    code, _out, err = run_gh(["repo", "delete", repo, "--yes"])
    return code == 0, err.strip() if code != 0 else "Deleted"


def transfer_repo(
    source: str, target_owner: str, new_name: str | None = None
) -> tuple[bool, str]:
    """Transfer ``source`` to ``target_owner`` via the GitHub API, keeping fork lineage."""
    args = [
        "api",
        f"repos/{source}/transfer",
        "-X",
        "POST",
        "-f",
        f"new_owner={target_owner}",
    ]
    if new_name:
        args.extend(["-f", f"new_name={new_name}"])
    code, _out, err = run_gh(args)
    return code == 0, err.strip() if code != 0 else "Transferred"


def set_repo_visibility(repo: str, private: bool) -> tuple[bool, str]:
    """Set ``repo`` visibility to private or public via ``gh repo edit``."""
    visibility = "private" if private else "public"
    code, _, err = run_gh(["repo", "edit", repo, f"--visibility={visibility}"])
    return code == 0, err.strip() if code != 0 else f"Set to {visibility}"


def clean_transfer(
    source: str,
    target_owner: str,
    new_name: str | None = None,
    visibility: str | None = None,
    delete_source: bool = False,
) -> tuple[bool, str]:
    """
    Clean transfer using bare clone + mirror push.
    Breaks fork relationships and creates a fresh copy.
    """
    _source_owner, source_repo = source.split("/")
    final_name = new_name or source_repo
    target_full = f"{target_owner}/{final_name}"

    with tempfile.TemporaryDirectory() as tmpdir:
        bare_path = Path(tmpdir) / f"{source_repo}.git"

        # 1. Create empty repo on target
        vis_flag = (
            f"--{visibility}" if visibility in ("public", "private") else "--private"
        )
        code, _, err = run_gh(["repo", "create", target_full, vis_flag, "--confirm"])
        if code != 0 and "already exists" not in err.lower():
            return False, f"Failed to create target repo: {err}"

        # 2. Bare clone source
        result = subprocess.run(
            [
                "git",
                "clone",
                "--bare",
                f"https://github.com/{source}.git",
                str(bare_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False, f"Failed to clone source: {result.stderr}"

        # 3. Mirror push to target
        result = subprocess.run(
            ["git", "push", "--mirror", f"https://github.com/{target_full}.git"],
            cwd=str(bare_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False, f"Failed to push to target: {result.stderr}"

        # 4. Delete source if requested
        if delete_source:
            ok, msg = delete_repo(source)
            if not ok:
                return True, f"Transferred but failed to delete source: {msg}"

    return True, f"Clean transferred to {target_full}"


# =============================================================================
# Filtering
# =============================================================================


def filter_repos(repos: list[Repository], pattern: str) -> list[Repository]:
    """Filter ``repos`` by name: ``~regex``, glob (``*``/``?``), or plain substring."""
    if not pattern:
        return repos
    pattern = pattern.strip()
    if pattern.startswith("~"):
        try:
            regex = re.compile(pattern[1:], re.IGNORECASE)
            return [r for r in repos if regex.search(r.name)]
        except re.error:
            return repos
    if "*" in pattern or "?" in pattern:
        return [r for r in repos if fnmatch.fnmatch(r.name.lower(), pattern.lower())]
    return [r for r in repos if pattern.lower() in r.name.lower()]


# =============================================================================
# HTML Form Generator
# =============================================================================


def generate_html(
    repos: list[Repository], orgs: list[str], timestamp: str, port: int | None = None
) -> str:
    """Render the self-contained interactive HTML form embedding ``repos`` as JSON.

    When ``port`` is set, the page polls ``/api/repos`` on that port for a
    live refresh button; otherwise it is a static snapshot.
    """
    repos_json = json.dumps([r.to_dict() for r in repos])
    orgs_options = "".join(f"<option>{o}</option>" for o in orgs)

    refresh_script = ""
    if port:
        refresh_script = f"""
        async function refreshData() {{
            try {{
                const resp = await fetch('http://localhost:{port}/api/repos');
                const data = await resp.json();
                repos.length = 0;
                repos.push(...data);
                init();
                document.querySelector('.refresh-info').textContent = '🕐 ' + new Date().toLocaleString();
            }} catch(e) {{ console.error('Refresh failed:', e); }}
        }}"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Repo Transfer - Vetcoders</title>
    <style>
        :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9; --dim:#8b949e; --red:#f85149; --green:#3fb950; --blue:#58a6ff; --yellow:#d29922; --purple:#a371f7; --orange:#f0883e; }}
        * {{ box-sizing:border-box; margin:0; padding:0; }}
        body {{ font-family:-apple-system,sans-serif; background:var(--bg); color:var(--text); padding:15px; }}
        .header {{ text-align:center; padding:20px 0; border-bottom:1px solid var(--border); margin-bottom:20px; }}
        .header h1 {{ font-size:1.8rem; }}
        .refresh-info {{ background:var(--card); padding:8px 15px; border-radius:6px; display:inline-block; margin-top:10px; font-size:0.8rem; color:var(--dim); }}
        .stats {{ display:flex; justify-content:center; gap:15px; margin:15px 0; flex-wrap:wrap; }}
        .stat {{ text-align:center; padding:10px 18px; background:var(--card); border-radius:8px; border:1px solid var(--border); cursor:pointer; }}
        .stat:hover {{ border-color:var(--dim); }}
        .stat-value {{ font-size:1.6rem; font-weight:bold; }}
        .stat-label {{ color:var(--dim); font-size:0.75rem; }}
        .stat.delete .stat-value {{ color:var(--red); }}
        .stat.keep .stat-value {{ color:var(--green); }}
        .stat.transfer .stat-value {{ color:var(--blue); }}
        .stat.clean .stat-value {{ color:var(--purple); }}
        .controls {{ display:flex; gap:8px; justify-content:center; margin:15px 0; flex-wrap:wrap; }}
        button {{ padding:8px 16px; border-radius:6px; border:1px solid var(--border); background:var(--card); color:var(--text); cursor:pointer; font-size:0.85rem; }}
        button:hover {{ border-color:var(--dim); }}
        button.primary {{ background:var(--green); color:#000; font-weight:bold; }}
        button.danger {{ background:var(--red); color:#fff; }}
        .filter-bar {{ display:flex; gap:8px; margin:15px auto; justify-content:center; flex-wrap:wrap; }}
        .filter-bar input,.filter-bar select {{ padding:6px 12px; border-radius:5px; border:1px solid var(--border); background:var(--card); color:var(--text); font-size:0.85rem; }}
        .filter-bar input {{ width:200px; }}
        .org-section {{ margin:20px auto; max-width:1400px; }}
        .org-header {{ background:var(--card); padding:12px 20px; border-radius:8px 8px 0 0; border:1px solid var(--border); border-bottom:none; display:flex; justify-content:space-between; align-items:center; }}
        .org-header h2 {{ font-size:1.1rem; }}
        .org-header .count {{ background:var(--border); padding:2px 8px; border-radius:10px; font-size:0.8rem; margin-left:8px; }}
        .org-repos {{ border:1px solid var(--border); border-radius:0 0 8px 8px; }}
        .repo-item {{ display:grid; grid-template-columns:1fr auto auto; gap:12px; padding:10px 15px; border-bottom:1px solid var(--border); align-items:center; font-size:0.9rem; }}
        .repo-item:last-child {{ border-bottom:none; }}
        .repo-item:hover {{ background:rgba(255,255,255,0.02); }}
        .repo-item.action-delete {{ border-left:3px solid var(--red); background:rgba(248,81,73,0.05); }}
        .repo-item.action-keep {{ border-left:3px solid var(--green); }}
        .repo-item.action-transfer {{ border-left:3px solid var(--blue); }}
        .repo-item.action-clean {{ border-left:3px solid var(--purple); background:rgba(163,113,247,0.05); }}
        .repo-info {{ display:flex; flex-direction:column; gap:2px; min-width:0; }}
        .repo-name {{ font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
        .repo-name a {{ color:var(--blue); text-decoration:none; }}
        .repo-meta {{ display:flex; gap:10px; color:var(--dim); font-size:0.75rem; }}
        .repo-options {{ display:flex; gap:5px; }}
        .repo-options input {{ padding:5px 8px; border-radius:4px; border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.8rem; width:100px; }}
        .repo-options select {{ padding:5px; border-radius:4px; border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.8rem; }}
        .repo-actions {{ display:flex; gap:3px; }}
        .action-btn {{ padding:5px 10px; border-radius:4px; border:2px solid transparent; background:var(--bg); color:var(--dim); cursor:pointer; font-size:0.8rem; }}
        .action-btn:hover {{ color:var(--text); }}
        .action-btn.selected {{ font-weight:bold; }}
        .action-btn.delete.selected {{ background:var(--red); color:#fff; }}
        .action-btn.keep.selected {{ background:var(--green); color:#000; }}
        .action-btn.transfer.selected {{ background:var(--blue); color:#000; }}
        .action-btn.clean.selected {{ background:var(--purple); color:#fff; }}
        .output-section {{ margin-top:30px; padding:15px; background:var(--card); border:1px solid var(--border); border-radius:8px; display:none; }}
        .output-section.visible {{ display:block; }}
        .output-section pre {{ background:var(--bg); padding:12px; border-radius:6px; overflow:auto; font-size:0.8rem; max-height:400px; }}
    </style>
</head>
<body>
<div class="header">
    <h1>🚀 Repo Transfer</h1>
    <p>{" + ".join(orgs)}</p>
    <div class="refresh-info">🕐 {timestamp}</div>
</div>
<div class="stats">
    <div class="stat delete" onclick="filterByAction('delete')"><div class="stat-value" id="count-delete">0</div><div class="stat-label">🗑️ Delete</div></div>
    <div class="stat keep" onclick="filterByAction('keep')"><div class="stat-value" id="count-keep">0</div><div class="stat-label">✓ Keep</div></div>
    <div class="stat transfer" onclick="filterByAction('transfer')"><div class="stat-value" id="count-transfer">0</div><div class="stat-label">→ Transfer</div></div>
    <div class="stat clean" onclick="filterByAction('clean')"><div class="stat-value" id="count-clean">0</div><div class="stat-label">⚡ Clean</div></div>
    <div class="stat" onclick="filterByAction('undecided')"><div class="stat-value" id="count-undecided">0</div><div class="stat-label">❓</div></div>
</div>
<div class="controls">
    <button onclick="selectAllForks('delete')" title="Mark all forked repos for deletion">Forks→Del</button>
    <button onclick="clearAll()">Clear All</button>
    {'<button onclick="refreshData()">🔄 Refresh</button>' if port else ""}
</div>
<div class="filter-bar">
    <input type="text" id="filter" placeholder="Filter by name..." oninput="filterRepos()">
    <select id="filter-org" onchange="filterRepos()"><option value="all">All Orgs</option>{orgs_options}</select>
    <select id="filter-type" onchange="filterRepos()">
        <option value="all">All Types</option>
        <option value="fork">Forks</option>
        <option value="original">Original</option>
        <option value="private">Private</option>
        <option value="public">Public</option>
    </select>
</div>
<div id="org-sections"></div>
<div class="controls" style="margin-top:25px">
    <button class="primary" onclick="generateJSON()">📋 JSON</button>
    <button onclick="generateScript()">📜 Script</button>
    <button onclick="copyOutput()">📋 Copy</button>
</div>
<div class="output-section" id="output-section"><pre id="output"></pre></div>
<script>
let repos = {repos_json};
const decisions = {{}};
const defaultTarget = 'Vetcoders';
{refresh_script}

function init() {{
    const orgs = [...new Set(repos.map(r=>r.owner))];
    document.getElementById('org-sections').innerHTML = orgs.map(org => {{
        const or = repos.filter(r=>r.owner===org).sort((a,b)=>a.name.localeCompare(b.name));
        return `<div class="org-section" data-org="${{org}}">
            <div class="org-header">
                <h2>${{org}}<span class="count">${{or.length}}</span></h2>
                <div>
                    <button onclick="selectAllForOrg('${{org}}','delete')" style="padding:4px 8px;font-size:0.7rem" title="Delete all from ${{org}}">Del</button>
                    <button onclick="selectAllForOrg('${{org}}','keep')" style="padding:4px 8px;font-size:0.7rem" title="Keep all from ${{org}}">Keep</button>
                    <button onclick="selectAllForOrg('${{org}}','clean')" style="padding:4px 8px;font-size:0.7rem" title="Clean transfer all from ${{org}}">Clean→VC</button>
                </div>
            </div>
            <div class="org-repos">${{or.map(repoRow).join('')}}</div>
        </div>`;
    }}).join('');
    updateCounts();
}}

function repoRow(r) {{
    const forkIcon = r.isFork ? '<span style="color:var(--purple)" title="Fork">⑂</span>' : '<span style="color:var(--green)" title="Original">●</span>';
    const visIcon = r.isPrivate ? '<span style="color:var(--orange)" title="Private">🔒</span>' : '<span style="color:var(--green)" title="Public">🌍</span>';
    const stars = r.stargazerCount > 0 ? `<span style="color:var(--yellow)">★${{r.stargazerCount}}</span>` : '';
    const existing = decisions[r.full_name];
    const targetVal = existing?.target || defaultTarget;
    const nameVal = existing?.newName || '';

    return `<div class="repo-item${{existing ? ' action-'+existing.action : ''}}" data-name="${{r.name}}" data-org="${{r.owner}}" data-fork="${{r.isFork}}" data-private="${{r.isPrivate}}" data-full="${{r.full_name}}">
        <div class="repo-info">
            <div class="repo-name"><a href="${{r.url}}" target="_blank">${{r.name}}</a></div>
            <div class="repo-meta">${{forkIcon}} ${{visIcon}} ${{stars}}</div>
        </div>
        <div class="repo-options">
            <input placeholder="New name" id="nn-${{r.owner}}-${{r.name}}" value="${{nameVal}}">
            <select id="tg-${{r.owner}}-${{r.name}}">
                <option ${{targetVal==='Vetcoders'?'selected':''}}>Vetcoders</option>
                <option ${{targetVal==='Szowesgad'?'selected':''}}>Szowesgad</option>
                <option ${{targetVal==='LibraxisAI'?'selected':''}}>LibraxisAI</option>
                <option ${{targetVal==='Loctree'?'selected':''}}>Loctree</option>
            </select>
        </div>
        <div class="repo-actions">
            <button class="action-btn delete${{existing?.action==='delete'?' selected':''}}" onclick="setAction('${{r.full_name}}','delete')" title="Delete repository">🗑️</button>
            <button class="action-btn keep${{existing?.action==='keep'?' selected':''}}" onclick="setAction('${{r.full_name}}','keep')" title="Keep as is">✓</button>
            <button class="action-btn transfer${{existing?.action==='transfer'?' selected':''}}" onclick="setAction('${{r.full_name}}','transfer')" title="Transfer (keeps fork relation)">→</button>
            <button class="action-btn clean${{existing?.action==='clean'?' selected':''}}" onclick="setAction('${{r.full_name}}','clean')" title="Clean transfer (breaks fork)">⚡</button>
        </div>
    </div>`;
}}

function setAction(fn, a) {{
    const [o, n] = fn.split('/');
    const tgEl = document.getElementById('tg-'+o+'-'+n);
    const nnEl = document.getElementById('nn-'+o+'-'+n);
    decisions[fn] = {{
        action: a,
        target: tgEl?.value || defaultTarget,
        newName: nnEl?.value || ''
    }};
    const item = document.querySelector(`.repo-item[data-full="${{fn}}"]`);
    if (item) {{
        item.className = 'repo-item action-' + a;
        item.querySelectorAll('.action-btn').forEach(b =>
            b.classList.toggle('selected', b.classList.contains(a))
        );
    }}
    updateCounts();
}}

function updateCounts() {{
    const c = {{delete:0, keep:0, transfer:0, clean:0}};
    Object.values(decisions).forEach(d => {{ if(d.action) c[d.action]++; }});
    ['delete','keep','transfer','clean'].forEach(a =>
        document.getElementById('count-'+a).textContent = c[a]
    );
    document.getElementById('count-undecided').textContent =
        repos.length - Object.values(decisions).filter(d=>d.action).length;
}}

function selectAllForOrg(org, action) {{
    repos.filter(r => r.owner === org).forEach(r => setAction(r.full_name, action));
}}

function selectAllForks(action) {{
    repos.filter(r => r.isFork).forEach(r => setAction(r.full_name, action));
}}

function clearAll() {{
    Object.keys(decisions).forEach(k => delete decisions[k]);
    init();
}}

function filterByAction(action) {{
    document.querySelectorAll('.repo-item').forEach(item => {{
        const fn = item.dataset.full;
        const d = decisions[fn];
        if (action === 'undecided') {{
            item.style.display = (!d || !d.action) ? '' : 'none';
        }} else {{
            item.style.display = (d?.action === action) ? '' : 'none';
        }}
    }});
    document.querySelectorAll('.org-section').forEach(s =>
        s.style.display = s.querySelectorAll('.repo-item:not([style*="none"])').length ? '' : 'none'
    );
}}

function filterRepos() {{
    const nf = document.getElementById('filter').value.toLowerCase();
    const of = document.getElementById('filter-org').value;
    const tf = document.getElementById('filter-type').value;
    document.querySelectorAll('.repo-item').forEach(item => {{
        let show = item.dataset.name.toLowerCase().includes(nf);
        if (show && of !== 'all') show = item.dataset.org === of;
        if (show && tf !== 'all') {{
            if (tf === 'fork') show = item.dataset.fork === 'true';
            else if (tf === 'original') show = item.dataset.fork !== 'true';
            else if (tf === 'private') show = item.dataset.private === 'true';
            else if (tf === 'public') show = item.dataset.private !== 'true';
        }}
        item.style.display = show ? '' : 'none';
    }});
    document.querySelectorAll('.org-section').forEach(s =>
        s.style.display = s.querySelectorAll('.repo-item:not([style*="none"])').length ? '' : 'none'
    );
}}

function generateJSON() {{
    const output = {{
        generated_at: new Date().toISOString(),
        summary: {{ total: repos.length, delete: 0, keep: 0, transfer: 0, clean: 0 }},
        actions: {{ delete: [], keep: [], transfer: [], clean: [] }}
    }};
    repos.forEach(r => {{
        const d = decisions[r.full_name];
        if (d?.action) {{
            output.summary[d.action]++;
            const entry = {{ repo: r.full_name }};
            if (d.action !== 'delete' && d.action !== 'keep') {{
                entry.target = d.target;
                if (d.newName) entry.newName = d.newName;
            }}
            output.actions[d.action].push(entry);
        }}
    }});
    document.getElementById('output').textContent = JSON.stringify(output, null, 2);
    document.getElementById('output-section').classList.add('visible');
}}

function generateScript() {{
    const script = '#!/bin/bash\\n# Generated by repo-transfer.py\\n\\n';
    let cmds = [];
    repos.forEach(r => {{
        const d = decisions[r.full_name];
        if (!d?.action || d.action === 'keep') return;
        if (d.action === 'delete') {{
            cmds.push(`gh repo delete "${{r.full_name}}" --yes`);
        }} else {{
            const target = d.target + '/' + (d.newName || r.name);
            const clean = d.action === 'clean' ? ' --clean' : '';
            cmds.push(`./repo-transfer.py --transfer "${{r.full_name}}" --to "${{target}}"${{clean}} --yes`);
        }}
    }});
    document.getElementById('output').textContent = script + cmds.join('\\n');
    document.getElementById('output-section').classList.add('visible');
}}

function copyOutput() {{
    navigator.clipboard.writeText(document.getElementById('output').textContent)
        .then(() => alert('Copied!'));
}}

init();
</script>
</body></html>"""


# =============================================================================
# HTTP Server
# =============================================================================


class RepoHandler(http.server.SimpleHTTPRequestHandler):
    """Minimal local HTTP handler serving the generated form and a JSON repo API."""

    repos_data: ClassVar[list[dict]] = []
    html_content: ClassVar[str] = ""

    def do_GET(self):
        """Serve the HTML form at ``/`` and repo JSON at ``/api/repos``; 404 otherwise."""
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(self.html_content.encode())
        elif self.path == "/api/repos":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(self.repos_data).encode())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        """Suppress the default per-request access log line."""
        # Suppress logs


def serve_form(html: str, repos: list[dict], port: int = 8765):
    """Serve ``html``/``repos`` on ``port``, open the browser, and block until Ctrl+C."""
    RepoHandler.html_content = html
    RepoHandler.repos_data = repos

    with socketserver.TCPServer(("", port), RepoHandler) as httpd:
        print(
            f"\n  {C.GREEN}✔{C.RESET} Server running at {C.CYAN}http://localhost:{port}{C.RESET}"
        )
        print(f"  {C.DIM}Press Ctrl+C to stop{C.RESET}\n")
        webbrowser.open(f"http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n  {C.YELLOW}Server stopped{C.RESET}")


# =============================================================================
# Execute Operations from JSON
# =============================================================================


def execute_operations(json_path: str, dry_run: bool = False) -> int:
    """Run the delete/transfer/clean actions from a generated JSON plan file.

    Returns 0 if every operation succeeded, 1 if any failed. ``dry_run``
    only prints what would happen without calling ``gh``/``git``.
    """
    with open(json_path) as f:
        data = json.load(f)

    actions = data.get("actions", {})
    total = sum(len(v) for v in actions.values())

    print(f"\n  {C.BOLD}Executing {total} operations from {json_path}{C.RESET}")
    if dry_run:
        print(f"  {C.YELLOW}DRY RUN - no changes will be made{C.RESET}\n")

    stats = {"success": 0, "failed": 0, "skipped": 0}

    # Delete operations
    for item in actions.get("delete", []):
        repo = item.get("repo") or item
        if dry_run:
            info(f"Would delete: {repo}")
            stats["skipped"] += 1
        else:
            ok, msg = delete_repo(repo)
            if ok:
                success(f"Deleted: {repo}")
                stats["success"] += 1
            else:
                error(f"Failed to delete {repo}: {msg}")
                stats["failed"] += 1

    # Transfer operations
    for item in actions.get("transfer", []):
        repo = item["repo"]
        target = item["target"]
        new_name = item.get("newName")
        if dry_run:
            info(f"Would transfer: {repo} → {target}/{new_name or repo.split('/')[-1]}")
            stats["skipped"] += 1
        else:
            ok, msg = transfer_repo(repo, target, new_name)
            if ok:
                success(f"Transferred: {repo} → {target}")
                stats["success"] += 1
            else:
                error(f"Failed to transfer {repo}: {msg}")
                stats["failed"] += 1

    # Clean transfer operations
    for item in actions.get("clean", []):
        repo = item["repo"]
        target = item["target"]
        new_name = item.get("newName")
        if dry_run:
            info(
                f"Would clean transfer: {repo} → {target}/{new_name or repo.split('/')[-1]}"
            )
            stats["skipped"] += 1
        else:
            ok, msg = clean_transfer(repo, target, new_name, delete_source=True)
            if ok:
                success(f"Clean transferred: {repo} → {target}")
                stats["success"] += 1
            else:
                error(f"Failed clean transfer {repo}: {msg}")
                stats["failed"] += 1

    print(f"\n  {C.BOLD}Results:{C.RESET}")
    print(f"  {C.GREEN}✔ Success: {stats['success']}{C.RESET}")
    if stats["failed"]:
        print(f"  {C.RED}✖ Failed: {stats['failed']}{C.RESET}")
    if stats["skipped"]:
        print(f"  {C.YELLOW}⚠ Skipped: {stats['skipped']}{C.RESET}")

    return 0 if stats["failed"] == 0 else 1


# =============================================================================
# CLI
# =============================================================================


def main():
    """CLI entry point: dispatch to execute/list/delete/transfer or form-generation mode."""
    parser = argparse.ArgumentParser(
        description="GitHub Repository Transfer Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate form and serve
  ./repo-transfer.py <source-owner> LibraxisAI Vetcoders

  # Generate form and open in browser
  ./repo-transfer.py <source-owner> LibraxisAI --open

  # Execute from JSON
  ./repo-transfer.py --execute cleanup.json
  ./repo-transfer.py --execute cleanup.json --dry-run

  # List repos
  ./repo-transfer.py --list <source-owner> --json

  # Direct operations
  ./repo-transfer.py --delete owner/repo --yes
  ./repo-transfer.py --transfer owner/repo --to NewOwner/new-name --yes
  ./repo-transfer.py --transfer owner/repo --to NewOwner --clean --yes

Created by Vetcoders (c)2024-2026
""",
    )

    # Positional args for orgs
    parser.add_argument("orgs", nargs="*", help="Organizations to include in form")

    # Form options
    parser.add_argument(
        "--open", "-o", action="store_true", help="Open HTML in browser (don't serve)"
    )
    parser.add_argument(
        "--port", "-p", type=int, default=8765, help="Server port (default: 8765)"
    )
    parser.add_argument("--output", help="Save HTML to file instead of serving")

    # Execute mode
    parser.add_argument(
        "--execute", "-e", metavar="JSON", help="Execute operations from JSON file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )

    # List mode
    parser.add_argument("--list", "-l", metavar="OWNER", help="List repos for owner")
    parser.add_argument("--filter", "-f", metavar="PATTERN", help="Filter pattern")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")

    # Direct operations
    parser.add_argument("--delete", "-d", metavar="REPO", help="Delete repository")
    parser.add_argument("--transfer", "-t", metavar="REPO", help="Transfer repository")
    parser.add_argument(
        "--to", metavar="TARGET", help="Transfer target (owner or owner/new-name)"
    )
    parser.add_argument(
        "--clean", "-c", action="store_true", help="Use clean transfer (breaks fork)"
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmations")

    args = parser.parse_args()

    # Check auth
    if not check_gh_auth():
        error("GitHub CLI not authenticated. Run: gh auth login")
        return 1

    # Execute mode
    if args.execute:
        return execute_operations(args.execute, args.dry_run)

    # List mode
    if args.list:
        repos = fetch_repos(args.list)
        if args.filter:
            repos = filter_repos(repos, args.filter)
        if args.json:
            print(json.dumps([r.to_dict() for r in repos], indent=2))
        else:
            for r in repos:
                print(r.full_name)
        return 0

    # Delete mode
    if args.delete:
        if not check_repo_exists(args.delete):
            error(f"Repository not found: {args.delete}")
            return 1
        if not args.yes:
            resp = input(f"Delete {args.delete}? Type DELETE to confirm: ")
            if resp != "DELETE":
                info("Aborted")
                return 0
        ok, msg = delete_repo(args.delete)
        if ok:
            success(f"Deleted: {args.delete}")
        else:
            error(msg)
        return 0 if ok else 1

    # Transfer mode
    if args.transfer:
        if not args.to:
            error("--to is required for transfer")
            return 1
        if "/" in args.to:
            target_owner, new_name = args.to.split("/", 1)
        else:
            target_owner, new_name = args.to, None

        if not args.yes:
            resp = input(f"Transfer {args.transfer} to {args.to}? [y/N]: ")
            if resp.lower() not in ["y", "yes"]:
                info("Aborted")
                return 0

        if args.clean:
            ok, msg = clean_transfer(
                args.transfer, target_owner, new_name, delete_source=True
            )
        else:
            ok, msg = transfer_repo(args.transfer, target_owner, new_name)

        if ok:
            success(msg)
        else:
            error(msg)
        return 0 if ok else 1

    # Form generation mode (default)
    orgs = args.orgs or ["Szowesgad", "LibraxisAI", "Loctree", "Vetcoders"]

    print(f"\n  {C.CYAN}🚀 Repo Transfer Tool{C.RESET}")
    print(f"  {C.DIM}Fetching repos...{C.RESET}\n")

    all_repos = []
    for org in orgs:
        repos = fetch_repos(org)
        print(f"  {org}: {len(repos)} repos")
        all_repos.extend(repos)

    all_repos.sort(key=lambda r: (r.owner, r.name.lower()))
    print(f"\n  {C.BOLD}Total: {len(all_repos)} repos{C.RESET}")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    port = None if args.open else args.port
    html = generate_html(all_repos, orgs, timestamp, port)

    if args.output:
        with open(args.output, "w") as f:
            f.write(html)
        success(f"Saved to {args.output}")
        return 0

    if args.open:
        # Save and open
        form_path = Path(__file__).parent / "repo-transfer-form.html"
        with open(form_path, "w") as f:
            f.write(html)
        webbrowser.open(f"file://{form_path}")
        success(f"Opened {form_path}")
        return 0

    # Serve
    serve_form(html, [r.to_dict() for r in all_repos], args.port)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Cancelled{C.RESET}")
        sys.exit(0)
