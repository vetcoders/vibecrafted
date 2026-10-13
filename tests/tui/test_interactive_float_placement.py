"""Interactive agent launches land as titled floats in their own workspace.

An interactive init / operator / partner / resume used to hang the provider on
the workspace session as a NEW TAB named after the bare tool. The Agents
surface wants the opposite: the provider is a float above current content,
inside the workspace the launch declared, and its title says which provider
and model actually run in it -- ``<repo> · <agent> · <model>``.

Proven here through the real shell faces against the fake Frame engine that
records exact argv/cwd (never a live session, never a paid provider):

* the declared workspace session receives exactly one ``new-pane --floating``
  and no ``new-tab``; the pane carries the admitted command and the exact cwd;
* the title names the launched provider and the explicit ``--model``; an absent
  model is ``unknown`` -- never invented;
* the same workspace reused by another provider is retitled by the launch, not
  inherited from the earlier occupant;
* an unrelated live workspace is never addressed, and no second terminal opens;
* the shared spawn helper keeps its tab behaviour for non-interactive callers
  (the Marbles orchestrator tab): float is an explicit interactive opt-in.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "declaration_entry_scene", Path(__file__).with_name("test_declaration_entry.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_entry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_entry)

FACES = _entry.FACES
FOREIGN_LIVE = _entry.FOREIGN_LIVE
Scene = _entry.Scene
_run_face = _entry._run_face
_new_tabs = _entry._provider_tabs
_session_of = _entry._session_of
_cwd_of = _entry._cwd_of
_attaches = _entry._attaches
_assert_no_foreign_mutation = _entry._assert_no_foreign_mutation

HOST = "session-vibecrafted"


def _float_panes(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "new-pane" in c["argv"] and "--floating" in c["argv"]]


def _pane_name(call: dict) -> str:
    argv = call["argv"]
    return argv[argv.index("--name") + 1]


def _scene(tmp_path: Path) -> Scene:
    return Scene(tmp_path, live=[FOREIGN_LIVE, HOST], project="vibecrafted")


def _launch(scene: Scene, invocation: str):
    return _run_face(scene, invocation, tty=True, terminal_entry=True)


def _assert_one_float(scene: Scene, *, title: str) -> dict:
    calls = scene.calls()
    assert not _new_tabs(calls), [c["argv"] for c in calls]
    panes = [c for c in calls if "new-pane" in c["argv"]]
    assert len(panes) == 1, [c["argv"] for c in calls]
    pane = panes[0]
    assert pane in _float_panes(calls), pane["argv"]
    assert _session_of(pane) == HOST, pane
    assert _cwd_of(pane) == scene.root, pane
    assert _pane_name(pane) == title, pane["argv"]
    # Only the requested agent pane: the engine is asked for nothing else that
    # opens a surface, and the terminal host is never launched a second time.
    assert not scene.terminal_launch(wait=0), "a second terminal was opened"
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))
    return pane


@pytest.mark.parametrize("verb", ["init", "operator", "partner"])
def test_interactive_face_opens_a_float_titled_with_provider_and_model(
    tmp_path: Path, verb: str
) -> None:
    scene = _scene(tmp_path)
    result = _launch(scene, f"{FACES[verb]} codex --model gpt-test-1 --prompt SEED")

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    pane = _assert_one_float(scene, title="vibecrafted · codex · gpt-test-1")
    assert "--" in pane["argv"]
    assert Path(pane["argv"][pane["argv"].index("--") + 1]).is_file()


def test_absent_model_is_unknown_never_invented(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    result = _launch(scene, "_vetcoders_skill_init codex")

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_one_float(scene, title="vibecrafted · codex · unknown")


def test_resume_opens_a_float_with_the_explicit_model(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    result = _launch(scene, "_vetcoders_resume_agent codex --model gpt-test-2")

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_one_float(scene, title="vibecrafted · codex · gpt-test-2")


def test_workspace_reused_by_another_provider_is_retitled(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    first = _launch(scene, "_vetcoders_skill_init codex --model gpt-test-1")
    assert "RC=[0]" in first.stdout, first.stdout + first.stderr
    second = _launch(scene, "_vetcoders_skill_init claude --model claude-test-1")
    assert "RC=[0]" in second.stdout, second.stdout + second.stderr

    names = [_pane_name(c) for c in _float_panes(scene.calls())]
    assert names == [
        "vibecrafted · codex · gpt-test-1",
        "vibecrafted · claude · claude-test-1",
    ], names
    assert not _new_tabs(scene.calls())


def test_spawn_helper_without_the_interactive_opt_in_keeps_the_tab(
    tmp_path: Path,
) -> None:
    """The Marbles orchestrator shares the spawn helper; it must stay a tab."""
    scene = _scene(tmp_path)
    result = _run_face(
        scene,
        '_vetcoders_spawn_into_operator_session "marbles" "true"',
        extra_env={"VIBECRAFTED_OPERATOR_SESSION": HOST},
    )

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    calls = scene.calls()
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == HOST, calls
    assert not _float_panes(calls), calls
