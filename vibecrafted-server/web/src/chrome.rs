//! Shared operator chrome for every vc-server HTML route.
//!
//! `ServerFrame` is the only owner of the global navigation vocabulary
//! (navbar, sidebar, mobile nav, theme toggle). Leptos pages mount it
//! directly; raw-HTML routes such as the scaffold studio go through
//! [`render_document`], which wraps their markup in the very same frame so
//! no route ever ships a second sidebar or an empty page without navigation.

use leptos::prelude::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ServerSection {
    Overview,
    Workspaces,
    Sessions,
    Agents,
    Runs,
    Transcripts,
    Lifecycle,
    Activity,
    Structure,
    Scaffold,
    Guide,
    Frame,
}

impl ServerSection {
    /// Five primary views. Catalog pages (workspaces, sessions, agents, live
    /// runs, control, activity, guide) stay reachable — they highlight Overview
    /// in the primary nav and themselves in the rail. AICX lives under Structure.
    fn family(self) -> Self {
        match self {
            Self::Workspaces
            | Self::Sessions
            | Self::Agents
            | Self::Runs
            | Self::Lifecycle
            | Self::Activity
            | Self::Guide => Self::Overview,
            other => other,
        }
    }

    fn nav_class(self, section: Self) -> &'static str {
        if self.family() == section {
            "server-nav-link is-active"
        } else {
            "server-nav-link"
        }
    }

    fn rail_class(self, section: Self) -> &'static str {
        if self == section {
            "server-rail-link is-active"
        } else {
            "server-rail-link"
        }
    }
}

#[component]
pub fn ServerFrame(active: ServerSection, status: String, children: Children) -> impl IntoView {
    view! {
        <div class="server-app-shell">
            <header class="server-navbar">
                <div class="server-navbar-inner">
                    <a class="server-navbar-brand" href="/" aria-label="Vibecrafted server overview">
                        <span class="server-brand-mark" aria-hidden="true">"⌁"</span>
                        <span class="server-brand-copy">
                            <strong>"Vibecrafted"</strong>
                            <small>{env!("VC_SERVER_VERSION")}</small>
                        </span>
                    </a>
                    <div class="server-navbar-actions">
                        <span class="server-status-pill">
                            <span class="server-status-dot" aria-hidden="true"></span>
                            {status}
                        </span>
                        <div class="server-focus-toggle" role="group" aria-label="Focus">
                            <button type="button" class="server-navbar-action" data-focus-mode="fleet">"Fleet"</button>
                            <button type="button" class="server-navbar-action" data-focus-mode="project">"Project"</button>
                        </div>
                        <button
                            type="button"
                            class="server-theme-toggle"
                            aria-label="Switch to light theme"
                        >
                            "light"
                        </button>
                    </div>
                </div>
            </header>

            <div class="server-app-body">
                <aside class="server-sidebar" aria-label="Vibecrafted server navigation">
                    <nav class="server-sidebar-nav">
                        <a class=active.nav_class(ServerSection::Overview) href="/">
                            <strong>"Overview"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Transcripts) href="/transcripts">
                            <strong>"Transcripts"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Structure) href="/structure">
                            <strong>"Structure"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Scaffold) href="/scaffold">
                            <strong>"Plans"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Frame) href="/frame">
                            <strong>"Frame"</strong>
                        </a>
                    </nav>
                    <ul class="server-sidebar-rail" aria-label="Overview filters and catalogs">
                        <li>
                            <a class="server-rail-link" href="/?rail=active" data-rail-filter="active">
                                <span>"Active"</span>
                            </a>
                        </li>
                        <li>
                            <a class="server-rail-link" href="/?rail=failures" data-rail-filter="failures">
                                <span>"Failures"</span>
                            </a>
                        </li>
                        <li>
                            <a class="server-rail-link" href="/?rail=health" data-rail-filter="health">
                                <span>"Health"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Workspaces) href="/workspaces">
                                <span>"Workspaces"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Sessions) href="/sessions">
                                <span>"Sessions"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Agents) href="/agents">
                                <span>"Agents"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Runs) href="/runs">
                                <span>"Live"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Lifecycle) href="/lifecycle">
                                <span>"Control"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Activity) href="/activity">
                                <span>"Activity"</span>
                            </a>
                        </li>
                        <li>
                            <a class=active.rail_class(ServerSection::Guide) href="/guide">
                                <span>"Guide"</span>
                            </a>
                        </li>
                    </ul>
                    <div class="server-sidebar-note">
                        <p>
                            <strong>"Focus"</strong>
                            <small class="server-focus-caption">"Fleet · all workspaces"</small>
                        </p>
                    </div>
                </aside>

                <main class="server-route-main">{children()}</main>
            </div>

            <nav class="server-mobile-nav" aria-label="Vibecrafted server mobile navigation">
                <a class=active.nav_class(ServerSection::Overview) href="/">
                    <strong>"Overview"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Transcripts) href="/transcripts">
                    <strong>"Logs"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Structure) href="/structure">
                    <strong>"Structure"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Scaffold) href="/scaffold">
                    <strong>"Plans"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Frame) href="/frame">
                    <strong>"Frame"</strong>
                </a>
            </nav>
            <div id="vc-ppm" class="vc-ppm" hidden>
                <button type="button" data-ppm-action="copy-id">"Copy id"</button>
                <button type="button" data-ppm-action="copy-url">"Copy link"</button>
                <button type="button" data-ppm-action="open">"Open"</button>
                <button type="button" data-ppm-action="copy-transcript">"Copy transcript"</button>
                <button type="button" data-ppm-action="search-aicx">"Search AICX"</button>
            </div>
        </div>
    }
}

