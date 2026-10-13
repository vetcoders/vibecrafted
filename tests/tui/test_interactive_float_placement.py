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
import json
import shlex
import subprocess
import sys
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


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _linked_worktree(scene: Scene, tmp_path: Path) -> Path:
    """A real linked worktree whose directory name is a run name, not the repo."""
    linked = tmp_path / "run 261013 abc"
    _git(scene.root, "worktree", "add", "-q", "-b", "wt-label", str(linked))
    nested = linked / "deep dir" / "inner"
    nested.mkdir(parents=True)
    return nested


def test_linked_worktree_launch_is_labelled_with_the_repo_not_the_run(
    tmp_path: Path,
) -> None:
    scene = _scene(tmp_path)
    nested = _linked_worktree(scene, tmp_path)
    result = _run_face(
        scene,
        "_vetcoders_skill_init codex --model gpt-test-3",
        tty=True,
        terminal_entry=True,
        cwd=nested,
    )

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    panes = _float_panes(scene.calls())
    assert [_pane_name(c) for c in panes] == ["vibecrafted · codex · gpt-test-3"], [
        c["argv"] for c in scene.calls()
    ]
    assert not _new_tabs(scene.calls())


def test_title_helper_resolves_the_common_repo_from_a_nested_linked_cwd(
    tmp_path: Path,
) -> None:
    scene = _scene(tmp_path)
    nested = _linked_worktree(scene, tmp_path)
    result = _run_face(scene, f'_vetcoders_interactive_pane_title codex "{nested}" m1')

    assert "vibecrafted · codex · m1" in result.stdout, result.stdout + result.stderr


def test_title_helper_outside_git_keeps_the_directory_name(tmp_path: Path) -> None:
    """No Git geometry means no verified repo identity: the plain directory
    name stays, exactly as before, and nothing is invented."""
    scene = _scene(tmp_path)
    plain = tmp_path / "plain dir"
    plain.mkdir()
    result = _run_face(scene, f'_vetcoders_interactive_pane_title claude "{plain}" ""')

    assert "plain dir · claude · unknown" in result.stdout, (
        result.stdout + result.stderr
    )


# --------------------------------------------------------------------------
# ambiguous new-pane ACK: never reissue a pane creation that may have landed
# --------------------------------------------------------------------------

# A recording Frame. `new-pane` creates a pane (or not) according to MODE and
# then answers with the ambiguous "timed out" ACK; `list-tabs` knows only
# tabs, so a pane can never be found there. `list-panes` lists panes by their
# command, the unique per-launch script path being the identity evidence.
_AMBIGUOUS_FRAME = r"""#!/usr/bin/env python3
import json, os, sys
d = os.environ["STUB_DIR"]
mode = os.environ.get("STUB_MODE", "")
def jl(name, default):
    try:
        return json.load(open(os.path.join(d, name)))
    except OSError:
        return default
def save(name, value):
    json.dump(value, open(os.path.join(d, name), "w"))
args = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(json.dumps(args) + "\n")
if args[:1] == ["--session"]:
    args = args[2:]
live = os.path.exists(os.path.join(d, "live"))
if args[:1] == ["list-sessions"] or args[:1] == ["ls"]:
    if live:
        print("host-a (attached)")
    sys.exit(0)
if args[:2] == ["attach", "--create-background"]:
    open(os.path.join(d, "live"), "w").write("1")
    sys.exit(0)
if args[:2] == ["action", "list-clients"]:
    print("CLIENT_ID")
    print("1")
    sys.exit(0)
if args[:2] == ["action", "list-tabs"]:
    print(json.dumps(jl("tabs.json", [])))
    sys.exit(0)
if args[:2] == ["action", "list-panes"]:
    if mode == "no-pane-listing":
        sys.exit(1)
    raw = os.path.join(d, "panes.raw")
    if os.path.exists(raw):
        sys.stdout.write(open(raw).read())
        sys.exit(0)
    print(json.dumps(jl("panes.json", [])))
    sys.exit(0)
if args[:2] == ["action", "new-pane"] or args[:2] == ["action", "new-tab"]:
    if mode == "missing-host" and not live:
        sys.stderr.write("Session 'host-a' not found\n")
        sys.exit(1)
    if mode == "refuse":
        sys.stderr.write("no current pane in this guest context\n")
        sys.exit(1)
    kind = args[1]
    name = args[args.index("--name") + 1]
    script = args[args.index("--") + 1]
    lost = mode in (
        "ack-lost",
        "ack-lost-hidden",
        "ack-lost-nothing",
        "same-title",
        "no-pane-listing",
    )
    created = mode not in ("ack-lost-nothing", "same-title")
    if created:
        n = jl("created.json", [])
        n.append({"kind": kind, "name": name, "script": script})
        save("created.json", n)
        if kind == "new-pane" and mode != "ack-lost-hidden":
            p = jl("panes.json", [])
            p.append(
                {
                    "id": len(p) + 1,
                    "is_plugin": False,
                    "exited": False,
                    "title": name,
                    "terminal_command": script,
                    "pane_command": "/bin/zsh",
                }
            )
            save("panes.json", p)
        if kind == "new-tab":
            t = jl("tabs.json", [])
            t.append({"name": name})
            save("tabs.json", t)
    if lost:
        sys.stderr.write("vc-frame action timed out after 5s\n")
        sys.exit(1)
    sys.exit(0)
sys.exit(0)
"""


