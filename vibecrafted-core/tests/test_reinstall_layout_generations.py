"""Generation paths serialized by Frame must not outlive a clean reinstall."""

from vibecrafted_core import frame_layout

CONFIG = "/home/founder/.config/vibecrafted/vc-frame"
OLD = "/rt/releases/4.4.0+gd6482186"
OTHER = "/rt/releases/4.4.0+ge95ae878"
NEW = "/rt/releases/4.4.0+gb089b743"


def test_rewrite_discovers_generations_in_plugin_configuration_and_startup_args():
    layout = f'''layout {{
    tab name="Agents" {{
        pane {{
            plugin location="file:{OTHER}/plugins/session-manager.wasm" {{
                nested {{
                    startup_command "{OLD}/bin/python3"
                    startup_args "-c" "exec {OTHER}/bin/vibecrafted tui"
                    runtime_root "{OTHER}"
                    unrelated "/other/product/releases/old/plugin.wasm"
                }}
            }}
        }}
        pane command="{OTHER}/python/bin/python3.14" name="Agent Workspaces" {{
            args "-u" "{CONFIG}/vc-agent-workshop.py" "launcher"
            start_suspended true
        }}
        floating_panes {{
            pane command="bash" name="Host console" {{
                args "{OTHER}/bin/vibecrafted" "tui"
                start_suspended true
            }}
        }}
    }}
}}
'''
    out, report = frame_layout.rewrite_layout(
        layout, launches=[], config_dir=CONFIG, old_roots=[OLD], new_root=NEW
    )
    assert OLD not in out
    assert OTHER not in out
    assert f'location="file:{NEW}/plugins/session-manager.wasm"' in out
    assert f'startup_command "{NEW}/bin/python3"' in out
    assert f'startup_args "-c" "exec {NEW}/bin/vibecrafted tui"' in out
    assert f'runtime_root "{NEW}"' in out
    assert "/other/product/releases/old/plugin.wasm" in out
    root = frame_layout.parse_layout(out)
    panes = {pane.prop("name"): pane for _, pane in frame_layout._command_panes(root)}
    chrome = panes["Agent Workspaces"]
    assert frame_layout.pane_signature(chrome) == [
        f"{CONFIG}/pane-python",
        "-u",
        f"{CONFIG}/vc-agent-workshop.py",
        "launcher",
    ]
    assert chrome.child("start_suspended") is None
    assert panes["Host console"].child("start_suspended") is not None
    assert any(row.get("chrome") == f"{CONFIG}/vc-agent-workshop.py" for row in report)


def test_rewrite_discovers_layout_generations_without_any_running_old_roots():
    layout = f'''layout {{
    tab name="Start here" {{
        pane command="{OTHER}/python/bin/python3.14" {{
            args "{CONFIG}/vc-start-here.py"
            start_suspended true
        }}
        pane {{
            plugin location="file:{OTHER}/plugins/compact-bar.wasm"
        }}
    }}
}}
'''
    out, _ = frame_layout.rewrite_layout(
        layout, launches=[], config_dir=CONFIG, old_roots=[], new_root=NEW
    )
    assert OTHER not in out
    assert f'location="file:{NEW}/plugins/compact-bar.wasm"' in out
    assert f'command="{CONFIG}/pane-python"' in out
    assert "start_suspended" not in out


def test_rewrite_preserves_generation_strings_without_a_new_generation():
    layout = f"""layout {{
    tab name="Plugin" {{
        pane {{
            plugin location="file:{OLD}/plugins/compact-bar.wasm"
        }}
    }}
}}
"""
    out, _ = frame_layout.rewrite_layout(
        layout, launches=[], config_dir=CONFIG, old_roots=[OLD], new_root=None
    )
    assert out == layout


def test_rewrite_preserves_empty_plugin_block_shape_and_raw_strings():
    layout = f"""layout {{
    tab name="Plugin" {{
        pane {{
            plugin location=r#"file:{OTHER}/plugins/compact-bar.wasm"# {{}}
        }}
    }}
}}
"""
    out, _ = frame_layout.rewrite_layout(
        layout, launches=[], config_dir=CONFIG, old_roots=[], new_root=NEW
    )
    assert OTHER not in out
    root = frame_layout.parse_layout(out)
    plugin = root.child("tab").child("pane").child("plugin")
    assert plugin.prop("location") == f"file:{NEW}/plugins/compact-bar.wasm"
    assert plugin.children == []
