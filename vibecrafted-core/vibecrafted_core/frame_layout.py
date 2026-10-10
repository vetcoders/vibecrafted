"""Serialized Frame layouts: parse and rewrite the KDL ``dump-layout`` emits.

The Frame serializer (and ``action dump-layout``) writes one node per line.  This
module reads exactly that shape, refuses anything else with ``KdlShapeError``,
and rewrites a session layout so it boots from a fresh runtime generation.  It
is a narrow reader, not a general KDL parser: callers fall back to
``minimal_layout`` when a layout does not have the serializer's shape.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SHELLS = frozenset({"zsh", "bash", "sh", "fish", "dash"})


def generation_root(path: str) -> str | None:
    """``.../releases/<generation>`` for a path inside an installed generation."""
    parts = Path(path).parts
    if "releases" not in parts:
        return None
    index = parts.index("releases")
    if index + 1 >= len(parts):
        return None
    return str(Path(*parts[: index + 2]))


def _is_python(name: str) -> bool:
    return re.fullmatch(r"(python|pypy)[0-9.]*", name) is not None


class KdlShapeError(ValueError):
    """The layout is not in the line-per-node shape the Frame serializer writes."""


@dataclass
class KdlNode:
    name: str
    args: list[tuple[str, bool]]
    props: list[tuple[str, str, bool]]
    start: int
    end: int
    parent: KdlNode | None = None
    children: list[KdlNode] = field(default_factory=list)

    def prop(self, key: str) -> str | None:
        for name, value, _ in self.props:
            if name == key:
                return value
        return None

    def child(self, name: str) -> KdlNode | None:
        return next((c for c in self.children if c.name == name), None)


def _read_string(line: str, index: int) -> tuple[str, int]:
    if line[index] == "r":
        match = re.match(r'r(#*)"', line[index:])
        if not match:
            raise KdlShapeError("malformed raw string")
        hashes = match.group(1)
        start = index + len(match.group(0))
        end = line.find('"' + hashes, start)
        if end < 0:
            raise KdlShapeError("unterminated raw string")
        return line[start:end], end + 1 + len(hashes)
    out: list[str] = []
    index += 1
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            out.append(escapes.get(line[index + 1], line[index + 1]))
            index += 2
            continue
        if char == '"':
            return "".join(out), index + 1
        out.append(char)
        index += 1
    raise KdlShapeError("unterminated string")


def _tokenize(line: str) -> tuple[list[tuple[str, Any]], int, int]:
    tokens: list[tuple[str, Any]] = []
    opens = closes = 0
    index = 0
    while index < len(line):
        char = line[index]
        if char.isspace() or char == ";":
            index += 1
        elif char == "{":
            opens += 1
            index += 1
        elif char == "}":
            closes += 1
            index += 1
        elif line.startswith("//", index):
            break
        elif char == '"' or re.match(r'r#*"', line[index:]):
            value, index = _read_string(line, index)
            tokens.append(("str", value))
        else:
            match = re.match(r'[^\s{};="]+', line[index:])
            if not match:
                raise KdlShapeError(f"unexpected character {char!r}")
            word = match.group(0)
            index += len(word)
            if index < len(line) and line[index] == "=":
                index += 1
                if index < len(line) and (
                    line[index] == '"' or re.match(r'r#*"', line[index:])
                ):
                    value, index = _read_string(line, index)
                    tokens.append(("prop", (word, value, True)))
                else:
                    bare = re.match(r'[^\s{};"]+', line[index:])
                    if not bare:
                        raise KdlShapeError("property without value")
                    index += len(bare.group(0))
                    tokens.append(("prop", (word, bare.group(0), False)))
            else:
                tokens.append(("bare", word))
    return tokens, opens, closes


def parse_layout(text: str) -> KdlNode:
    """Parse the line-per-node KDL the Frame serializer and dump-layout emit."""
    lines = text.splitlines()
    root: KdlNode | None = None
    stack: list[KdlNode] = []
    for number, line in enumerate(lines):
        tokens, opens, closes = _tokenize(line)
        if tokens:
            kind, name = tokens[0]
            if kind != "bare":
                raise KdlShapeError(f"line {number + 1}: node without a name")
            node = KdlNode(name=name, args=[], props=[], start=number, end=number)
            for token_kind, value in tokens[1:]:
                if token_kind == "prop":
                    node.props.append(value)
                else:
                    node.args.append((value, token_kind == "str"))
            if stack:
                node.parent = stack[-1]
                stack[-1].children.append(node)
            elif root is None:
                root = node
            else:
                raise KdlShapeError("more than one top-level node")
            if opens == 1 and closes == 0:
                stack.append(node)
            elif opens != closes:
                raise KdlShapeError(f"line {number + 1}: unsupported brace shape")
        else:
            if opens:
                raise KdlShapeError(f"line {number + 1}: anonymous block")
            for _ in range(closes):
                if not stack:
                    raise KdlShapeError(f"line {number + 1}: unbalanced brace")
                stack.pop().end = number
    if stack or root is None or root.name != "layout":
        raise KdlShapeError("layout is incomplete or not a layout")
    return root


def _q(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _render_line(indent: str, node: KdlNode, *, opens: bool) -> str:
    parts = [node.name]
    parts.extend(_q(value) if is_str else value for value, is_str in node.args)
    parts.extend(f"{k}={_q(v) if is_str else v}" for k, v, is_str in node.props)
    return indent + " ".join(parts) + (" {" if opens else "")


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _command_panes(root: KdlNode) -> list[tuple[KdlNode, KdlNode]]:
    """(tab, pane) for every command pane under the layout's direct tabs."""
    found: list[tuple[KdlNode, KdlNode]] = []
    for tab in (c for c in root.children if c.name == "tab"):
        queue = list(tab.children)
        while queue:
            node = queue.pop(0)
            if node.name == "pane" and node.prop("command"):
                found.append((tab, node))
            queue.extend(node.children)
    return found