class _Frame:
    def __init__(self, tmp_path: Path, mode: str) -> None:
        self.dir = tmp_path / "ambiguous-frame"
        self.dir.mkdir()
        self.bin = self.dir / "vc-frame"
        self.bin.write_text(_AMBIGUOUS_FRAME, encoding="utf-8")
        self.bin.chmod(0o755)
        self.mode = mode
        if mode != "missing-host":
            (self.dir / "live").write_text("1", encoding="utf-8")

    def json(self, name: str) -> list:
        path = self.dir / name
        return json.loads(path.read_text()) if path.exists() else []

    def calls(self) -> list[list[str]]:
        path = self.dir / "calls.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def creations(self, kind: str) -> list[dict]:
        return [c for c in self.json("created.json") if c["kind"] == kind]


def _spawn(scene: Scene, frame: _Frame, kind: str = "new-pane", name: str = "T"):
    script = scene.tmp_path / f"launch-{kind}.sh"
    script.write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    extra = ["--floating"] if kind == "new-pane" else []
    return _run_face(
        scene,
        f'_vetcoders_vc_frame_session_action "{frame.bin}" host-a '
        f'action {kind} {" ".join(extra)} --name "{name}" --cwd "{scene.root}" '
        f'-- "{script}"',
        extra_env={
            "STUB_DIR": str(frame.dir),
            "STUB_MODE": frame.mode,
            "VIBECRAFTED_PYTHON": sys.executable,
        },
    )


def test_lost_ack_for_a_pane_that_landed_is_confirmed_and_never_reissued(
    tmp_path: Path,
) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "ack-lost")
    result = _spawn(scene, frame)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert len(frame.creations("new-pane")) == 1, frame.calls()


def test_lost_ack_without_pane_evidence_is_unconfirmed_not_retried(
    tmp_path: Path,
) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "ack-lost-hidden")
    result = _spawn(scene, frame)

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert len(frame.creations("new-pane")) == 1, frame.calls()
    assert "not reissued" in result.stderr, result.stderr


def test_unlistable_panes_fail_honestly_without_a_second_creation(
    tmp_path: Path,
) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "no-pane-listing")
    result = _spawn(scene, frame)

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert len(frame.creations("new-pane")) == 1, frame.calls()
    assert len([c for c in frame.calls() if "new-pane" in c]) == 1, frame.calls()


def test_a_same_title_pane_is_not_evidence_of_this_launch(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "same-title")
    (frame.dir / "panes.json").write_text(
        json.dumps(
            [
                {
                    "id": 5,
                    "is_plugin": False,
                    "exited": False,
                    "title": "T",
                    "terminal_command": "/some/other/script.sh",
                }
            ]
        )
    )
    result = _spawn(scene, frame, name="T")

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert not frame.creations("new-pane"), frame.calls()
    new_pane_calls = [c for c in frame.calls() if "new-pane" in c]
    assert len(new_pane_calls) == 1, new_pane_calls


def test_definite_refusal_is_returned_once(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "refuse")
    result = _spawn(scene, frame)

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert len([c for c in frame.calls() if "new-pane" in c]) == 1, frame.calls()


def test_missing_host_still_resurrects_once_and_creates_one_pane(
    tmp_path: Path,
) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "missing-host")
    result = _spawn(scene, frame)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    creates = [c for c in frame.calls() if "--create-background" in c]
    assert len(creates) == 1, frame.calls()
    assert len(frame.creations("new-pane")) == 1, frame.calls()


def test_ambiguous_tab_ack_keeps_the_tab_presence_semantics(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "ack-lost")
    result = _spawn(scene, frame, kind="new-tab", name="codex")

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert len(frame.creations("new-tab")) == 1, frame.calls()


# --------------------------------------------------------------------------
# pane identity is structured: terminal_command == the launch's own script
# --------------------------------------------------------------------------

SCRIPT = "/tmp/vc-spawn-cmd-identity-17.sh"


def _pane(**fields) -> dict:
    base = {
        "id": 7,
        "is_plugin": False,
        "exited": False,
        "title": "T",
        "terminal_command": SCRIPT,
        "pane_command": "/bin/zsh",
        "tab_name": "work",
        "pane_cwd": "/tmp",
    }
    base.update(fields)
    return base


def _present(tmp_path: Path, payload, script: str = SCRIPT, raw: bool = False) -> bool:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "")
    text = payload if raw else json.dumps(payload)
    (frame.dir / "panes.raw").write_text(text, encoding="utf-8")
    result = _run_face(
        scene,
        f'_vetcoders_vc_frame_pane_present "{frame.bin}" host-a {shlex.quote(script)}',
        extra_env={
            "STUB_DIR": str(frame.dir),
            "VIBECRAFTED_PYTHON": sys.executable,
        },
    )
    assert "RC=[" in result.stdout, result.stdout + result.stderr
    return "RC=[0]" in result.stdout