/// Document-level assets every vc-server HTML route shares. The Leptos shell
/// in `app.rs` and raw-HTML routes read the same bytes, so the product has
/// exactly one stylesheet set and one theme script.
#[cfg(feature = "ssr")]
pub const STYLE_TOKENS: &str = include_str!("../styles/tokens.css");
#[cfg(feature = "ssr")]
pub const STYLE_FONTS: &str = include_str!("../styles/fonts.css");
#[cfg(feature = "ssr")]
pub const STYLE_MAIN: &str = include_str!("../styles/main.css");

/// Restores the persisted theme before first paint (no flash of wrong theme).
#[cfg(feature = "ssr")]
pub fn theme_head_script() -> &'static str {
    r#"(() => {
  try {
    const saved = localStorage.getItem('loct-theme');
    document.documentElement.dataset.theme = saved === 'light' ? 'light' : 'dark';
  } catch (_) {
    document.documentElement.dataset.theme = 'dark';
  }
})();"#
}

/// Wires the navbar theme toggle rendered by [`ServerFrame`].
#[cfg(feature = "ssr")]
pub fn theme_control_script() -> &'static str {
    r#"(() => {
  const button = document.querySelector('.server-theme-toggle');
  if (!button) return;
  const apply = (theme) => {
    const next = theme === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    const target = next === 'light' ? 'dark' : 'light';
    button.textContent = target;
    button.setAttribute('aria-label', `Switch to ${target} theme`);
    try { localStorage.setItem('loct-theme', next); } catch (_) {}
  };
  apply(document.documentElement.dataset.theme);
  button.addEventListener('click', () => {
    apply(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  });
})();"#
}

/// Restores Fleet/Project focus before first paint, same contract as theme.
#[cfg(feature = "ssr")]
pub fn operator_head_script() -> &'static str {
    r#"(() => {
  try {
    const saved = localStorage.getItem('vc-focus-mode');
    document.documentElement.dataset.focus = saved === 'project' ? 'project' : 'fleet';
  } catch (_) {
    document.documentElement.dataset.focus = 'fleet';
  }
})();"#
}

