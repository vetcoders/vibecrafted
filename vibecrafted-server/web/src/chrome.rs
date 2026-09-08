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
    Lifecycle,
    Activity,
    Structure,
    Scaffold,
    Guide,
}

impl ServerSection {
    fn nav_class(self, section: Self) -> &'static str {
        if self == section {
            "server-nav-link is-active"
        } else {
            "server-nav-link"
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
                            <strong>"Vibecrafted server"</strong>
                            <small>{format!("control plane · {}", env!("VC_SERVER_VERSION"))}</small>
                        </span>
                    </a>
                    <div class="server-navbar-actions">
                        <span class="server-status-pill">
                            <span class="server-status-dot" aria-hidden="true"></span>
                            {status}
                        </span>
                        <a class="server-navbar-action" href="/scaffold">"Open scaffold"</a>
                        <button
                            type="button"
                            class="server-theme-toggle"
                            aria-label="Toggle color theme"
                            aria-pressed="false"
                        >
                            "dark"
                        </button>
                    </div>
                </div>
            </header>

            <div class="server-app-body">
                <aside class="server-sidebar" aria-label="Vibecrafted server navigation">
                    <nav class="server-sidebar-nav">
                        <p class="server-nav-label">"Workspace"</p>
                        <a class=active.nav_class(ServerSection::Overview) href="/">
                            <span>"01"</span><strong>"Overview"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Workspaces) href="/workspaces">
                            <span>"02"</span><strong>"Workspaces"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Sessions) href="/sessions">
                            <span>"03"</span><strong>"Sessions"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Agents) href="/agents">
                            <span>"04"</span><strong>"Agent Manager"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Runs) href="/runs">
                            <span>"05"</span><strong>"Live runs"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Lifecycle) href="/lifecycle">
                            <span>"06"</span><strong>"Control"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Activity) href="/activity">
                            <span>"07"</span><strong>"Activity"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Structure) href="/structure">
                            <span>"08"</span><strong>"Structure"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Scaffold) href="/scaffold">
                            <span>"09"</span><strong>"Plans / Scaffold"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Guide) href="/guide">
                            <span>"10"</span><strong>"Guide"</strong>
                        </a>
                    </nav>
                    <div class="server-sidebar-note">
                        <span class="server-status-dot" aria-hidden="true"></span>
                        <p>
                            <strong>"Runtime truth"</strong>
                            <small>"read-only control-plane projection"</small>
                        </p>
                    </div>
                </aside>

                <main class="server-route-main">{children()}</main>
            </div>

            <nav class="server-mobile-nav" aria-label="Vibecrafted server mobile navigation">
                <a class=active.nav_class(ServerSection::Overview) href="/">
                    <span>"01"</span><strong>"Overview"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Workspaces) href="/workspaces">
                    <span>"02"</span><strong>"Workspaces"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Sessions) href="/sessions">
                    <span>"03"</span><strong>"Sessions"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Agents) href="/agents">
                    <span>"04"</span><strong>"Agents"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Runs) href="/runs">
                    <span>"05"</span><strong>"Runs"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Lifecycle) href="/lifecycle">
                    <span>"06"</span><strong>"Control"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Activity) href="/activity">
                    <span>"07"</span><strong>"Activity"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Structure) href="/structure">
                    <span>"08"</span><strong>"Structure"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Scaffold) href="/scaffold">
                    <span>"09"</span><strong>"Plans"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Guide) href="/guide">
                    <span>"10"</span><strong>"Guide"</strong>
                </a>
            </nav>
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
    button.textContent = next;
    button.setAttribute('aria-pressed', String(next === 'light'));
    try { localStorage.setItem('loct-theme', next); } catch (_) {}
  };
  apply(document.documentElement.dataset.theme);
  button.addEventListener('click', () => {
    apply(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  });
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
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>{title}</title>\n<script>{head_script}</script>\n<style>{tokens}</style>\n<style>{fonts}</style>\n<style>{main}</style>\n{head_html}\n</head>\n<body>\n{frame}\n<script>{control_script}</script>\n{tail_html}\n</body>\n</html>\n",
        title = escape_text(document.title),
        head_script = theme_head_script(),
        tokens = STYLE_TOKENS,
        fonts = STYLE_FONTS,
        main = STYLE_MAIN,
        head_html = document.head_html,
        frame = frame,
        control_script = theme_control_script(),
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
}