def test_exact_terminal_command_is_pane_identity(tmp_path: Path) -> None:
    assert _present(tmp_path, [_pane()])


def test_title_only_match_is_not_identity(tmp_path: Path) -> None:
    """Parent's counterexample: the token lives in an unrelated title while the
    pane runs zsh. A raw substring match called this present."""
    pane = _pane(title=SCRIPT, terminal_command="/bin/zsh")
    assert not _present(tmp_path, [pane])


@pytest.mark.parametrize(
    "fields",
    [
        {"terminal_command": None, "pane_command": SCRIPT},
        {"terminal_command": "/bin/zsh", "pane_cwd": SCRIPT},
        {"terminal_command": "/bin/zsh", "tab_name": SCRIPT},
        {"terminal_command": SCRIPT + "x"},
        {"terminal_command": SCRIPT + " --arg"},
        {"terminal_command": "/other" + SCRIPT},
        {"is_plugin": True},
        {"exited": True},
        {"id": "7"},
        {"id": True},
    ],
    ids=[
        "live-pane_command-only",
        "cwd-collision",
        "tab-name-collision",
        "longer-path",
        "extra-argument",
        "prefixed-path",
        "plugin-pane",
        "exited-pane",
        "string-id",
        "bool-id",
    ],
)
def test_other_metadata_or_wrong_pane_type_is_not_identity(
    tmp_path: Path, fields: dict
) -> None:
    assert not _present(tmp_path, [_pane(**fields)])


def test_duplicate_identity_is_unknown(tmp_path: Path) -> None:
    assert not _present(tmp_path, [_pane(id=1), _pane(id=2)])


def test_one_live_pane_beside_an_exited_twin_is_unique(tmp_path: Path) -> None:
    assert _present(tmp_path, [_pane(id=1, exited=True), _pane(id=2)])


@pytest.mark.parametrize(
    "payload",
    ["not json", '[{"id": 7,', "{}", '{"panes": []}', "null", "[1, 2]", ""],
    ids=["text", "truncated", "object", "wrapped", "null", "scalars", "empty"],
)
def test_malformed_or_non_list_answers_are_unknown(
    tmp_path: Path, payload: str
) -> None:
    assert not _present(tmp_path, payload, raw=True)


def test_unicode_and_spaces_in_the_script_path_decode_exactly(tmp_path: Path) -> None:
    script = '/tmp/ünï cöde/łódź launch "q" cmd.sh'
    assert _present(tmp_path, [_pane(terminal_command=script)], script=script)
    # json.dumps escaped it as \uXXXX / \" -- decoding, not text search, matched.
    scene_dir = tmp_path / "ambiguous-frame"
    assert "\\u" in (scene_dir / "panes.raw").read_text(encoding="utf-8")


def test_failed_pane_query_is_unknown(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "no-pane-listing")
    result = _run_face(
        scene,
        f'_vetcoders_vc_frame_pane_present "{frame.bin}" host-a "{SCRIPT}"',
        extra_env={
            "STUB_DIR": str(frame.dir),
            "STUB_MODE": "no-pane-listing",
            "VIBECRAFTED_PYTHON": sys.executable,
        },
    )
    assert "RC=[1]" in result.stdout, result.stdout + result.stderr


def test_no_owned_interpreter_is_unknown_never_a_host_python(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "")
    (frame.dir / "panes.raw").write_text(json.dumps([_pane()]), encoding="utf-8")
    result = _run_face(
        scene,
        "_vetcoders_internal_python() { return 1; }; "
        f'_vetcoders_vc_frame_pane_present "{frame.bin}" host-a "{SCRIPT}"',
        extra_env={"STUB_DIR": str(frame.dir)},
    )
    assert "RC=[1]" in result.stdout, result.stdout + result.stderr


# --------------------------------------------------------------------------
# action classification: the action boundary, not any argv word
# --------------------------------------------------------------------------


def test_a_tab_named_new_pane_keeps_tab_semantics(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "ack-lost")
    result = _spawn(scene, frame, kind="new-tab", name="new-pane")

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert len(frame.creations("new-tab")) == 1, frame.calls()


def test_pane_launch_with_extra_arguments_has_no_provable_identity(
    tmp_path: Path,
) -> None:
    """Only a single script argument after `--` is an identity; anything else
    is unconfirmed (and still never reissued)."""
    scene = _scene(tmp_path)
    frame = _Frame(tmp_path, "ack-lost")
    script = scene.tmp_path / "launch-extra.sh"
    script.write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    result = _run_face(
        scene,
        f'_vetcoders_vc_frame_session_action "{frame.bin}" host-a '
        f'action new-pane --floating --name T --cwd "{scene.root}" '
        f'-- "{script}" extra',
        extra_env={
            "STUB_DIR": str(frame.dir),
            "STUB_MODE": "ack-lost",
            "VIBECRAFTED_PYTHON": sys.executable,
        },
    )

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert len([c for c in frame.calls() if "new-pane" in c]) == 1, frame.calls()