/// Focus toggle, copy buttons, and the operator context menu. Shared by the
/// Leptos shell and every raw-HTML route so Copy / PPM / Fleet·Project exist
/// on Plans as well as Overview.
#[cfg(feature = "ssr")]
pub fn operator_desk_script() -> &'static str {
    r#"(() => {
  const FOCUS_KEY = 'vc-focus-mode';
  const ROOT_KEY = 'vc-focus-root';
  const html = document.documentElement;
  const caption = document.querySelector('.server-focus-caption');
  const menu = document.getElementById('vc-ppm');
  let menuTarget = null;

  const selectedRoot = () => {
    const ctx = document.getElementById('vc-focus-context');
    const live = ctx && ctx.getAttribute('data-selected-workspace-root');
    if (live) return live;
    try { return localStorage.getItem(ROOT_KEY) || ''; } catch (_) { return ''; }
  };

  const repoName = (path) => {
    const trimmed = String(path || '').replace(/[\\/]+$/, '');
    const parts = trimmed.split(/[\\/]/);
    return parts[parts.length - 1] || '';
  };

  const applyFocus = (mode) => {
    const next = mode === 'project' ? 'project' : 'fleet';
    html.dataset.focus = next;
    document.querySelectorAll('[data-focus-mode]').forEach((btn) => {
      btn.classList.toggle('is-active', btn.getAttribute('data-focus-mode') === next);
    });
    const root = selectedRoot();
    try { localStorage.setItem(FOCUS_KEY, next); } catch (_) {}
    if (caption) {
      caption.textContent = next === 'project'
        ? (root ? ('Project · ' + root) : 'Project · pick a workspace')
        : 'Fleet · all workspaces';
    }
    const repo = repoName(root);
    document.querySelectorAll('[data-focus-root], [data-focus-repo]').forEach((el) => {
      const searchMiss = el.getAttribute('data-search-hit') === '0';
      let focusMiss = false;
      if (next === 'project' && root) {
        const focusRoot = el.getAttribute('data-focus-root');
        if (focusRoot) {
          focusMiss = focusRoot !== root;
        } else {
          const focusRepo = el.getAttribute('data-focus-repo') || '';
          focusMiss = !focusRepo || focusRepo !== repo;
        }
      }
      el.hidden = searchMiss || focusMiss;
    });
  };

  const ctx = document.getElementById('vc-focus-context');
  const ctxRoot = ctx && ctx.getAttribute('data-selected-workspace-root');
  if (ctxRoot) {
    try { localStorage.setItem(ROOT_KEY, ctxRoot); } catch (_) {}
  } else {
    const selectedCard = document.querySelector('.workspace-card[data-selected="1"][data-focus-root]');
    if (selectedCard) {
      try { localStorage.setItem(ROOT_KEY, selectedCard.getAttribute('data-focus-root') || ''); } catch (_) {}
    }
  }

  applyFocus(html.dataset.focus);
  document.documentElement.addEventListener('vc-focus-refresh', () => applyFocus(html.dataset.focus));
  document.querySelectorAll('[data-focus-mode]').forEach((btn) => {
    btn.addEventListener('click', () => applyFocus(btn.getAttribute('data-focus-mode')));
  });

  const copyText = async (text) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
  };

  document.addEventListener('click', (event) => {
    const copy = event.target.closest('[data-copy]');
    if (copy) {
      event.preventDefault();
      copyText(copy.getAttribute('data-copy') || copy.textContent || '');
    }
    const workspace = event.target.closest('.workspace-card[data-focus-root]');
    if (workspace) {
      const root = workspace.getAttribute('data-focus-root') || '';
      try { localStorage.setItem(ROOT_KEY, root); } catch (_) {}
      const ctx = document.getElementById('vc-focus-context');
      if (ctx) ctx.setAttribute('data-selected-workspace-root', root);
      applyFocus(html.dataset.focus);
    }
    if (menu && !event.target.closest('#vc-ppm')) menu.hidden = true;
  });

  const transcriptUrl = (target) => {
    const named = target.getAttribute('data-transcript-url') || '';
    if (named) return named;
    if (target.getAttribute('data-ppm') !== 'run') return '';
    const id = target.getAttribute('data-run-id') || '';
    return id ? ('/api/control/runs/' + id + '/transcript') : '';
  };

  const bindMenu = (target) => {
    if (!menu || !target) return;
    menuTarget = target;
    const id = target.getAttribute('data-run-id') || target.getAttribute('data-copy-id') || '';
    const href = target.getAttribute('data-href') || (id ? ('/run/' + id) : '');
    const transcript = transcriptUrl(target);
    menu.querySelectorAll('[data-ppm-action]').forEach((btn) => {
      const action = btn.getAttribute('data-ppm-action');
      btn.hidden = (action === 'copy-id' && !id)
        || (action === 'open' && !href)
        || (action === 'copy-transcript' && !transcript)
        || (action === 'search-aicx' && !id);
    });
    menu.hidden = false;
    const rect = target.getBoundingClientRect();
    menu.style.left = Math.min(rect.left + window.scrollX, window.scrollX + window.innerWidth - 220) + 'px';
    menu.style.top = (rect.bottom + window.scrollY + 4) + 'px';
  };

  document.addEventListener('contextmenu', (event) => {
    const row = event.target.closest('[data-ppm]');
    if (!row) return;
    event.preventDefault();
    bindMenu(row);
  });

  const applyRail = (key) => {
    const next = (key === 'active' || key === 'failures' || key === 'health') ? key : '';
    document.querySelectorAll('[data-rail-filter]').forEach((el) => {
      el.classList.toggle('is-active', next !== '' && el.getAttribute('data-rail-filter') === next);
    });
    const bands = document.querySelectorAll('[data-rail-band]');
    if (!bands.length) return;
    bands.forEach((band) => {
      const id = band.getAttribute('data-rail-band');
      let hide = false;
      if (id === 'health') hide = next !== 'health';
      else if (id === 'recent') hide = next !== '';
      else if (id === 'active') hide = next === 'failures';
      else if (id === 'failures') hide = next === 'active';
      band.hidden = hide;
    });
  };
  applyRail(new URLSearchParams(location.search).get('rail') || '');
  document.querySelectorAll('[data-rail-filter]').forEach((el) => {
    el.addEventListener('click', (event) => {
      if (location.pathname !== '/') return;
      event.preventDefault();
      const key = el.getAttribute('data-rail-filter') || '';
      const current = new URLSearchParams(location.search).get('rail') || '';
      const next = current === key ? '' : key;
      const url = next ? ('/?rail=' + encodeURIComponent(next)) : '/';
      history.replaceState({}, '', url);
      applyRail(next);
    });
  });

  const fillInspector = async (row) => {
    const pane = document.getElementById('overview-inspector');
    if (!pane || !row) return;
    document.querySelectorAll('.run-table tbody tr.is-selected').forEach((el) => el.classList.remove('is-selected'));
    row.classList.add('is-selected');
    const id = row.getAttribute('data-run-id') || '';
    const href = row.getAttribute('data-href') || (id ? ('/run/' + id) : '');
    const report = row.getAttribute('data-report') || '';
    const error = row.getAttribute('data-error') || '';
    const title = pane.querySelector('[data-inspector-id]');
    const reportEl = pane.querySelector('[data-inspector-report]');
    const errorEl = pane.querySelector('[data-inspector-error]');
    const open = pane.querySelector('[data-inspector-open]');
    const tail = pane.querySelector('[data-inspector-tail]');
    if (title) title.textContent = id || 'Nothing selected';
    if (reportEl) reportEl.textContent = report || 'No report.md yet';
    if (errorEl) {
      errorEl.textContent = error;
      errorEl.hidden = !error;
    }
    if (open) {
      open.href = href || '#';
      open.hidden = !href;
    }
    if (tail) {
      const url = row.getAttribute('data-transcript-url') || '';
      tail.textContent = url ? 'Loading transcript…' : 'No transcript lines yet.';
      if (url) {
        try {
          const response = await fetch(url, { credentials: 'same-origin' });
          const payload = await response.json();
          tail.textContent = payload.body || 'No transcript lines yet.';
        } catch (_) {
          tail.textContent = 'Transcript unavailable.';
        }
      }
    }
  };
  document.querySelectorAll('.run-table tbody tr[data-run-id]').forEach((row, index) => {
    row.addEventListener('click', (event) => {
      if (event.target.closest('a, button')) return;
      fillInspector(row);
    });
    if (index === 0) fillInspector(row);
  });

  if (menu) {
    menu.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-ppm-action]');
      if (!btn || !menuTarget) return;
      const action = btn.getAttribute('data-ppm-action');
      const id = menuTarget.getAttribute('data-run-id') || menuTarget.getAttribute('data-copy-id') || '';
      const href = menuTarget.getAttribute('data-href') || (id ? ('/run/' + id) : '');
      const transcript = transcriptUrl(menuTarget);
      menu.hidden = true;
      if (action === 'copy-id') return copyText(id);
      if (action === 'copy-url') return copyText(href ? (location.origin + href) : location.href);
      if (action === 'open' && href) { location.href = href; return; }
      if (action === 'search-aicx' && id) { location.href = '/aicx?q=' + encodeURIComponent(id); return; }
      if (action === 'copy-transcript' && transcript) {
        try {
          const response = await fetch(transcript, { credentials: 'same-origin' });
          const payload = await response.json();
          await copyText(payload.body || '');
        } catch (_) {}
      }
    });
  }
})();"#
}

