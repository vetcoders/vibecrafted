"""Execution controls (``--permissions`` / ``--sandbox``): resolver + argv shapes.

Pure in-process contract of ``execution_controls`` and the spawn builders it
feeds. No provider binary is ever executed here; cursor materialization is fed
a fake ``--help`` surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vibecrafted_core import spawn, workflow
from vibecrafted_core.continuity.capabilities import (
    CURSOR_TRACKED_FLAGS,
    PROBE_CONFIRMED,
    CursorCliSurface,
)
from vibecrafted_core.execution_controls import (
    PERMISSION_POLICIES,
    SANDBOX_BOUNDARY,
    ExecutionControlsError,
    claude_sandbox_settings,
    parse_permissions_word,
    parse_sandbox_word,
    resolve_execution_controls,
)

# Claude Code's documented hard gate: on, fail when the backend is missing, and
# never let the model retry a command outside the sandbox.
CLAUDE_SANDBOX_ON = (
    '{"sandbox":{"enabled":true,"failIfUnavailable":true,'
    '"allowUnsandboxedCommands":false}}'
)
CLAUDE_SANDBOX_OFF = '{"sandbox":{"enabled":false}}'


def test_public_words_are_parsed_deliberately() -> None:
    assert parse_permissions_word(None) == ""
    assert parse_permissions_word("") == ""
    assert parse_permissions_word(" Auto ") == "auto"
    for word in PERMISSION_POLICIES:
        assert parse_permissions_word(word) == word
    with pytest.raises(ExecutionControlsError, match="--permissions expects"):
        parse_permissions_word("yolo")
    with pytest.raises(ExecutionControlsError, match="bypassPermissions"):
        # The provider's own spelling is not a public word.
        parse_permissions_word("bypassPermissions")

    assert parse_sandbox_word(None) is None
    assert parse_sandbox_word("") is None
    assert parse_sandbox_word(True) is True
    for word in ("true", "1", "yes", "on", "TRUE"):
        assert parse_sandbox_word(word) is True
    for word in ("false", "0", "no", "off"):
        assert parse_sandbox_word(word) is False
    with pytest.raises(ExecutionControlsError, match="--sandbox expects true or false"):
        parse_sandbox_word("maybe")


def test_spawn_re_exports_the_same_permission_vocabulary() -> None:
    assert spawn.PERMISSION_POLICIES is PERMISSION_POLICIES


def test_claude_auto_and_sandbox_true_map_to_supported_interfaces() -> None:
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    assert controls.permissions_requested == "auto"
    assert controls.permissions_effective == "auto"
    assert controls.sandbox_requested is True
    assert controls.sandbox_effective == "enabled"
    assert controls.provider_flags == (
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    )
    assert "bypassPermissions" not in controls.provider_flags
    assert "sandbox.enabled" in controls.evidence
    receipt = controls.receipt()
    assert receipt["schema"] == "vibecrafted.execution_controls.v1"
    assert receipt["sandbox_requested"] == "true"
    assert receipt["provider_flags"] == list(controls.provider_flags)
    assert receipt["boundary"] == SANDBOX_BOUNDARY["claude"]


def test_claude_sandbox_true_emits_the_documented_no_fallback_hard_gate() -> None:
    """The exact --settings document: enabled, fail-closed, no unsandboxed retry.

    ``sandbox.enabled`` alone would let inherited settings negate the request:
    ``allowUnsandboxedCommands`` defaults to true (the model may retry a failed
    command with ``dangerouslyDisableSandbox``) and ``failIfUnavailable``
    defaults to false (a missing backend runs commands unsandboxed with a
    warning). Both are scalar keys, so the command-line ``--settings`` level
    overrides user/project/local values.
    """
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    assert controls.provider_flags[-2] == "--settings"
    settings = json.loads(controls.provider_flags[-1])
    assert settings == {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
        }
    }
    assert settings == claude_sandbox_settings(True)
    # Key order is part of the argv contract the subprocess tests pin.
    assert controls.provider_flags[-1] == CLAUDE_SANDBOX_ON
    # The receipt names the real boundary and the remaining inherited hole, and
    # does not claim the launcher observed OS enforcement.
    assert "Bash-tool sandbox" in controls.boundary
    assert "child processes" in controls.boundary
    assert "MCP servers run outside" in controls.boundary
    assert "VM" not in controls.boundary
    assert "excludedCommands" in controls.behavior
    assert "managed settings outrank --settings" in controls.behavior
    assert "not observed" in controls.behavior


def test_claude_sandbox_false_is_explicit_not_omitted() -> None:
    controls = resolve_execution_controls("claude", sandbox=False)
    # Fleet doctrine (Founder 13716da4a2b8af7049ea1565): omission resolves auto.
    assert controls.permissions_effective == "auto"
    assert controls.permissions_source == "default"
    assert controls.sandbox_effective == "disabled"
    assert controls.provider_flags[-2:] == ("--settings", CLAUDE_SANDBOX_OFF)
    assert controls.provider_flags[:2] == ("--permission-mode", "auto")
    # Disabling touches one scalar key only; no hard-gate keys leak into "off".
    assert json.loads(controls.provider_flags[-1]) == {"sandbox": {"enabled": False}}
    assert claude_sandbox_settings(False) == {"sandbox": {"enabled": False}}
    assert controls.boundary == SANDBOX_BOUNDARY["claude"]


def test_omitted_controls_keep_provider_defaults() -> None:
    # Fleet doctrine (Founder 13716da4a2b8af7049ea1565): omission resolves the
    # shared ``auto`` default for every provider — no bypass fallbacks.
    claude = resolve_execution_controls("claude")
    assert not claude.requested
    assert claude.permissions_requested == ""
    assert claude.permissions_effective == "auto"
    assert claude.permissions_source == "default"
    assert claude.sandbox_effective == "provider-default"
    assert claude.provider_flags == ("--permission-mode", "auto")
    assert claude.boundary == SANDBOX_BOUNDARY["claude"]

    # Omission stays provider-default for agy too: the launcher neither emits
    # nor infers anything about agy's persistent terminal-sandbox settings.
    agy = resolve_execution_controls("agy")
    assert agy.permissions_effective == "auto"
    assert agy.provider_flags == ()
    assert agy.sandbox_effective == "provider-default"
    assert "--sandbox" not in agy.provider_flags
    assert "agy's own settings" in agy.behavior

    junie = resolve_execution_controls("junie")
    assert junie.permissions_effective == "auto"
    assert junie.provider_flags == ()

    codex = resolve_execution_controls("codex")
    # Default auto lands on the codex exec overlay: automatic review inside the
    # workspace-write sandbox (never the bypass flag).
    assert codex.permissions_effective == "auto"
    assert codex.sandbox_effective == "enabled"
    assert codex.provider_flags == ("--approve-for-me",)


def test_codex_headless_auto_uses_exec_surface_not_interactive_flags() -> None:
    controls = resolve_execution_controls("codex", permissions="auto")
    assert controls.provider_flags == ("--approve-for-me",)
    assert "--ask-for-approval" not in controls.provider_flags
    assert controls.sandbox_effective == "enabled"
    # Interactive resolution keeps the interactive cell untouched.
    interactive = spawn.resolve_provider_policy(
        "codex", "local-native", "auto", "interactive"
    )
    assert interactive.flags[:2] == ("--ask-for-approval", "on-request")


def test_codex_bypass_with_sandbox_true_keeps_the_sandbox() -> None:
    controls = resolve_execution_controls("codex", permissions="bypass", sandbox=True)
    assert controls.provider_flags == ("--sandbox", "workspace-write")
    assert "--dangerously-bypass-approvals-and-sandbox" not in controls.provider_flags
    assert controls.sandbox_effective == "enabled"


@pytest.mark.parametrize(
    ("provider", "permissions", "sandbox", "alternative"),
    [
        ("codex", "auto", False, "--permissions bypass --sandbox false"),
        ("codex", "read-only", False, "--sandbox true"),
        ("junie", "", True, "Omit --sandbox"),
        ("junie", "auto", False, "Omit --sandbox"),
        ("junie", "read-only", None, "supported: auto"),
        ("agy", "", False, "Omit --sandbox to keep agy's own setting"),
        ("agy", "auto", False, "or pass --sandbox true"),
        ("codex", "accept-edits", None, "supported: bypass, auto, read-only"),
        ("cursor", "accept-edits", True, "supported: bypass, auto, read-only"),
    ],
)
def test_unenforceable_combinations_are_refused_with_an_alternative(
    provider: str, permissions: str, sandbox: bool | None, alternative: str
) -> None:
    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls(provider, permissions=permissions, sandbox=sandbox)
    message = str(excinfo.value)
    assert message.startswith(f"{provider}:")
    assert alternative in message


def test_grok_and_cursor_and_agy_sandbox_words() -> None:
    grok_on = resolve_execution_controls("grok", permissions="auto", sandbox=True)
    assert grok_on.provider_flags == (
        "--permission-mode",
        "auto",
        "--sandbox",
        "workspace",
    )
    grok_ro = resolve_execution_controls("grok", permissions="read-only", sandbox=True)
    assert grok_ro.provider_flags[-2:] == ("--sandbox", "read-only")
    grok_off = resolve_execution_controls("grok", sandbox=False)
    assert grok_off.provider_flags[-2:] == ("--sandbox", "off")
    assert grok_off.sandbox_effective == "disabled"

    cursor_on = resolve_execution_controls("cursor", permissions="auto", sandbox=True)
    assert cursor_on.provider_flags == ("--trust", "--sandbox", "enabled")
    cursor_off = resolve_execution_controls("cursor", sandbox=False)
    # Doctrine default is auto (Founder 13716da4a2b8af7049ea1565): cursor's
    # auto cell is --trust only — no --force without an explicit opt-in.
    assert cursor_off.provider_flags == ("--trust", "--sandbox", "disabled")

    agy_on = resolve_execution_controls("agy", permissions="auto", sandbox=True)
    assert agy_on.provider_flags == ("--sandbox",)
    assert agy_on.sandbox_effective == "enabled"
    assert agy_on.boundary == SANDBOX_BOUNDARY["agy"]


def test_agy_sandbox_false_fails_closed_instead_of_claiming_disabled() -> None:
    """agy has only an opt-in flag; "no flag" is not evidence of "disabled".

    agy 1.1.27 keeps persistent terminal-sandbox settings (sandboxMode /
    enableTerminalSandbox in the binary), so omitting ``--sandbox`` may still
    run sandboxed. Without a verified explicit disable interface the resolver
    refuses explicit false with the supported alternatives; nothing is receipted
    as ``disabled`` on the strength of an absent flag.
    """
    for permissions in ("", "bypass", "auto", "accept-edits", "read-only"):
        with pytest.raises(ExecutionControlsError) as excinfo:
            resolve_execution_controls("agy", permissions=permissions, sandbox=False)
        message = str(excinfo.value)
        assert message.startswith("agy: agy 1.1.27 exposes only the opt-in --sandbox")
        assert "persistent terminal-sandbox settings" in message
        assert "Omit --sandbox to keep agy's own setting" in message
        assert "pass --sandbox true" in message
    assert "sandboxMode" in resolve_execution_controls("agy").evidence


def test_stdin_command_default_shape_carries_the_auto_doctrine() -> None:
    """No-control launches resolve the shared auto default (Founder
    13716da4a2b8af7049ea1565): claude gets --permission-mode auto, codex gets
    --approve-for-me inside its workspace-write sandbox. No bypass literals.
    """
    assert spawn._stdin_command("claude") == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "auto",
    ]
    assert spawn._stdin_command("codex") == [
        "codex",
        "exec",
        "--json",
        "--approve-for-me",
        "-",
    ]
    assert spawn._stdin_command("omp") == [
        "omp",
        "-p",
        "--mode",
        "json",
        "--approval-mode",
        "write",
    ]


def test_stdin_command_carries_resolved_controls_verbatim() -> None:
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    command = spawn._stdin_command("claude", controls=controls)
    assert command == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    ]
    codex = spawn._stdin_command(
        "codex", controls=resolve_execution_controls("codex", permissions="auto")
    )
    assert codex == ["codex", "exec", "--json", "--approve-for-me", "-"]
    grok = spawn._stdin_command(
        "grok",
        controls=resolve_execution_controls("grok", permissions="bypass", sandbox=True),
    )
    assert grok[:6] == [
        "grok",
        "--cwd",
        ".",
        "--permission-mode",
        "bypassPermissions",
        "--sandbox",
    ]
    assert grok[6] == "workspace"
    agy = spawn._stdin_command(
        "agy",
        controls=resolve_execution_controls("agy", permissions="auto", sandbox=True),
    )
    assert agy[:4] == ["agy", "--sandbox", "--add-dir", "."]
    assert agy[-5:] == [
        "--print=",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]

    with pytest.raises(ValueError, match="resolved for claude, not codex"):
        spawn._stdin_command("codex", controls=controls)


def test_cursor_sandbox_flag_is_verified_against_the_help_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def surface(with_sandbox: bool) -> CursorCliSurface:
        flags = {flag: flag != "--best-of-n" for flag in CURSOR_TRACKED_FLAGS}
        flags["--sandbox"] = with_sandbox
        return CursorCliSurface(
            executable="/fake/cursor-agent",
            version="2026.09.08-test",
            help_text="",
            flags=flags,
            state=PROBE_CONFIRMED,
            detail="fake surface",
            checked_at="",
        )

    controls = resolve_execution_controls("cursor", permissions="auto", sandbox=True)
    monkeypatch.setattr(
        "vibecrafted_core.continuity.capabilities.probe_cursor_cli_surface",
        lambda executable=None: surface(True),
    )
    command = spawn._stdin_command("cursor", controls=controls)
    assert command[-3:] == ["--trust", "--sandbox", "enabled"]

    monkeypatch.setattr(
        "vibecrafted_core.continuity.capabilities.probe_cursor_cli_surface",
        lambda executable=None: surface(False),
    )
    with pytest.raises(ValueError, match="lacks required flag\\(s\\) --sandbox"):
        spawn._stdin_command("cursor", controls=controls)


def _spec(**overrides: object) -> workflow.WorkflowLaunchSpec:
    base: dict[str, object] = {
        "agent": "claude",
        "mode": "workflow",
        "skill": "workflow",
        "prompt": "go",
        "file": "",
        "runtime": "headless",
        "root": "/tmp/repo",
    }
    base.update(overrides)
    return workflow.WorkflowLaunchSpec(**base)


def test_normalize_launch_spec_parses_and_refuses_before_launch(
    tmp_path: Path,
) -> None:
    payload = {
        "skill": "workflow",
        "agent": "claude",
        "prompt": "go",
        "root": str(tmp_path),
        "permissions": "auto",
        "sandbox": "true",
    }
    spec = workflow.normalize_launch_spec(payload, tmp_path)
    assert spec.permissions == "auto"
    assert spec.sandbox is True
    assert spec.to_payload()["sandbox"] is True

    omitted = workflow.normalize_launch_spec(
        {**payload, "permissions": "", "sandbox": ""}, tmp_path
    )
    assert omitted.permissions == ""
    assert omitted.sandbox is None

    with pytest.raises(ValueError, match="--permissions expects"):
        workflow.normalize_launch_spec({**payload, "permissions": "yolo"}, tmp_path)
    with pytest.raises(ValueError, match="--sandbox expects true or false"):
        workflow.normalize_launch_spec({**payload, "sandbox": "maybe"}, tmp_path)
    with pytest.raises(ValueError, match="junie: junie 26.8.31 exposes no sandbox"):
        workflow.normalize_launch_spec(
            {**payload, "agent": "junie", "permissions": ""}, tmp_path
        )
    with pytest.raises(ValueError, match="codex: --permissions auto runs under"):
        workflow.normalize_launch_spec(
            {**payload, "agent": "codex", "sandbox": "false"}, tmp_path
        )
    with pytest.raises(ValueError, match="agy: agy 1.1.27 exposes only the opt-in"):
        workflow.normalize_launch_spec(
            {**payload, "agent": "agy", "permissions": "", "sandbox": "false"},
            tmp_path,
        )
    with pytest.raises(ValueError, match="not carried into the research"):
        workflow.normalize_launch_spec(
            {**payload, "skill": "research", "agent": ["claude"]}, tmp_path
        )
    with pytest.raises(ValueError, match="not carried into the marbles"):
        workflow.normalize_launch_spec({**payload, "skill": "marbles"}, tmp_path)


def test_build_launch_command_injects_controls_only_when_requested(
    tmp_path: Path,
) -> None:
    # Doctrine: the default IS the shared auto policy, so default launches do
    # carry the auto argv; "requested" changes the word, not the wiring.
    default = workflow.build_launch_command(_spec(), tmp_path)
    assert default[-2:] == ["--permission-mode", "auto"]
    assert "--settings" not in default

    founder = workflow.build_launch_command(
        _spec(model="claude-fable-5-1", permissions="auto", sandbox=True), tmp_path
    )
    assert founder[:3] == ["claude", "--model", "claude-fable-5-1"]
    assert founder[-4:] == [
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    ]
    assert "bypassPermissions" not in founder

    codex = workflow.build_launch_command(
        _spec(agent="codex", permissions="auto"), tmp_path
    )
    assert codex == ["codex", "exec", "--json", "--approve-for-me", "-"]

    with pytest.raises(ExecutionControlsError, match="junie: junie 26.8.31"):
        workflow.build_launch_command(_spec(agent="junie", sandbox=True), tmp_path)


def test_launch_execution_controls_receipt_states_defaults_and_requests(
    tmp_path: Path,
) -> None:
    default = workflow.launch_execution_controls(_spec())
    assert default is not None
    assert default.receipt()["permissions_requested"] == ""
    assert default.receipt()["permissions_effective"] == "auto"
    assert default.receipt()["permissions_source"] == "default"
    assert default.receipt()["sandbox_requested"] == ""
    assert default.receipt()["sandbox_effective"] == "provider-default"

    requested = workflow.launch_execution_controls(
        _spec(permissions="read-only", sandbox=False)
    )
    assert requested is not None
    assert requested.receipt()["permissions_effective"] == "read-only"
    assert requested.receipt()["sandbox_requested"] == "false"
    assert requested.receipt()["sandbox_effective"] == "disabled"

    # Supervised runtimes have no controls receipt and refuse explicit ones.
    assert workflow.launch_execution_controls(_spec(skill="marbles")) is None
    with pytest.raises(ExecutionControlsError, match="not carried into the marbles"):
        workflow.launch_execution_controls(_spec(skill="marbles", permissions="auto"))


def test_machine_launch_receipt_projects_execution_controls() -> None:
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    payload = {
        "accepted": True,
        "run_id": "work-1",
        "agent": "claude",
        "skill": "workflow",
        "root": "/tmp/repo",
        "execution_controls": controls.receipt(),
    }
    receipt = workflow.machine_launch_receipt(payload)
    assert receipt["execution_controls"]["permissions_effective"] == "auto"
    assert receipt["execution_controls"]["sandbox_effective"] == "enabled"
    assert receipt["execution_controls"]["boundary"] == SANDBOX_BOUNDARY["claude"]
    assert "execution_controls" not in workflow.machine_launch_receipt(
        {k: v for k, v in payload.items() if k != "execution_controls"}
    )


def test_kimi_default_headless_auto_is_refused_with_the_explicit_alternative() -> None:
    """Doctrine default meets kimi's recorded native contract, honestly.

    Omission now resolves the fleet ``auto`` default (Founder
    13716da4a2b8af7049ea1565). kimi print mode rejects every permission flag
    with --prompt (OptionConflictError) and never prompts, so headless auto
    has no enforceable mapping: the launch refuses and names the opt-in
    (explicit bypass stays the never-ask bare print invocation), never a
    silent downgrade.
    """
    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls("kimi")
    message = str(excinfo.value)
    assert message.startswith("kimi:")
    assert "interactive-only" in message
    assert "supported: bypass" in message

    bypass = resolve_execution_controls("kimi", permissions="bypass")
    assert bypass.provider_flags == ()
    assert bypass.permissions_source == "dispatch"
    assert bypass.sandbox_effective == "provider-default"
    assert "OptionConflictError" in bypass.evidence


@pytest.mark.parametrize("permissions", ["auto", "read-only"])
def test_kimi_interactive_only_permissions_are_refused(permissions: str) -> None:
    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls("kimi", permissions=permissions)
    message = str(excinfo.value)
    assert message.startswith("kimi:")
    assert "interactive-only" in message
    assert "--prompt rejects the flag" in message
    assert "supported: bypass" in message


def test_kimi_accept_edits_is_refused_without_a_native_policy() -> None:
    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls("kimi", permissions="accept-edits")
    message = str(excinfo.value)
    assert message.startswith("kimi:")
    assert "no native accept-edits policy" in message


@pytest.mark.parametrize("sandbox", [True, False])
def test_kimi_sandbox_words_are_refused_without_a_control(sandbox: bool) -> None:
    """kimi 0.42.0 exposes no sandbox surface at all (probed --help + binary).

    Permissions are pinned to the enforceable bypass opt-in so the refusal
    under test is the sandbox axis, not the headless-auto default refusal.
    """
    word = "true" if sandbox else "false"
    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls("kimi", permissions="bypass", sandbox=sandbox)
    message = str(excinfo.value)
    assert message.startswith("kimi: kimi 0.42.0 exposes no sandbox control")
    assert f"--sandbox {word} cannot be enforced" in message
    assert "Omit --sandbox" in message


# ---------------------------------------------------------------------------
# omp (oh-my-pi 18.11.0): native tier ladder + the auto-first doctrine
# ---------------------------------------------------------------------------


def test_omp_cells_map_vc_words_onto_the_native_tier_ladder() -> None:
    """omp approval modes are capability-tier ceilings (read < write < exec):
    pi-coding-agent tools/approval.ts APPROVAL_MODE_MAX_TIER maps
    always-ask→read, write→write, yolo→exec, and modeApprovesTier auto-allows
    every tier at or below the mode's ceiling. The VC words land on that
    ladder as: bypass→yolo, auto→write (routine read+write runs, exec asks),
    accept-edits→write, read-only→the --tools read-tier whitelist. Both
    --approval-mode and --tools combine with -p (probed 2026-10-15, rc 0), so
    the headless overlay keeps the same argv with a fail-closed prompt note.
    """
    bypass = resolve_execution_controls("omp", permissions="bypass")
    assert bypass.provider_flags == ("--approval-mode", "yolo")
    assert bypass.permissions_source == "dispatch"

    auto = resolve_execution_controls("omp", permissions="auto")
    assert auto.provider_flags == ("--approval-mode", "write")
    assert auto.permissions_effective == "auto"
    # The receipt separates the requested word from the native mapping.
    assert "fails closed headless" in auto.behavior

    accept_edits = resolve_execution_controls("omp", permissions="accept-edits")
    assert accept_edits.provider_flags == ("--approval-mode", "write")

    read_only = resolve_execution_controls("omp", permissions="read-only")
    assert read_only.provider_flags == ("--tools", "read,grep,glob,find")


def test_omp_default_is_auto_and_sandbox_words_refuse() -> None:
    default = resolve_execution_controls("omp")
    assert default.permissions_effective == "auto"
    assert default.permissions_source == "default"
    assert default.provider_flags == ("--approval-mode", "write")

    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls("omp", sandbox=True)
    message = str(excinfo.value)
    assert message.startswith("omp: omp 18.11.0 exposes no sandbox control")
    assert "Omit --sandbox" in message


def test_durable_config_opt_in_beats_default_and_loses_to_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence: explicit dispatch > [agents.<x>] > top-level > fleet auto."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "vibecrafted"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'permissions = "read-only"\n[agents.omp]\npermissions = "bypass"\n',
        encoding="utf-8",
    )

    durable = workflow.launch_execution_controls(_spec(agent="omp"))
    assert durable is not None
    assert durable.permissions_effective == "bypass"
    assert durable.permissions_source == "config:agents.omp"
    assert durable.provider_flags == ("--approval-mode", "yolo")

    global_only = workflow.launch_execution_controls(_spec(agent="claude"))
    assert global_only is not None
    assert global_only.permissions_effective == "read-only"
    assert global_only.permissions_source == "config:global"

    dispatch_wins = workflow.launch_execution_controls(
        _spec(agent="omp", permissions="auto")
    )
    assert dispatch_wins is not None
    assert dispatch_wins.permissions_effective == "auto"
    assert dispatch_wins.permissions_source == "dispatch"

    # Counterexample 1: malformed durable word refuses before launch.
    (config_dir / "config.toml").write_text('permissions = "yolo"\n', encoding="utf-8")
    with pytest.raises(Exception, match="permissions"):
        workflow.launch_execution_controls(_spec(agent="claude"))

    # Counterexample 2: an enforceable-in-principle but unrecorded combination
    # (sandbox on omp) refuses instead of silently ignoring the request.
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(ExecutionControlsError, match="no sandbox control"):
        workflow.launch_execution_controls(_spec(agent="omp", sandbox=True))


def test_admission_frozen_decision_survives_a_later_config_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review counterexample: one frozen decision rides admission → argv/meta.

    Resolving a spec under durable read-only, then flipping the config to
    bypass, must not change what the admitted spec emits.
    """
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        'permissions = "read-only"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    payload = {
        "agent": "claude",
        "mode": "workflow",
        "skill": "workflow",
        "prompt": "go",
        "file": "",
        "runtime": "headless",
        "root": "/tmp/repo",
    }
    spec = workflow.normalize_launch_spec(payload, tmp_path)
    frozen = spec.execution_controls_frozen
    assert frozen is not None
    assert frozen["permissions_effective"] == "read-only"
    assert frozen["permissions_source"] == "config:global"

    (config_dir / "config.toml").write_text(
        'permissions = "bypass"\n', encoding="utf-8"
    )
    controls = workflow.launch_execution_controls(spec)
    assert controls is not None
    assert controls.permissions_effective == "read-only"
    command = workflow.build_launch_command(spec, tmp_path)
    assert "plan" in command
    assert "bypassPermissions" not in command


