#!/usr/bin/env python3
"""Seed renderer for agents-workshop layouts. Next week: Layout factory."""

from __future__ import annotations

import html
import json
from pathlib import Path

W, H = 120, 36
RAIL = 20
CANVAS = W - RAIL  # 100
REPO = Path(__file__).resolve().parents[3]
DOCS = Path(__file__).resolve().parent
PLAN = (
    Path.home()
    / ".vibecrafted/artifacts/vetcoders/vibecrafted/2026_0817/plans/agents-workshop-layouts"
)

SESSION = "01a00bfd-5efc-7bf0-883f-a5d096f5235d"
AUTHOR = "grok <agents@vetcoders.io>"
DATE = "2026-08-17"


def vis(s: str) -> int:
    return len(s)


def clip(s: str, n: int, fill: str = " ") -> str:
    s = s.replace("\t", " ")
    if vis(s) > n:
        return s[:n]
    return s + fill * (n - vis(s))


def lr(left: str, right: str, w: int) -> str:
    left, right = left[:w], right[:w]
    gap = w - vis(left) - vis(right)
    if gap < 1:
        return (left + right)[:w]
    return left + (" " * gap) + right


def wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        if vis(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def frontmatter(*, layout_id: str, title: str, kind: str = "layout") -> str:
    return (
        "---\n"
        f"status: accepted\n"
        f"kind: {kind}\n"
        f"layout_id: {layout_id}\n"
        f"title: {title}\n"
        f"owner: grok\n"
        f"authored_by: {AUTHOR}\n"
        f"session_id: {SESSION}\n"
        f"date: {DATE}\n"
        f"grid: {W}x{H}\n"
        f"source_cheat: 120x36-operator-vertical\n"
        f"studio_type: 12px/1.28\n"
        f"scope: agents-workshop\n"
        f"product: vibecrafted\n"
        f"next: layout-factory\n"
        "---\n"
    )


class Frame:
    def __init__(self) -> None:
        self.rows = [[" "] * W for _ in range(H)]

    def put(self, x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            xx = x + i
            if 0 <= y < H and 0 <= xx < W:
                self.rows[y][xx] = ch

    def hline(self, x: int, y: int, n: int, ch: str = "─") -> None:
        self.put(x, y, ch * n)

    def rect(self, x: int, y: int, w: int, h: int) -> None:
        if h < 2 or w < 2:
            return
        self.put(x, y, "┌" + "─" * (w - 2) + "┐")
        for yy in range(y + 1, y + h - 1):
            self.put(x, yy, "│")
            self.put(x + w - 1, yy, "│")
            for xx in range(x + 1, x + w - 1):
                self.rows[yy][xx] = " "
        self.put(x, y + h - 1, "└" + "─" * (w - 2) + "┘")

    def fill(self, x: int, y: int, w: int, h: int, ch: str = " ") -> None:
        for yy in range(y, y + h):
            self.put(x, yy, ch * w)

    def lines(self) -> list[str]:
        return ["".join(r) for r in self.rows]


def compact_bar() -> str:
    left = " Vibecrafted. | L  ○ Start  ◉ Agents  ○ shell  ○ voc"
    right = "Composer · cmd"
    return clip(lr(left, right, W), W)


def status_bar() -> str:
    left = " LOCK  PANE  TAB  MOVE"
    right = "LIVE 3 | HEALTH"
    return clip(lr(left, right, W), W)


def paint_chrome(fr: Frame) -> None:
    fr.put(0, 0, compact_bar())
    fr.put(0, H - 1, status_bar())


def paint_rail(fr: Frame) -> None:
    rail = [
        "SESSIONS 5",
        " ● Live 3",
        "01 ○ main",
        "02 ○ lbrx-svc",
        "   · Shell",
        "03 ○ w-c207",
        "   · resume-grok",
        "   · resume-codex",
        "04 ◉ vc-release",
        "   · Start here",
        "   ◉ Agents",
        "   · shell",
        "   · voc",
        "05 ○ vibecrafted-vc_",
        "   · resume-grok",
        "   · Shell",
        "   · grok",
    ]
    for i, line in enumerate(rail):
        fr.put(0, 1 + i, clip(line, RAIL))


def paint_canvas_frame(fr: Frame, title: str, switcher: str) -> None:
    x, y = RAIL, 1
    w, h = CANVAS, H - 2
    fr.rect(x, y, w, h)
    left = "┌ " + title + " "
    right = " " + switcher + " ┐"
    fill = w - vis(left) - vis(right)
    if fill < 1:
        left = clip(left, w - vis(right) - 1)
        fill = w - vis(left) - vis(right)
    fr.put(x, y, left + ("─" * fill) + right)


def paint_text(fr: Frame, x: int, y: int, lines: list[str], width: int) -> int:
    for i, line in enumerate(lines):
        fr.put(x, y + i, clip(line, width))
    return y + len(lines)


def grok_transcript() -> list[str]:
    blocks = [
        "4.1.0 · feat/resume-no-implicit-native-session",
        "",
        "Maciej: floating, Ctrl+P, strzalki. hehe",
        "Jeden talerz. Host: float, rename, PANE, strzalki.",
        "",
        "Pasek talii:  [‹][›]  [Voc]  [Nowy]",
        "New agent = interaktywny TTY na tym tabie.",
        "Dispatch / headless = inne drzwi, pozniej.",
    ]
    width = CANVAS - 4
    out: list[str] = []
    for block in blocks:
        if block == "":
            out.append("")
        else:
            out.extend(wrap(block, width))
    return out[:12]


def paint_prompt(fr: Frame, y: int, model: str) -> None:
    x = RAIL + 2
    w = CANVAS - 4
    fr.put(x, y, "╭" + "─" * (w - 2) + "╮")
    fr.put(x, y + 1, "│ ❯" + " " * (w - 4) + "│")
    fr.put(x, y + 2, "╰" + clip("─ " + model + " ", w - 2, "─") + "╯")


def workshop(switcher: str, title: str, transcript: list[str], model: str) -> Frame:
    fr = Frame()
    paint_chrome(fr)
    paint_rail(fr)
    paint_canvas_frame(fr, title, switcher)
    prompt_y = H - 5
    paint_text(fr, RAIL + 2, 2, transcript[: prompt_y - 3], CANVAS - 4)
    paint_prompt(fr, prompt_y, model)
    return fr


def box_new_agent(focus: str = "agent") -> list[str]:
    inner_w = 88
    agents = ["agy", "claude", "codex", "grok", "junie"]
    flows = ["init", "resume", "operator", "partner"]

    def chips(items: list[str], selected: str) -> str:
        parts = []
        for it in items:
            parts.append(f"«{it}»" if it == selected else f"[{it}]")
        return " ".join(parts)

    def mark(name: str) -> str:
        return "▸" if focus == name else " "

    path = "/srv/vetcoders/vibecrafted"
    if focus == "path":
        path += "█"
    rows_inner = [
        f"  {mark('agent')} agent    {chips(agents, 'grok')}",
        f"  {mark('workflow')} rytual   {chips(flows, 'resume')}",
        f"  {mark('path')} sciezka  {path}",
        "  Enter = interaktywny panel na tym tabie. Nie mux. Nie headless.",
    ]
    title = "┌ ❯ Nowy agent "
    right = " [Anuluj] ┐"
    fill = inner_w - vis(title) - vis(right)
    top = title + ("─" * fill) + right
    bot = (
        "└"
        + clip(
            "─ ↑/↓ wiersz  ·  ←/→ chip  ·  spacja  ·  enter  ·  esc ", inner_w - 2, "─"
        )
        + "┘"
    )
    body = ["│" + clip(s, inner_w - 2) + "│" for s in rows_inner]
    return [top, *body, bot]


def box_new_dispatch() -> list[str]:
    inner_w = 88
    rows_inner = [
        "  ▸ agent    [agy] [claude] [codex] «grok» [junie]",
        "    rytual   [init] «resume» [operator] [partner]",
        "    sciezka  /srv/vetcoders/vibecrafted                                   ",
        "  Enter = HEADLESS worker. Bez TTY. Widać go na serwerze / w voc.",
    ]
    title = "┌ ❯ Nowy dispatch "
    right = " [Anuluj] ┐"
    fill = inner_w - vis(title) - vis(right)
    top = title + ("─" * fill) + right
    bot = (
        "└"
        + clip("─ ten sam chassis co Nowy agent · inne narodziny ", inner_w - 2, "─")
        + "┘"
    )
    body = ["│" + clip(s, inner_w - 2) + "│" for s in rows_inner]
    return [top, *body, bot]


def box_voc() -> list[str]:
    inner_w = 64
    rows_inner = [
        "  ● grok     vibecrafted   14m  na wierzchu",
        "  ○ claude   vibecrafted    0m",
        "  ○ codex    vc-workspace   1h",
        "  ○ junie    ~             40m",
        "  j/k  enter podnies  n Nowy agent",
    ]
    title = "┌ voc · ten tab "
    right = " PIN ◉ ┐"
    fill = inner_w - vis(title) - vis(right)
    top = title + ("─" * max(1, fill)) + right
    bot = "└" + clip("─ drzwi tego taba, nie farma ", inner_w - 2, "─") + "┘"
    body = ["│" + clip(s, inner_w - 2) + "│" for s in rows_inner]
    return [top, *body, bot]


def clear_canvas_band(fr: Frame, y0: int, y1: int) -> None:
    """Empty the pane interior so a card does not slice words."""
    for y in range(max(2, y0), min(H - 2, y1)):
        fr.put(RAIL + 2, y, " " * (CANVAS - 4))


def stamp(fr: Frame, box: list[str], x: int, y: int) -> None:
    clear_canvas_band(fr, y - 1, y + len(box) + 1)
    for i, line in enumerate(box):
        fr.put(x, y + i, line)


SWITCH_NOW = "[‹2/4›] [Voc] [Nowy]"
SWITCH_AFTER = "[‹3/5›] [Voc] [Nowy]"


def layout_1() -> list[str]:
    return workshop(
        SWITCH_NOW, "grok · vibecrafted", grok_transcript(), "Grok 4.6 · always-approve"
    ).lines()


def layout_2() -> list[str]:
    fr = workshop(
        SWITCH_NOW, "grok · vibecrafted", grok_transcript(), "Grok 4.6 · always-approve"
    )
    box = box_new_agent("agent")
    x = RAIL + (CANVAS - vis(box[0])) // 2
    y = 8
    stamp(fr, box, x, y)
    return fr.lines()


def layout_3() -> list[str]:
    born = [
        "claude · resume · vibecrafted",
        "",
        "Wlasnie sie urodzil. Interaktywny TTY. Talia 3/5.",
        "Poprzedni grok zyje jedno [‹] wstecz.",
        "Nie powstala sesja muxa. Nie Tab #7. Nie resume-*.",
        "Headless to nie ten przycisk — to Layout-5 albo Quick cmd.",
    ]
    width = CANVAS - 4
    lines: list[str] = []
    for b in born:
        lines.extend([""] if b == "" else wrap(b, width))
    return workshop(
        SWITCH_AFTER,
        "claude · resume · vibecrafted",
        lines,
        "Claude · interactive pane",
    ).lines()


def layout_4() -> list[str]:
    fr = workshop(
        SWITCH_NOW, "grok · vibecrafted", grok_transcript(), "Grok 4.6 · always-approve"
    )
    box = box_voc()
    stamp(fr, box, RAIL + 4, 7)
    return fr.lines()


def layout_5() -> list[str]:
    fr = workshop(
        SWITCH_NOW, "grok · vibecrafted", grok_transcript(), "Grok 4.6 · always-approve"
    )
    box = box_new_dispatch()
    x = RAIL + (CANVAS - vis(box[0])) // 2
    stamp(fr, box, x, 8)
    return fr.lines()


def fence(lines: list[str]) -> str:
    assert len(lines) == H, len(lines)
    for i, line in enumerate(lines):
        if vis(line) != W:
            raise SystemExit(f"row {i} width {vis(line)}")
    tens = "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(W))
    ones = "".join(str(i % 10) for i in range(W))
    return "```\n" + tens + "\n" + ones + "\n" + "\n".join(lines) + "\n```\n"


PRAWO = f"""{frontmatter(layout_id="Layout-0", title="Prawo siatki i dwóch drzwi", kind="design-doc")}
# Agents workshop — prawo, korekty, Layout-1..n

Te pliki są po to, żeby **człowiek** zobaczył warsztat zanim ktokolwiek ruszy plugin.
Nie są kalką bałaganu z żywej ściągi. Są spakowanym celem na siatce ze ściągi.

## Frontmatter — jak mnie znaleźć

Każdy Layout ma:

```
owner: grok
authored_by: {AUTHOR}
session_id: {SESSION}
date: {DATE}
grid: {W}x{H}
```

Szukaj `session_id: {SESSION}` albo `authored_by: grok`.

## Siatka

Komórka TUI to nie piksel. Canva projektowa bierze **proporcje okna operatora w VERTICAL**, nie pełnego muxa 182.

| | pełny mux | Twoja ściąga VERTICAL | makieta |
|---|---|---|---|
| szerokość | ~182 | ~126 | **120** |
| wysokość | ~40 | ~30 w zrzucie, za płasko na 120 | **36** |

```
wiersz   1     compact-bar
wiersze  2–35  ciało   (rail {RAIL} + canvas {CANVAS})
wiersz  36     status-bar
```

**LOCKED 2026-08-17 (operator: JEAH — teraz idealnie).**
Studio: `12px / 1.28`. Nie wracamy do 1.7 ani do 130 wierszy.

120 zostaje. 36 daje powietrze jak pusta dziura shella na ściądze.
Pasek talii: `[‹2/4›] [Voc] [Nowy]`. Karta Nowy agent: 6 wierszy.
Nie kopiujemy Tab #6 / Tab #7 — tylko proporcje canvy.

## Korekty

Grok nie powinien był rysować 130 wierszy. Miał poprawić: „to piksele, nie komórki”.

Poza tym z recenzji użytkownika zostaje:

1. Karta nie przecina słów w tle.
2. Ścieżka w całości.
3. Na pasku NOW nie ma `[New dispatch]`.
4. W celu tab nazywa się `voc`, nie `Tab #6`.
5. Chrome jak żywy (SESSIONS/LOCK), karta po polsku.

Wektor z naszych klatek:

float+PANE+strzałki → talia hosta → brakowało `[New agent]` → rytuał 3 osi, nie KDL → headless to inne drzwi.

## Dwa launchery

| drzwi | przycisk | narodziny |
|---|---|---|
| teraz | `[New agent]` | interaktywny TTY na **tym** tabie |
| później | `[New dispatch]` | worker, najlepiej headless, tylko przez serwer |
| zawsze | z wewnątrz żywego interaktywnego agenta | dispatch jak dziś |
| zawsze | Quick cmd | osobny widok, nie atrapa paska |

## Nawigacja karty

| klawisz | skutek |
|---|---|
| `↑` `↓` | wiersz agent → rytuał → ścieżka |
| `←` `→` | chip (w ścieżce: kursor) |
| `spacja` | zaznacz chip |
| `enter` | launch |
| `esc` / Anuluj | nic się nie rodzi |

## Layout factory (za tydzień)

CUT-1..n były briefami do cięcia kodu.
**Layout-1..n** są rysunkami do cięcia powierzchni.

Nie generujemy jeszcze KDL. Te pliki są ziarnem fabryki.

## Pliki

| id | plik | co widzisz |
|---|---|---|
| Layout-0 | ten dokument | prawo |
| Layout-1 | `Layout-1.md` | warsztat, jedna twarz |
| Layout-2 | `Layout-2.md` | Nowy agent (karta) |
| Layout-3 | `Layout-3.md` | po Enter |
| Layout-4 | `Layout-4.md` | voc jako drzwi tego taba |
| Layout-5 | `Layout-5.md` | Nowy dispatch, później |

HTML studio: `preview.html` (lewe taby Layout-1..n, jeden canvas). Analog `/scaffold/editor`.
"""


LAYOUTS = [
    ("Layout-0", "Prawo siatki i dwóch drzwi", "design-doc", None, PRAWO),
    (
        "Layout-1",
        "Warsztat",
        "brief",
        layout_1,
        "Jedna twarz. Pasek talii w tytule, nie na Quick cmd. Rail zostaje. Zero nachodzących floatów.\n",
    ),
    (
        "Layout-2",
        "Nowy agent",
        "brief",
        layout_2,
        "Rytuał z Session Managera, nie jego lista. Tylko interaktywny launch. `←` `→` po chipach.\n",
    ),
    (
        "Layout-3",
        "Po launchu",
        "brief",
        layout_3,
        "Enter z karty. Urodził się panel. Talia 3/5. Poprzedni grok jedno `[‹]` wstecz.\n",
    ),
    (
        "Layout-4",
        "Voc drzwi",
        "brief",
        layout_4,
        "`[Voc Console]` podnosi voc. Lista = twarze tego taba, nie Observe 8 live.\n",
    ),
    (
        "Layout-5",
        "Nowy dispatch (później)",
        "brief",
        layout_5,
        "Nie budujemy teraz. Ten sam chassis. Inne narodziny: headless, serwer, nie TTY.\n",
    ),
]


def md_for(layout_id: str, title: str, kind: str, drawer, preface: str) -> str:
    head = frontmatter(layout_id=layout_id, title=title, kind=kind)
    if drawer is None:
        return preface
    return head + f"# {layout_id} · {title}\n\n{preface}\n" + fence(drawer())


def preview_html(frames: dict[str, list[str]]) -> str:
    tabs = []
    sections = []
    for i, (lid, title, *_rest) in enumerate(LAYOUTS):
        if lid == "Layout-0":
            continue
        body = "\n".join(frames[lid])
        tabs.append(
            f'<button type="button" class="tab{" is-active" if i == 1 else ""}" data-id="{lid}">{lid}<span>{html.escape(title)}</span></button>'
        )
        sections.append(
            f'<section class="doc{" is-active" if i == 1 else ""}" id="{lid}">'
            f"<pre>{html.escape(body)}</pre></section>"
        )
    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>Agents workshop · Layout-1..n</title>
<style>
:root {{
  --bg:#101216; --ink:#e8e6e1; --mute:#8b8a84; --line:#2a2d33;
  --accent:#c4b08a; --rail:#16181d; --hi:#1e2229;
  font-family: ui-sans-serif, system-ui, sans-serif;
}}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; height:100%; background:var(--bg); color:var(--ink); }}
.app {{ display:grid; grid-template-columns:220px 1fr 240px; grid-template-rows:48px 1fr 28px; height:100%; }}
.top {{ grid-column:1/-1; display:flex; align-items:center; gap:16px; padding:0 16px; border-bottom:1px solid var(--line); }}
.brand {{ letter-spacing:.08em; font-size:13px; color:var(--accent); }}
.top small {{ color:var(--mute); }}
.left {{ background:var(--rail); border-right:1px solid var(--line); padding:12px 8px; overflow:auto; }}
.tab {{ display:flex; flex-direction:column; align-items:flex-start; width:100%; gap:2px; margin:0 0 4px; padding:8px 10px; background:transparent; color:var(--ink); border:1px solid transparent; border-radius:6px; cursor:pointer; text-align:left; }}
.tab span {{ color:var(--mute); font-size:12px; }}
.tab.is-active {{ background:var(--hi); border-color:var(--line); }}
.center {{ overflow:auto; padding:16px; }}
.doc {{ display:none; }}
.doc.is-active {{ display:block; }}
pre {{ margin:0 auto; width:120ch; font:12px/1.28 ui-monospace, "SF Mono", Menlo, Consolas, monospace; white-space:pre; color:#d7d4cc; }}
.right {{ border-left:1px solid var(--line); padding:16px; font-size:13px; }}
.right h2 {{ font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--mute); margin:0 0 8px; }}
.right p {{ margin:0 0 10px; color:var(--mute); }}
.stat {{ border-top:1px solid var(--line); grid-column:1/-1; display:flex; gap:24px; align-items:center; padding:0 16px; font-size:12px; color:var(--mute); }}
</style>
</head>
<body>
<div class="app">
  <header class="top">
    <div class="brand">Vibecrafted. · Layout studio</div>
    <small>120×36 · canva VERTICAL · analog CUT-1..n</small>
  </header>
  <nav class="left">{"".join(tabs)}</nav>
  <main class="center">{"".join(sections)}</main>
  <aside class="right">
    <h2>Inspector</h2>
    <p>owner: grok</p>
    <p>authored_by: {html.escape(AUTHOR)}</p>
    <p>session: {SESSION}</p>
    <p>date: {DATE}</p>
    <p>grid: {W}×{H}</p>
    <p>LOCKED: 120×36 · 12px/1.28. Operator accepted 2026-08-17.</p>
    <p>Layout-5 jest przygaszony w prawie — nie budujemy go teraz.</p>
  </aside>
  <footer class="stat">
    <span>studio · jeden dokument</span>
    <span>znaki/wiersz {W}</span>
    <span>wiersze {H}</span>
    <span>Layout-1..5</span>
  </footer>
</div>
<script>
const tabs=[...document.querySelectorAll('.tab')];
const docs=[...document.querySelectorAll('.doc')];
function show(id){{
  tabs.forEach(t=>t.classList.toggle('is-active', t.dataset.id===id));
  docs.forEach(d=>d.classList.toggle('is-active', d.id===id));
  history.replaceState(null,'','#'+id);
}}
tabs.forEach(t=>t.addEventListener('click',()=>show(t.dataset.id)));
if(location.hash) show(location.hash.slice(1));
</script>
</body>
</html>
"""


def manifest() -> dict:
    arts = []
    for lid, title, role, *_ in LAYOUTS:
        path = "README.md" if lid == "Layout-0" else f"layouts/{lid}.md"
        arts.append(
            {
                "id": lid.lower(),
                "role": role,
                "path": path,
                "editable": True,
                "required": True,
                "title": title,
            }
        )
    return {
        "schema_version": "1",
        "plan_id": "agents-workshop-layouts",
        "org": "vetcoders",
        "repo": "vibecrafted",
        "day": "2026_0817",
        "title": "Agents workshop · Layout-1..n",
        "owner": "grok",
        "authored_by": AUTHOR,
        "session_id": SESSION,
        "artifacts": arts,
    }


def write_all() -> None:
    frames = {
        "Layout-1": layout_1(),
        "Layout-2": layout_2(),
        "Layout-3": layout_3(),
        "Layout-4": layout_4(),
        "Layout-5": layout_5(),
    }
    docs_files = {
        "00-prawo.md": PRAWO,
        "Layout-1.md": md_for("Layout-1", "Warsztat", "brief", layout_1, LAYOUTS[1][4]),
        "Layout-2.md": md_for(
            "Layout-2", "Nowy agent", "brief", layout_2, LAYOUTS[2][4]
        ),
        "Layout-3.md": md_for(
            "Layout-3", "Po launchu", "brief", layout_3, LAYOUTS[3][4]
        ),
        "Layout-4.md": md_for(
            "Layout-4", "Voc drzwi", "brief", layout_4, LAYOUTS[4][4]
        ),
        "Layout-5.md": md_for(
            "Layout-5", "Nowy dispatch (później)", "brief", layout_5, LAYOUTS[5][4]
        ),
        "preview.html": preview_html(frames),
    }
    # drop leftover short mockups
    for stale in (
        "01-warsztat.md",
        "02-new-agent.md",
        "03-po-launchu.md",
        "04-voc-drzwi.md",
        "05-new-dispatch-pozniej.md",
    ):
        p = DOCS / stale
        if p.exists():
            p.unlink()

    DOCS.mkdir(parents=True, exist_ok=True)
    for name, text in docs_files.items():
        (DOCS / name).write_text(text, encoding="utf-8")
        print("docs", name, "bytes", len(text.encode()))

    PLAN.mkdir(parents=True, exist_ok=True)
    (PLAN / "layouts").mkdir(exist_ok=True)
    (PLAN / "README.md").write_text(PRAWO, encoding="utf-8")
    for lid in ("Layout-1", "Layout-2", "Layout-3", "Layout-4", "Layout-5"):
        (PLAN / "layouts" / f"{lid}.md").write_text(
            docs_files[f"{lid}.md"], encoding="utf-8"
        )
    (PLAN / "preview.html").write_text(docs_files["preview.html"], encoding="utf-8")
    (PLAN / "manifest.json").write_text(
        json.dumps(manifest(), indent=2) + "\n", encoding="utf-8"
    )
    print("plan", PLAN)


if __name__ == "__main__":
    write_all()