/// A raw-HTML route rendered inside the shared operator chrome.
///
/// The route owns its canvas (`body_html`), its own `<style>` additions
/// (`head_html`) and its scripts (`tail_html`); the chrome owns everything
/// else: document head, stylesheets, navbar, sidebar, theme. All three
/// fragments are trusted server-generated markup — callers escape user data
/// before they get here, exactly as they did for their standalone documents.
#[cfg(feature = "ssr")]
#[derive(Clone, Copy, Debug)]
pub struct ServerDocument<'a> {
    pub title: &'a str,
    pub active: ServerSection,
    pub status: &'a str,
    pub head_html: &'a str,
    pub body_html: &'a str,
    pub tail_html: &'a str,
}

/// Renders a complete HTML document: shared head + [`ServerFrame`] around the
/// route canvas. The canvas is mounted as `div.server-route-document` inside
/// `main.server-route-main`, so it is the one `<main>` of the page and the
/// route must not emit another.
#[cfg(feature = "ssr")]
pub fn render_document(document: &ServerDocument<'_>) -> String {
    let active = document.active;
    let status = document.status.to_owned();
    let canvas = document.body_html.to_owned();
    let owner = Owner::new();
    let frame = owner.with(|| {
        view! {
            <ServerFrame active=active status=status>
                <div class="server-route-document" inner_html=canvas></div>
            </ServerFrame>
        }
        .to_html()
    });
    format!(
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>{title}</title>\n<script>{head_script}</script>\n<script>{operator_head}</script>\n<style>{tokens}</style>\n<style>{fonts}</style>\n<style>{main}</style>\n{head_html}\n</head>\n<body>\n{frame}\n<script>{control_script}</script>\n<script>{operator_desk}</script>\n{tail_html}\n</body>\n</html>\n",
        title = escape_text(document.title),
        head_script = theme_head_script(),
        operator_head = operator_head_script(),
        tokens = STYLE_TOKENS,
        fonts = STYLE_FONTS,
        main = STYLE_MAIN,
        head_html = document.head_html,
        frame = frame,
        control_script = theme_control_script(),
        operator_desk = operator_desk_script(),
        tail_html = document.tail_html,
    )
}

