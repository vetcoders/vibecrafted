"""Cursor fleet parity gate — cursor stands wherever claude and codex stand.

This module is the red/green umbrella for the cursor parity cut.  It
enumerates every workflow/agent-acceptance surface in the repo and asserts
``cursor`` is accepted identically to ``claude``/``codex``:

- Python acceptance registries (workflow, ship, wrappers, cli, spawn policy,
  dispatch sandbox, research lanes)
- Help surfaces (core help renderer + vc-* wrapper usage)
- The shell deck (both copies byte-identical, acceptance gate, help topics)
- Dispatch policy guards (provider runtime roots stay recovery-only)
- Rust operator surfaces (TUI agent picker, skills catalog, process family
  tags, mux IPC client kinds) — source-level gates; crate behavior is pinned
  by cargo tests next to each module
- Skill + docs fleet enumerations (EN and PL)

Intentional exception (fail-closed by sealed Cut B design, pinned here so the
exception is explicit, not silent): ``workflow_runtime.NATIVE_RESUME_AGENTS``
must NOT contain cursor until headless ``-p --resume`` is proven on host.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import (
    agent_dispatch,
    cli,
    help_surface,
    research_config,
    ship,
    spawn,
    supervisor_async,
    workflow,
    workflow_runtime,
    wrappers,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "vibecrafted-core"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

FLEET = {"claude", "codex", "agy", "junie", "grok", "cursor"}

# Canonical rendered selector everywhere a workflow asks for an agent name.
CANONICAL_SELECTOR = "<claude|codex|agy|junie|grok|cursor>"
STALE_SELECTOR = "<claude|codex|agy|junie|grok>"
# Dotted fleet list; negative lookahead so the fixed line still matches.
STALE_DOTTED_RE = re.compile(r"claude · codex · agy · junie · grok(?! · cursor)")


# ---------------------------------------------------------------------------
# 1. Python acceptance registries
# ---------------------------------------------------------------------------


def test_python_registries_accept_cursor() -> None:
    assert FLEET <= workflow.SUPPORTED_AGENTS
    assert FLEET <= ship.SUPPORTED_AGENTS
    assert FLEET <= wrappers.AGENTS
    assert FLEET <= cli.AGENTS
    assert "cursor" in spawn.POLICY_PROVIDERS
    assert agent_dispatch.sandbox_supported("cursor")
    assert agent_dispatch.sandbox_supported("claude")
    assert "cursor" in research_config.SUPPORTED_RESEARCH_AGENTS


def test_supervisor_infers_cursor_key_from_cursor_agent_binary() -> None:
    """argv[0] is the binary (`cursor-agent`); the fleet key is `cursor`."""
    assert supervisor_async._infer_agent(["cursor-agent", "-p", "hi"]) == "cursor"
    assert supervisor_async._infer_agent(["claude"]) == "claude"
    assert supervisor_async._infer_agent(["codex"]) == "codex"


def test_native_resume_stays_fail_closed_for_cursor() -> None:
    """Sealed Cut B decision: headless native resume is UNVERIFIED → closed."""
    assert "cursor" not in workflow_runtime.NATIVE_RESUME_AGENTS
    with pytest.raises(ValueError, match="native_resume_unsupported:cursor"):
        workflow_runtime.native_resume_argv("cursor", "sess-1")


# ---------------------------------------------------------------------------
# 2. Core help surface + wrapper usage
# ---------------------------------------------------------------------------


def test_help_surface_agent_selector_includes_cursor() -> None:
    assert help_surface.AGENT_SELECTOR == CANONICAL_SELECTOR


def test_every_workflow_help_renders_cursor_in_selector() -> None:
    # research/paste render agent-free usage; everything else names the selector
    agent_free = {"research", "paste"}
    for topic in sorted(help_surface.WORKFLOW_HELP):
        rendered = help_surface.render_workflow_help(topic)
        assert STALE_SELECTOR not in rendered, topic
        if topic in agent_free:
            continue
        assert CANONICAL_SELECTOR in rendered, topic


def test_wrapper_usage_line_includes_cursor(capsys: pytest.CaptureFixture) -> None:
    rc = wrappers.implement_main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert CANONICAL_SELECTOR in err
    assert STALE_SELECTOR not in err


# ---------------------------------------------------------------------------
# 3. Shell deck (canonical copy + checkout mirror)
# ---------------------------------------------------------------------------

CANONICAL_DECK = CORE_ROOT / "vibecrafted_core" / "deck" / "vibecrafted"
MIRROR_DECK = REPO_ROOT / "scripts" / "vibecrafted"


def test_deck_mirror_is_byte_identical_to_canonical() -> None:
    assert CANONICAL_DECK.read_bytes() == MIRROR_DECK.read_bytes()


def test_deck_agent_acceptance_gate_lists_cursor() -> None:
    body = CANONICAL_DECK.read_text(encoding="utf-8")
    assert "_agents=(claude codex agy junie grok cursor)" in body
    assert re.search(r"claude\|codex\|agy\|junie\|grok\|cursor\) return 0", body)


def test_deck_probes_cursor_agent_binary_not_editor_cli() -> None:
    """`command -v cursor` finds the editor CLI; the fleet needs cursor-agent."""
    body = CANONICAL_DECK.read_text(encoding="utf-8")
    assert re.search(r"cursor\)\s*cli=\"?cursor-agent", body)


def test_deck_help_texts_include_cursor() -> None:
    body = CANONICAL_DECK.read_text(encoding="utf-8")
    assert STALE_SELECTOR not in body
    assert not STALE_DOTTED_RE.search(body)
    # help-topic dispatch: `vibecrafted help cursor` must route to agent help
    assert re.search(r"claude\|codex\|agy\|junie\|grok\|cursor\)", body)


def _run_deck(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    # Restricted PATH: no cursor-agent, so `init cursor` must stop at the
    # CLI-missing gate (proving acceptance) instead of spawning anything.
    env["PATH"] = "/usr/bin:/bin"
    return subprocess.run(
        ["bash", str(MIRROR_DECK), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_deck_init_accepts_cursor_and_names_cursor_agent_binary(
    tmp_path: Path,
) -> None:
    result = _run_deck(tmp_path, "init", "cursor")
    output = ANSI_RE.sub("", result.stderr + result.stdout)
    assert "Unknown agent" not in output
    assert result.returncode != 0
    assert "cursor-agent" in output


def test_deck_help_lists_cursor_in_fleet_line(tmp_path: Path) -> None:
    result = _run_deck(tmp_path, "help")
    output = ANSI_RE.sub("", result.stdout)
    assert result.returncode == 0
    assert not STALE_DOTTED_RE.search(output)
    assert "cursor" in output


def test_deck_help_topic_routes_cursor(tmp_path: Path) -> None:
    result = _run_deck(tmp_path, "help", "cursor")
    output = ANSI_RE.sub("", result.stdout)
    assert result.returncode == 0
    assert "vibecrafted init cursor" in output


# ---------------------------------------------------------------------------
# 4. Dispatch policy guard
# ---------------------------------------------------------------------------


_DISPATCH_TEMPLATE = """schema = "vibecrafted.dispatch.v1"

