"""W2-C: pure pane-shell transform tests."""

from __future__ import annotations

from vibecrafted_core.vc_frame_staging import substitute_pane_shell


def test_counts_match_research_workflow_style() -> None:
    # synthetic 12 occurrences
    body = "\n".join(['command="zsh"'] * 12)
    out = substitute_pane_shell(body, "bash")
    assert out.count('command="bash"') == 12
    assert 'command="zsh"' not in out


def test_decoy_preserved() -> None:
    body = 'x command="zsh"\ny command="zsh-lookalike"\nz command="other"\n'
    out = substitute_pane_shell(body, "bash")
    assert 'command="zsh-lookalike"' in out
    assert 'command="other"' in out
    assert out.count('command="bash"') == 1
