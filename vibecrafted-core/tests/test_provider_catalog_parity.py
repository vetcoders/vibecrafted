"""Provider catalog parity gate — one truth for the launcher provider list.

F04 (Founder review 2026-09-15): kimi was installed and policy-supported, yet
invisible in the New agent launcher because ``vc-agent-workshop.py`` carried
its own literal ``AGENTS`` tuple that commit e6b94ac2 never touched.  This
module pins every provider enumeration the launcher path reads to the same
set, so the next provider cannot be silently dropped from one list again:

- ``vc-agent-workshop.py`` ``AGENTS`` (the New agent provider row)
- ``vibecrafted_core.wrappers.AGENTS`` (CLI wrapper acceptance)
- ``vibecrafted_core.spawn.POLICY_PROVIDERS`` (launch policy)
- ``vibecrafted_core.cli.AGENTS`` minus ``swarm``
- ``vibecrafted-app/tui-agent/src/catalog.rs`` — literal read of the
  expected agent vec in its own test module, plus the frozen capabilities
  fixture its tests parse

``swarm`` is excluded from parity: it is a research/meta orchestration face,
not a provider binary — catalog.rs's own ``drops_swarm`` test pins that the
launcher catalog filters it out of declarable agents, and no ``swarm_spawn.sh``
exists.  ``workflow.SUPPORTED_AGENTS`` equally carries ``swarm`` for the
research lane and is asserted here on the same minus-swarm basis.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

from vibecrafted_core import cli, spawn, workflow, wrappers

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SCRIPT = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-agent-workshop.py"
)
CATALOG_RS = REPO_ROOT / "vibecrafted-app" / "tui-agent" / "src" / "catalog.rs"
CAPABILITIES_FIXTURE = (
    REPO_ROOT
    / "vibecrafted-app"
    / "tui-agent"
    / "tests"
    / "fixtures"
    / "capabilities.json"
)

# Research/meta faces that stand in acceptance registries but are not
# launchable provider binaries.
NON_PROVIDER_FACES = frozenset({"swarm"})


def _load_workshop() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vc_agent_workshop", WORKSHOP_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_rs_expected_agents() -> set[str]:
    """The literal agent vec catalog.rs's own test asserts after parse."""
    body = CATALOG_RS.read_text(encoding="utf-8")
    match = re.search(r"catalog\.agents,\s*vec!\[([^\]]+)\]", body)
    assert match, "catalog.rs must keep a literal expected-agents vec in its tests"
    return set(re.findall(r'"(\w+)"', match.group(1)))


def test_python_provider_lists_are_identical() -> None:
    workshop = _load_workshop()
    expected = set(spawn.POLICY_PROVIDERS) - NON_PROVIDER_FACES
    assert set(workshop.AGENTS) == expected, "New agent provider row drifted"
    assert wrappers.AGENTS - NON_PROVIDER_FACES == expected, "wrappers drifted"
    assert cli.AGENTS - NON_PROVIDER_FACES == expected, "cli drifted"
    assert workflow.SUPPORTED_AGENTS - NON_PROVIDER_FACES == expected, (
        "workflow registry drifted"
    )


def test_workshop_agent_row_covers_every_policy_provider() -> None:
    """The launcher tuple must name each provider exactly once (no duplicates)."""
    workshop = _load_workshop()
    providers = [
        name for name in spawn.POLICY_PROVIDERS if name not in NON_PROVIDER_FACES
    ]
    assert sorted(workshop.AGENTS) == sorted(providers)
    assert len(workshop.AGENTS) == len(set(workshop.AGENTS))


def test_rust_catalog_literal_matches_python_truth() -> None:
    assert (
        _catalog_rs_expected_agents()
        == set(spawn.POLICY_PROVIDERS) - NON_PROVIDER_FACES
    )


def test_capabilities_fixture_matches_python_truth() -> None:
    """The frozen fixture VOC renders against must offer the same providers."""
    fixture = json.loads(CAPABILITIES_FIXTURE.read_text(encoding="utf-8"))
    assert (
        set(fixture["agents"]) - NON_PROVIDER_FACES
        == set(spawn.POLICY_PROVIDERS) - NON_PROVIDER_FACES
    )


def test_every_provider_has_a_spawn_wrapper_or_lane() -> None:
    """Each catalog provider must be launchable, not just listed."""
    scripts = (
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"
    )
    for name in set(spawn.POLICY_PROVIDERS) - NON_PROVIDER_FACES:
        wrapper = scripts / f"{name}_spawn.sh"
        assert wrapper.is_file(), f"missing spawn wrapper for {name}"
