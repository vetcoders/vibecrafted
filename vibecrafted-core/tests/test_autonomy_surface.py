from __future__ import annotations

import pytest
from vibecrafted_core.autonomy_surface import destructive_remote_push

ALLOWED = (
    "git push",
    "git push origin",
    "git push origin HEAD",
    "git push -u origin HEAD",
    "git push -u origin feat/mcp-sessions-continuity",
    "git push origin feat/foo",
    "GIT PUSH ORIGIN HEAD",
    "git push origin feat/main-fix",
    "git push origin feat/main",
    "please git push origin HEAD after the commit",
    '{"command": "git push origin HEAD"}',
)

BLOCKED = (
    "git push origin main",
    "git push origin master",
    "git push origin develop",
    "git push origin trunk",
    "git push origin release/4.1.0",
    "git push origin v1.2.3",
    "git push --force origin feat/x",
    "git push --force-with-lease",
    "git push -f origin HEAD",
    "git push origin +feat/x",
    "git push --delete origin feat/x",
    "git push origin :feat/x",
    "git push --mirror",
    "git push --all",
    "git push --tags",
    "git push origin HEAD:main",
    "git push origin refs/heads/main",
    "Please git push origin main",
    '{"command": "git push origin main"}',
)


@pytest.mark.parametrize("sample", ALLOWED)
def test_non_destructive_feature_branch_push_is_not_a_button(sample: str) -> None:
    assert destructive_remote_push(sample) is None


@pytest.mark.parametrize("sample", BLOCKED)
def test_force_trunk_delete_and_tag_push_stay_hard_stops(sample: str) -> None:
    evidence = destructive_remote_push(sample)
    assert evidence is not None
    assert "git" in evidence.lower()
    assert "push" in evidence.lower()