def test_junie_bypass_adaptation_is_frozen_across_config_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.execution_controls import controls_from_receipt

    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[runtime.picking]\npermissions = "bypass"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent.parent))
    payload = {
        "agent": "junie",
        "skill": "workflow",
        "prompt": "go",
        "root": str(tmp_path),
    }
    spec = workflow.normalize_launch_spec(payload, tmp_path)
    receipt = spec.execution_controls_frozen
    assert receipt["permissions_requested"] == "bypass"
    assert receipt["permissions_effective"] == "auto"
    assert receipt["permissions_reason"] == "junie-headless-no-bypass"
    config.write_text(
        '[runtime.picking]\npermissions = "read-only"\n', encoding="utf-8"
    )
    restored = controls_from_receipt(json.loads(json.dumps(receipt)))
    assert restored.receipt() == receipt
    assert workflow.launch_execution_controls(spec).receipt() == receipt
    assert workflow.build_launch_command(spec, tmp_path) == spawn._stdin_command(
        "junie", controls=restored
    )
    assert "--brave" not in workflow.build_launch_command(spec, tmp_path)
    with pytest.raises(
        ExecutionControlsError, match="cannot enforce --permissions read-only"
    ):
        workflow.normalize_launch_spec(payload, tmp_path)


def test_junie_bypass_adaptation_does_not_change_interactive_or_restricted_modes() -> (
    None
):
    interactive = resolve_execution_controls(
        "junie", permissions="bypass", mode="interactive"
    )
    assert interactive.permissions_effective == "bypass"
    assert interactive.provider_flags == ("--brave",)
    assert interactive.receipt()["permissions_reason"] == ""
    auto = resolve_execution_controls("junie", permissions="auto")
    assert auto.permissions_effective == "auto"
    assert auto.receipt()["permissions_reason"] == ""
    for permissions in ("read-only", "accept-edits"):
        with pytest.raises(
            ExecutionControlsError, match=f"cannot enforce --permissions {permissions}"
        ):
            resolve_execution_controls("junie", permissions=permissions)
    for sandbox in (True, False):
        with pytest.raises(ExecutionControlsError, match="no sandbox control"):
            resolve_execution_controls("junie", permissions="bypass", sandbox=sandbox)