#[cfg(feature = "ssr")]
fn escape_text(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

#[cfg(all(test, feature = "ssr"))]
mod tests {
    use super::*;

    fn count(haystack: &str, needle: &str) -> usize {
        haystack.matches(needle).count()
    }

    /// `render_document` embeds three complete stylesheets, so counting a class
    /// name over the whole document counts CSS selectors as if they were
    /// markup: `main.css` alone declares `.server-theme-toggle` six times.
    /// Contracts about the live DOM and its scripts are measured here, on the
    /// document with the embedded sheets stripped; contracts about the sheets
    /// are measured against `STYLE_TOKENS` / `STYLE_MAIN` directly.
    fn live_layer(html: &str) -> String {
        let mut out = String::with_capacity(html.len());
        let mut rest = html;
        while let Some(open) = rest.find("<style>") {
            out.push_str(&rest[..open]);
            let after = &rest[open + "<style>".len()..];
            rest = match after.find("</style>") {
                Some(close) => &after[close + "</style>".len()..],
                None => "",
            };
        }
        out.push_str(rest);
        out
    }

    #[test]
    fn document_wraps_raw_canvas_in_one_server_frame() {
        let html = render_document(&ServerDocument {
            title: "scaffold <review> - vc-server",
            active: ServerSection::Scaffold,
            status: "3 plans",
            head_html: "<style>.x{}</style>",
            body_html: "<div class=\"canvas\">hello</div>",
            tail_html: "<script>/* tail */</script>",
        });

        assert!(html.starts_with("<!DOCTYPE html>"));
        assert!(html.contains("<title>scaffold &lt;review&gt; - vc-server</title>"));
        assert_eq!(count(&html, "class=\"server-navbar\""), 1);
        assert_eq!(count(&html, "class=\"server-sidebar\""), 1);
        assert_eq!(count(&html, "<main class=\"server-route-main\">"), 1);
        assert_eq!(count(&html, "<main"), 1, "the frame owns the only <main>");
        assert!(html.contains("href=\"/scaffold\" class=\"server-nav-link is-active\""));
        assert!(html.contains(
            "<div class=\"server-route-document\"><div class=\"canvas\">hello</div></div>"
        ));
        assert!(html.contains("3 plans"));
        assert!(html.contains("<style>.x{}</style>"));
        assert!(html.contains("<script>/* tail */</script>"));
        assert!(html.contains("server-theme-toggle"));
        let head_end = html.find("</head>").expect("head");
        assert!(html[..head_end].contains(theme_head_script()));
        assert!(html[head_end..].contains(theme_control_script()));
        // Shared bytes, not a second stylesheet copy.
        assert!(html.contains(STYLE_MAIN));
    }

    /// Founder contract: the button names where a click takes you, never the
    /// theme you are already in. Server-rendered default is dark, so the button
    /// must offer "light"; the control script must keep flipping the noun.
    #[test]
    fn theme_toggle_names_its_destination_not_the_active_theme() {
        let html = render_document(&ServerDocument {
            title: "t",
            active: ServerSection::Overview,
            status: "ok",
            head_html: "",
            body_html: "",
            tail_html: "",
        });

        // Dark is the pre-paint default, so the offer is light.
        assert!(html.contains("aria-label=\"Switch to light theme\""));
        // The first `server-theme-toggle` in the raw document is a CSS
        // selector, not the control — slice the live layer or this reads the
        // stylesheet and passes for the wrong reason.
        let live = live_layer(&html);
        let button_start = live.find("server-theme-toggle").expect("toggle");
        let button_end = live[button_start..].find("</button>").expect("close") + button_start;
        let button = &live[button_start..button_end];
        assert!(button.contains("light"), "dark active must offer light");
        assert!(
            !button.contains(">dark<"),
            "the button must not name the active theme"
        );

        // And the destination keeps flipping at runtime.
        let script = theme_control_script();
        assert!(
            script.contains("const target = next === 'light' ? 'dark' : 'light';"),
            "the label must always be the theme a click switches TO"
        );
        assert!(
            script.contains("button.textContent = target;"),
            "the visible noun must follow the destination"
        );
        assert!(
            script.contains("button.setAttribute('aria-label', `Switch to ${target} theme`);"),
            "the accessible name must follow the same destination"
        );
    }

    /// The console half of `main.css` — everything from the shell marker down.
    /// The legacy marketing sections above it (hero, blog, pricing) have no
    /// route in this product and deliberately keep their own scale, so a
    /// whole-file assertion would be measuring dead bytes.
    fn console_css() -> &'static str {
        const MARK: &str = "/* W1-b_web-shell: vibecrafted server console shell */";
        let at = STYLE_MAIN.find(MARK).expect("console section marker");
        &STYLE_MAIN[at..]
    }

    /// Founder's binding reference: the native reconnect/recovery card. These
    /// are `CommandDeckPalette.resolved(...)` transcribed to sRGB — if someone
    /// repaints the console without repainting the native deck, this fails.
    #[test]
    fn console_tokens_transcribe_the_native_command_deck_palette() {
        for (role, hex) in [
            ("surface (dark)", "#21211f"),
            ("surfaceRaised (dark)", "#2e2b29"),
            ("ink (dark)", "#ede6d6"),
            ("muted (dark)", "#b3ab9e"),
            ("amber", "#e39e38"),
            ("destructive (dark)", "#db5247"),
            ("stroke (dark)", "#524d45"),
            ("surface (light)", "#f5f0e3"),
            ("surfaceRaised (light)", "#fcfaf2"),
            ("ink (light)", "#2e2b26"),
            (
                "amber (light, increased variant — see tokens.css)",
                "#945705",
            ),
            ("stroke (light)", "#c7bdad"),
            ("surface (dark, increased)", "#12120f"),
            ("ink (dark, increased)", "#faf5eb"),
            ("amber (increased)", "#ffb82e"),
        ] {
            assert!(
                STYLE_TOKENS.contains(hex),
                "{role} must be the native value {hex}"
            );
        }

        // CommandDeckMetrics: one radius, one stroke, one duration.
        assert!(STYLE_TOKENS.contains("--radius-surface: 8px;"));
        assert!(STYLE_TOKENS.contains("--stroke-width: 1px;"));
        assert!(STYLE_TOKENS.contains("--stroke-width: 1.5px;"));
        assert!(STYLE_TOKENS.contains("--motion-base: 220ms;"));
    }

    /// The two-colour editorial split had collapsed into one dead grey on both
    /// names, which is how a focus ring became invisible. The native deck
    /// carries exactly one accent; so does the console now.
    #[test]
    fn the_console_carries_one_live_accent_and_a_usable_focus_ring() {
        assert!(
            !STYLE_TOKENS.contains("#d4d4d8"),
            "the dead grey accent must not survive anywhere in the token layer"
        );
        assert!(STYLE_TOKENS.contains("--amber: #e39e38;"));
        assert!(
            STYLE_TOKENS.contains("--teal: var(--amber);"),
            "the second accent name must converge on the one accent, not hold a rival value"
        );
        assert!(
            STYLE_TOKENS.contains("--focus-ring: var(--accent);"),
            "focus must ride the live accent"
        );
        assert!(
            STYLE_TOKENS.contains("--accent-narrative: var(--accent);")
                && STYLE_TOKENS.contains("--accent-interaction: var(--accent);"),
            "both legacy accent roles must converge instead of being overridden downstream"
        );
    }

    /// The native card is one raised surface plus one 1px stroke: no shadow,
    /// no glow, no blur. This is the assertion that keeps the main view
    /// belonging to the same app as the reconnect card.
    #[test]
    fn console_surfaces_are_flat_stroked_and_share_one_radius() {
        let css = console_css();
        for (banned, why) in [
            ("--radius-xl", "24px radii are not the deck's 8px"),
            ("--radius-lg", "16px radii are not the deck's 8px"),
            ("--radius-md", "12px radii are not the deck's 8px"),
            ("--radius-sm", "one radius owner only: --radius-surface"),
            ("--shadow-hover", "the native card has no resting shadow"),
            ("radial-gradient", "no decorative glow on an operator desk"),
            ("linear-gradient", "no decorative glow on an operator desk"),
            ("backdrop-filter", "no gratuitous blur"),
            (
                "rgba(255, 255, 255",
                "nested surfaces must be theme-aware, not white alpha",
            ),
        ] {
            assert!(
                !css.contains(banned),
                "console css still contains {banned}: {why}"
            );
        }
        assert!(css.contains("var(--radius-surface)"));
        assert!(css.contains("var(--stroke-width) solid var(--border-subtle)"));
    }

    /// Status, Fleet/Project, and the theme toggle sit in one actions row.
    /// They must share one chip plane (radius, stroke, fill, padding). A
    /// capsule status next to 8px controls — or a UA-styled `<button>` next
    /// to an `<a>` — is the split the Founder marked on the live navbar.
    #[test]
    fn navbar_actions_share_one_chip_plane() {
        let css = console_css();
        let start = css
            .find(".server-navbar-actions {")
            .expect("navbar actions cluster");
        let end = css[start..]
            .find(".server-status-dot")
            .expect("cluster ends at the status dot")
            + start;
        let cluster = &css[start..end];

        assert!(
            cluster
                .contains(".server-status-pill,\n.server-navbar-action,\n.server-theme-toggle {"),
            "the three siblings must be one rule, not three restyles"
        );
        assert!(
            cluster.contains("border-radius: var(--chip-stack-radius);"),
            "navbar chips reuse the existing chip-stack radius, not a second shape"
        );
        assert!(
            cluster.contains("appearance: none;"),
            "the <button> toggle must drop UA chrome so it matches the <a>"
        );
        assert!(
            !cluster.contains("999px"),
            "a capsule in this row is a second radius"
        );
        assert!(
            !cluster.contains("border-radius: var(--radius-surface);"),
            "do not re-radius the interactive pair after the shared rule"
        );
    }

    /// The operator desk is a native surface: clickable links keep the
    /// arrow. `cursor: pointer` is the web pointing-hand.
    #[test]
    fn console_clickable_links_keep_the_arrow_cursor() {
        let css = console_css();
        assert!(
            !css.contains("cursor: pointer"),
            "the desk must not transform the pointer into a hand"
        );
        assert!(
            css.contains(".server-app-shell a,\n.server-app-shell button,"),
            "one owner must pin the arrow on every clickable link and control"
        );
        assert!(
            css.contains(".overview-structure-line a {\n  cursor: default;\n}"),
            "the Overview Structure/Plans line is a link row, not a hand"
        );
    }

    /// Focus had seven separate `outline: none` suppressions and signalled
    /// itself with a border tint, which is not a focus indicator. One owner now.
    #[test]
    fn the_console_has_exactly_one_focus_owner() {
        let css = console_css();
        assert!(
            !css.contains("outline: none"),
            "no route may suppress the focus indicator"
        );
        assert_eq!(
            css.matches(".server-app-shell :focus-visible").count(),
            2,
            "one focus rule plus its increased-contrast refinement"
        );
        assert!(css.contains("outline: 2px solid var(--focus-ring);"));
    }

    /// The deck honours reduced motion and thickens its stroke at increased
    /// contrast. The console shipped neither before this pass.
    #[test]
    fn accessibility_preferences_reach_the_console() {
        let css = console_css();
        assert!(css.contains("@media (prefers-reduced-motion: reduce)"));
        assert!(css.contains("@media (prefers-contrast: more)"));
        assert!(
            STYLE_TOKENS.contains("@media (prefers-contrast: more)"),
            "the increased-contrast palette must exist at the token layer, \
             so every route gets it without opting in"
        );
    }

    /// Adoption, not decoration: both shells embed the same two stylesheets,
    /// so a converged token reaches every route including the raw-HTML studio.
    #[test]
    fn every_route_receives_the_converged_tokens() {
        let html = render_document(&ServerDocument {
            title: "t",
            active: ServerSection::Overview,
            status: "ok",
            head_html: "",
            body_html: "",
            tail_html: "",
        });
        assert!(
            html.contains("#21211f"),
            "the native surface reaches the document"
        );
        assert!(html.contains("--focus-ring: var(--accent);"));
        assert_eq!(
            count(&html, STYLE_TOKENS),
            1,
            "one token sheet, not a copy per route"
        );
        assert_eq!(
            count(&html, STYLE_MAIN),
            1,
            "one main sheet, not a copy per route"
        );
    }

    /// One theme contract for every route: the raw-HTML document and the Leptos
    /// shell must ship the same pre-paint restore and the same control script.
    #[test]
    fn operator_desk_keeps_plan_cards_out_of_run_transcripts() {
        let script = operator_desk_script();
        assert!(script.contains("getAttribute('data-ppm') !== 'run'"));
        assert!(script.contains("data-focus-repo"));
        assert!(script.contains("vc-focus-refresh"));
        assert!(script.contains("data-search-hit"));
        assert!(script.contains("data-rail-filter"));
        assert!(script.contains("overview-inspector"));
        assert!(
            !script.contains("data-copy-id') || (id ? ('/api/control/runs/'"),
            "plan copy-id must not invent a run transcript URL"
        );
    }

    #[test]
    fn every_route_shares_one_theme_contract() {
        let html = render_document(&ServerDocument {
            title: "t",
            active: ServerSection::Scaffold,
            status: "ok",
            head_html: "",
            body_html: "",
            tail_html: "",
        });

        let live = live_layer(&html);
        assert_eq!(
            count(&live, "server-theme-toggle"),
            2,
            "one toggle, one script hook"
        );
        assert_eq!(
            count(&live, "loct-theme"),
            2,
            "one storage key, read once and written once"
        );
        assert!(theme_head_script().contains("localStorage.getItem('loct-theme')"));
        assert!(theme_control_script().contains("localStorage.setItem('loct-theme', next)"));
    }

    #[test]
    fn sidebar_is_five_primary_views_and_rail_keeps_catalog_routes() {
        let overview = live_layer(&render_document(&ServerDocument {
            title: "t",
            active: ServerSection::Overview,
            status: "ok",
            head_html: "",
            body_html: "",
            tail_html: "",
        }));
        let workspaces = live_layer(&render_document(&ServerDocument {
            title: "t",
            active: ServerSection::Workspaces,
            status: "ok",
            head_html: "",
            body_html: "",
            tail_html: "",
        }));
        let aicx = live_layer(&render_document(&ServerDocument {
            title: "t",
            active: ServerSection::Structure,
            status: "ok",
            head_html: "",
            body_html: "",
            tail_html: "",
        }));

        let sidebar = {
            let start = overview.find("class=\"server-sidebar\"").expect("sidebar");
            let end = overview[start..].find("</aside>").expect("aside") + start;
            &overview[start..end]
        };
        assert_eq!(
            count(sidebar, "class=\"server-nav-link"),
            5,
            "primary nav is five views"
        );
        assert!(sidebar.contains("href=\"/\" class=\"server-nav-link is-active\""));
        assert!(sidebar.contains("href=\"/transcripts\" class=\"server-nav-link\""));
        assert!(sidebar.contains("href=\"/structure\" class=\"server-nav-link\""));
        assert!(sidebar.contains("href=\"/scaffold\" class=\"server-nav-link\""));
        assert!(sidebar.contains("href=\"/frame\" class=\"server-nav-link\""));
        assert!(sidebar.contains("<strong>Plans</strong>"));
        assert!(!sidebar.contains("Plans / Scaffold"));
        assert!(!sidebar.contains(">01<"));
        assert!(!sidebar.contains("Open scaffold"));
        assert!(!sidebar.contains("href=\"/workspaces\" class=\"server-nav-link"));
        assert!(sidebar.contains("href=\"/workspaces\" class=\"server-rail-link\""));
        assert!(sidebar.contains("href=\"/sessions\" class=\"server-rail-link\""));
        assert!(sidebar.contains("href=\"/agents\" class=\"server-rail-link\""));
        assert!(sidebar.contains("href=\"/runs\" class=\"server-rail-link\""));
        assert!(sidebar.contains("href=\"/lifecycle\" class=\"server-rail-link\""));
        assert!(sidebar.contains("href=\"/activity\" class=\"server-rail-link\""));
        assert!(sidebar.contains("href=\"/guide\" class=\"server-rail-link\""));

        assert!(!overview.contains("href=\"/aicx\" class=\"server-navbar-action\""));
        assert!(!overview.contains("Open scaffold"));

        assert!(workspaces.contains("href=\"/\" class=\"server-nav-link is-active\""));
        assert!(workspaces.contains("href=\"/workspaces\" class=\"server-rail-link is-active\""));
        assert!(aicx.contains("href=\"/structure\" class=\"server-nav-link is-active\""));
        assert_eq!(count(&overview, "class=\"server-mobile-nav\""), 1);
        let mobile_start = overview
            .find("class=\"server-mobile-nav\"")
            .expect("mobile");
        let mobile_end =
            overview[mobile_start..].find("</nav>").expect("mobile end") + mobile_start;
        let mobile = &overview[mobile_start..mobile_end];
        assert_eq!(count(mobile, "class=\"server-nav-link"), 5);
    }
}
