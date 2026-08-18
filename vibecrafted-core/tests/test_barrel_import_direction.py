"""The 0-cycles invariant, made executable.

Roadmap 4.2.0 cut W3-a took `vibecrafted_core`'s import cycles from 4 to 0 and
its loctree health from 74 to 80, by having `__init__.__getattr__` and two
sibling modules name the module that owns each symbol instead of routing
through the package barrel.

The invariant was then defended by a code comment and nothing else — and the
repository's own formatter had already reverted it once. `4918c7fb` exists
solely because ruff's PLR0402 rewrote `import vibecrafted_core.X as X` back
into `from vibecrafted_core import X` INSIDE the pre-commit hook, restoring the
self-cycle (structural 1, health back to 78) without anyone typing a character.

So the guard has to be a test. This one is host-independent, needs no loctree
binary, and fails on exactly the spelling the formatter produces.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by VetCoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "vibecrafted_core"
PACKAGE_NAME = "vibecrafted_core"


def submodule_names() -> set[str]:
    """Every name that resolves to a module of this package, not a symbol."""
    names = {path.stem for path in PACKAGE.glob("*.py") if path.stem != "__init__"}
    names |= {
        path.name for path in PACKAGE.iterdir() if (path / "__init__.py").is_file()
    }
    return names


def barrel_imports_of_submodules(source: str, submodules: set[str]) -> list[str]:
    """Find `from . import X` / `from vibecrafted_core import X` for a module X.

    Importing a *symbol* through the barrel is fine and pervasive. Importing a
    *module object* through it is the edge that closes the cycle, because the
    barrel is what the importer graph then records as the owner.
    """
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        targets_the_package = (node.level == 1 and node.module is None) or (
            node.level == 0 and node.module == PACKAGE_NAME
        )
        if not targets_the_package:
            continue
        for alias in node.names:
            if alias.name in submodules:
                spelled = (
                    "from . import" if node.level else f"from {PACKAGE_NAME} import"
                )
                offenders.append(f"line {node.lineno}: {spelled} {alias.name}")
    return offenders


def test_the_barrel_never_imports_a_sibling_through_itself() -> None:
    """`.` IS `__init__.py`; `from . import X` there is a literal self-cycle."""
    source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")

    offenders = barrel_imports_of_submodules(source, submodule_names())

    assert not offenders, (
        "vibecrafted_core/__init__.py imports its own submodules through the "
        "package barrel, which records __init__.py -> __init__.py as a "
        f"structural self-cycle: {offenders}. Spell it "
        "`import vibecrafted_core.X` (no `as` alias — PLR0402 rewrites that "
        f"form straight back). Offenders: {offenders}"
    )


def test_the_barrel_binds_its_lazy_exports_by_module_path() -> None:
    """The positive half: the fix is present, not merely the defect absent.

    A `__getattr__` that stopped importing anything would pass the test above
    while silently breaking every lazy export.
    """
    source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    dotted = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith(f"{PACKAGE_NAME}.")
    }
    assert dotted, "__getattr__ no longer imports any sibling by module path"

    # `import vibecrafted_core.X as X` is the shape ruff PLR0402 rewrites into
    # the barrel form. It must not reappear.
    aliased = [
        f"{alias.name} as {alias.asname}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith(f"{PACKAGE_NAME}.")
        and alias.asname
        and alias.name.rsplit(".", 1)[-1] == alias.asname
    ]
    assert not aliased, (
        "this alias form is what ruff PLR0402 rewrites back into "
        f"`from {PACKAGE_NAME} import X`, which restores the self-cycle: {aliased}"
    )


def test_the_two_modules_that_closed_the_diamonds_stay_closed() -> None:
    """`run_triage` and `vc_frame_delivery` put the barrel inside 3 diamonds.

    They bound sibling *module objects* through the package. Both were changed
    in `01e5e18a`; nothing but this test stops the next tidy-up from undoing it.
    """
    submodules = submodule_names()

    for name in ("run_triage.py", "vc_frame_delivery.py"):
        source = (PACKAGE / name).read_text(encoding="utf-8")
        offenders = barrel_imports_of_submodules(source, submodules)
        assert not offenders, (
            f"{name} binds a sibling module through the barrel: {offenders}"
        )