[meta]
name = "dispatch-parity"
repo = "/tmp/vibecrafted-dispatch-parity"
reports_dir = "{reports_dir}"

[policy]
concurrency = 1
on_timeout = "fail"

[[phases]]
title = "Foundation"
detail = "parity gate"

[[cuts]]
id = "d1-parity"
phase = "Foundation"
critical = true
agent = "codex"
workflow = "implement"
prompt = "No-op parity cut."

  [[cuts.verify]]
  run = "true"
  expect = {{ exit_code = 0 }}
"""


def test_dispatch_policy_rejects_cursor_runtime_root() -> None:
    """Provider-local runtime roots are recovery-only; /.cursor/ joins them."""
    from vibecrafted_core.dispatch import schema

    body = (CORE_ROOT / "vibecrafted_core" / "dispatch" / "schema.py").read_text(
        encoding="utf-8"
    )
    assert '"/.cursor/"' in body
    # behavioral: a reports_dir under ~/.cursor must be rejected like ~/.claude
    for root in ("$HOME/.claude/reports", "$HOME/.cursor/reports"):
        result = schema.doctor_dispatch(_DISPATCH_TEMPLATE.format(reports_dir=root))
        assert not result.ok, root
        assert any("recovery-only" in e for e in result.errors), root


# ---------------------------------------------------------------------------
# 5. Rust operator surfaces (source gates; cargo pins behavior)
# ---------------------------------------------------------------------------

TUI = REPO_ROOT / "vibecrafted-app" / "tui-agent"
MUX = REPO_ROOT / "vibecrafted-app" / "mux-agent"


def test_tui_agent_picker_offers_cursor() -> None:
    body = (TUI / "src" / "app.rs").read_text(encoding="utf-8")
    match = re.search(r"pub fn agents\(\) -> \[&'static str; (\d+)\] \{([^}]*)\}", body)
    assert match, "agents() picker not found"
    listed = re.findall(r'"(\w+)"', match.group(2))
    assert "claude" in listed and "codex" in listed
    assert "cursor" in listed
    assert int(match.group(1)) == len(listed)


def test_tui_skills_catalog_resolves_cursor_token() -> None:
    body = (TUI / "src" / "skills_catalog.rs").read_text(encoding="utf-8")
    assert "SkillAgent::Cursor" in body
    assert '"cursor" => SkillAgent::Cursor' in body
    assert 'SkillAgent::Cursor => "cursor"' in body


def test_tui_process_family_tags_cursor() -> None:
    body = (TUI / "src" / "procs" / "model.rs").read_text(encoding="utf-8")
    assert "Cursor," in body
    assert 'Self::Cursor => "cursor"' in body
    assert 'blob.contains("cursor")' in body


def test_mux_ipc_client_kind_has_cursor_variant() -> None:
    command = (MUX / "src" / "ipc" / "command.rs").read_text(encoding="utf-8")
    assert re.search(r"enum ClientKind \{[^}]*\bCursor\b", command, re.DOTALL)
    handlers = (MUX / "src" / "ipc" / "handlers.rs").read_text(encoding="utf-8")
    assert "ClientKind::Cursor => crate::scan::HostKind::Cursor" in handlers
    lib = (TUI / "src" / "lib.rs").read_text(encoding="utf-8")
    assert '"cursor" => rmcp_mux::ipc::ClientKind::Cursor' in lib


# ---------------------------------------------------------------------------
# 6. Skill + docs fleet enumerations (EN and PL)
# ---------------------------------------------------------------------------

SKILLS = CORE_ROOT / "vibecrafted_core" / "skills"

# Files whose fleet enumerations must name cursor wherever claude+codex stand.
FLEET_ENUM_FILES = [
    SKILLS / "vc-agents" / "SKILL.md",
    SKILLS / "pl" / "vc-agents" / "SKILL.md",
    SKILLS / "vc-operator" / "references" / "agent-control-contract.md",
    SKILLS / "pl" / "vc-operator" / "references" / "agent-control-contract.md",
    SKILLS / "vc-trust" / "SKILL.md",
    SKILLS / "pl" / "vc-trust" / "SKILL.md",
    SKILLS / "vc-release" / "references" / "release-report-template.md",
    SKILLS / "pl" / "vc-release" / "references" / "release-report-template.md",
    SKILLS / "vc-research" / "references" / "synthesis-template.md",
    SKILLS / "pl" / "vc-research" / "references" / "synthesis-template.md",
    SKILLS / "vc-scaffold" / "references" / "plan-template.md",
    SKILLS / "pl" / "vc-scaffold" / "references" / "plan-template.md",
    SKILLS / "vc-scaffold" / "references" / "output-shapes.md",
    SKILLS / "pl" / "vc-scaffold" / "references" / "output-shapes.md",
    SKILLS / "vc-scaffold" / "plans" / "HOWTO.md",
    SKILLS / "pl" / "vc-scaffold" / "plans" / "HOWTO.md",
    SKILLS / "vc-workflow" / "SKILL.md",
    SKILLS / "pl" / "vc-workflow" / "SKILL.md",
    SKILLS / "vc-research" / "SKILL.md",
    SKILLS / "pl" / "vc-research" / "SKILL.md",
]

DOCS_ENUM_FILES = [
    REPO_ROOT / "docs" / "CLI_PRODUCT_SPEC.md",
    REPO_ROOT / "docs" / "FOUNDATION.md",
    REPO_ROOT / "docs" / "RUNBOOK.md",
    REPO_ROOT / "docs" / "public" / "cli" / "cli-overview.md",
    REPO_ROOT / "docs" / "public" / "getting-started" / "overview.md",
    REPO_ROOT / "docs" / "public" / "reference" / "security.md",
    REPO_ROOT / "docs" / "public" / "skills" / "skills-catalog.md",
    REPO_ROOT / "docs" / "public" / "troubleshooting" / "faq.md",
    REPO_ROOT / "docs" / "katalog-launcherow.html",
]

# Pipe-separated agent enumerations only; prose placeholders like
# "<claude did not address Q3; codex addressed it but…>" are not acceptance
# lists and must not match.
ENUM_LINE_RE = re.compile(r"<[^>]*\bclaude\b[^>]*\|[^>]*\bcodex\b[^>]*>")


@pytest.mark.parametrize(
    "path", FLEET_ENUM_FILES, ids=lambda p: p.name + ":" + p.parent.name
)
def test_skill_agent_enumerations_include_cursor(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for enum in ENUM_LINE_RE.findall(line):
            assert "cursor" in enum, f"{path}:{lineno}: {enum}"


# A fleet enumeration line: names claude + codex plus at least one more fleet
# member, whatever the separator (·, comma, pipe, brackets).
FLEET_LINE_RE = re.compile(r"\bclaude\b.*\bcodex\b.*\b(agy|junie|grok)\b")


@pytest.mark.parametrize("path", DOCS_ENUM_FILES, ids=lambda p: p.name)
def test_docs_fleet_enumerations_include_cursor(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert STALE_SELECTOR not in text, path
    assert "[claude|codex|agy|junie|grok]" not in text, path
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FLEET_LINE_RE.search(line):
            assert "cursor" in line, f"{path}:{lineno}: {line.strip()}"


# ---------------------------------------------------------------------------
# 7. Install diagnostics probe the real binary
# ---------------------------------------------------------------------------


def test_install_diagnostics_probe_cursor_agent_binary() -> None:
    """`cursor` on PATH is the editor CLI; the fleet binary is cursor-agent."""
    install_toml = (REPO_ROOT / "install.toml").read_text(encoding="utf-8")
    agents_line = next(
        line for line in install_toml.splitlines() if line.startswith("agents = [")
    )
    assert '"cursor-agent"' in agents_line
    assert '"cursor"' not in agents_line

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import installer_gui

        assert "cursor-agent" in installer_gui.AGENT_COMMANDS
        assert "cursor" not in installer_gui.AGENT_COMMANDS
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


def test_installer_gui_launcher_offers_cursor() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import installer_gui

        controller = installer_gui.InstallController(str(REPO_ROOT))
        payload = controller.preflight_payload()
        agents = payload["launcher_defaults"]["agents"]
        assert "claude" in agents and "codex" in agents
        assert "cursor" in agents
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# 8. Runtime shell scripts (marbles loop + spawn wrappers)
# ---------------------------------------------------------------------------

RUNTIME_SCRIPTS = CORE_ROOT / "vibecrafted_core" / "runtime" / "scripts"


def test_marbles_scripts_accept_cursor() -> None:
    spawn_body = (RUNTIME_SCRIPTS / "marbles_spawn.sh").read_text(encoding="utf-8")
    assert re.search(r"\^\(claude\|codex\|agy\|junie\|grok\|cursor\)\$", spawn_body)
    next_body = (RUNTIME_SCRIPTS / "marbles_next.sh").read_text(encoding="utf-8")
    assert re.search(r"\(claude\|codex\|agy\|junie\|grok\|cursor\)", next_body)


def test_marbles_verification_skips_cursor_with_warning() -> None:
    """cursor headless resume is UNVERIFIED → verify lane skips, not fails."""
    next_body = (RUNTIME_SCRIPTS / "marbles_next.sh").read_text(encoding="utf-8")
    assert re.search(r"cursor\).*skipping verification", next_body)


def test_cursor_spawn_wrapper_exists_and_probes_cursor_agent() -> None:
    wrapper = RUNTIME_SCRIPTS / "cursor_spawn.sh"
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    body = wrapper.read_text(encoding="utf-8")
    # Probes the fleet binary, not the editor CLI.
    assert "spawn_require_command cursor-agent" in body
    # Headless lane mirrors spawn.py's cursor permission policy.
    assert "cursor-agent -p --output-format stream-json --force --trust" in body
    # Stream events flow through the shared filter as the cursor lane.
    assert "vibecrafted_core.agent_stream --agent cursor" in body