def pane_signature(pane: KdlNode) -> list[str]:
    args = pane.child("args")
    return [pane.prop("command") or "", *(v for v, _ in (args.args if args else []))]


@dataclass(frozen=True)
class PaneLaunch:
    """One agent pane the rewritten layout must start with a fresh command."""

    key: str
    pane_command: str
    tab_name: str
    argv: tuple[str, ...]
    cwd: str
    title: str = ""


def _pane_block(
    indent: str, launch: PaneLaunch, keep: Sequence[tuple[str, str, bool]]
) -> list[str]:
    props = [("command", launch.argv[0], True), ("cwd", launch.cwd, True)]
    props.extend(p for p in keep if p[0] not in {"command", "cwd"})
    head = KdlNode("pane", [], props, 0, 0)
    lines = [_render_line(indent, head, opens=True)]
    if len(launch.argv) > 1:
        lines.append(indent + "    args " + " ".join(_q(a) for a in launch.argv[1:]))
    lines.append(indent + "}")
    return lines


def rewrite_layout(
    text: str,
    *,
    launches: Sequence[PaneLaunch],
    config_dir: str,
    old_roots: Sequence[str],
    new_root: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite a serialized session layout so it boots from the fresh generation.

    * agent panes (matched by their live foreground command, scoped by tab name
      when the command repeats) are replaced by their resurrect launch;
    * chrome panes that the serializer froze as ``<old python> <config>/x.py``
      run through the generation-agnostic ``<config>/pane-python`` again and are
      no longer suspended; a bare shell pane (``zsh -l``) is not suspended either;
    * any remaining string under an old generation root points at the new one;
    * every other command pane keeps ``start_suspended``: the Founder decides
      whether an editor or a long command should run again.

    Launches that match no pane get their own tab at the end of the layout.
    """
    lines = text.splitlines()
    root = parse_layout(text)
    panes = _command_panes(root)
    edits: dict[int, tuple[int, list[str]]] = {}
    report: list[dict[str, Any]] = []
    unmatched: list[PaneLaunch] = []
    taken: set[int] = set()
    for launch in launches:
        matches = [
            (tab, pane)
            for tab, pane in panes
            if " ".join(pane_signature(pane)) == launch.pane_command
            and pane.start not in taken
        ]
        if len(matches) > 1:
            matches = [(t, p) for t, p in matches if t.prop("name") == launch.tab_name]
        if len(matches) != 1:
            unmatched.append(launch)
            report.append(
                {"key": launch.key, "matched": False, "candidates": len(matches)}
            )
            continue
        tab, pane = matches[0]
        taken.add(pane.start)
        indent = _indent_of(lines[pane.start])
        keep = [p for p in pane.props if p[0] not in {"command", "cwd"}]
        edits[pane.start] = (pane.end, _pane_block(indent, launch, keep))
        report.append(
            {
                "key": launch.key,
                "matched": True,
                "tab": tab.prop("name"),
                "line": pane.start + 1,
            }
        )
    pane_python = f"{config_dir.rstrip('/')}/pane-python"
    for _, pane in panes:
        if pane.start in taken:
            continue
        command = pane.prop("command") or ""
        signature = pane_signature(pane)
        chrome = (
            _is_python(Path(command).name)
            and generation_root(command) is not None
            and len(signature) > 1
            and signature[1].startswith(config_dir.rstrip("/") + "/")
        )
        # A login/interactive shell is always safe to start again.
        shell = Path(command).name in _SHELLS and all(
            arg.startswith("-") for arg in signature[1:]
        )
        if not (chrome or shell) or (shell and not pane.child("start_suspended")):
            continue
        indent = _indent_of(lines[pane.start])
        props = [
            (k, pane_python if chrome and k == "command" else v, s)
            for k, v, s in pane.props
        ]
        head = KdlNode(pane.name, pane.args, props, 0, 0)
        body = [_render_line(indent, head, opens=True)]
        for child in pane.children:
            if child.name == "start_suspended":
                continue
            body.extend(lines[child.start : child.end + 1])
        body.append(lines[pane.end])
        edits[pane.start] = (pane.end, body)
        if chrome:
            report.append({"key": f"chrome:{pane.start + 1}", "chrome": signature[1]})
        else:
            report.append({"key": f"shell:{pane.start + 1}", "shell": command})
    output: list[str] = []
    index = 0
    while index < len(lines):
        if index in edits:
            end, replacement = edits[index]
            output.extend(replacement)
            index = end + 1
            continue
        output.append(lines[index])
        index += 1
    text_out = "\n".join(output)
    if new_root:
        for old in old_roots:
            if old and old != new_root:
                text_out = text_out.replace(f'"{old}/', f'"{new_root}/')
    if unmatched:
        tabs = [c for c in root.children if c.name == "tab"]
        insert_after = max((t.end for t in tabs), default=root.start)
        anchor = _indent_of(lines[tabs[0].start]) if tabs else "    "
        extra: list[str] = []
        for launch in unmatched:
            extra.append(
                f"{anchor}tab name={_q((launch.tab_name or 'agent') + ' (resumed)')} {{"
            )
            extra.extend(_pane_block(anchor + "    ", launch, []))
            extra.append(anchor + "}")
            report.append({"key": launch.key, "appended_tab": True})
        # Recompute against the rewritten text: edits never touch tab end lines.
        out_lines = text_out.splitlines()
        shift = len(output) - len(lines)
        position = insert_after + 1 + shift if insert_after > 0 else len(out_lines) - 1
        out_lines[position:position] = extra
        text_out = "\n".join(out_lines)
    return text_out + "\n", report


def minimal_layout(cwd: str, launches: Sequence[PaneLaunch]) -> str:
    """A layout built from nothing, for a session whose KDL could not be parsed."""
    lines = [
        "layout {",
        f"    cwd {_q(cwd)}",
        '    tab name="Shell" {',
        "        pane",
        "    }",
    ]
    for launch in launches:
        lines.append(f"    tab name={_q(launch.tab_name or 'agent')} {{")
        lines.extend(_pane_block("        ", launch, []))
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"
