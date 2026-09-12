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
        assert!(!button.contains(">dark<"), "the button must not name the active theme");

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
            ("amber (light, increased variant — see tokens.css)", "#945705"),
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
            ("rgba(255, 255, 255", "nested surfaces must be theme-aware, not white alpha"),
        ] {
            assert!(
                !css.contains(banned),
                "console css still contains {banned}: {why}"
            );
        }
        assert!(css.contains("var(--radius-surface)"));
        assert!(css.contains("var(--stroke-width) solid var(--border-subtle)"));
    }

    /// Status, "Open scaffold", and the theme toggle sit in one actions row.
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
            cluster.contains(".server-status-pill,\n.server-navbar-action,\n.server-theme-toggle {"),
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
        assert!(html.contains("#21211f"), "the native surface reaches the document");
        assert!(html.contains("--focus-ring: var(--accent);"));
        assert_eq!(count(&html, STYLE_TOKENS), 1, "one token sheet, not a copy per route");
        assert_eq!(count(&html, STYLE_MAIN), 1, "one main sheet, not a copy per route");
    }

    /// One theme contract for every route: the raw-HTML document and the Leptos
    /// shell must ship the same pre-paint restore and the same control script.
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
        assert_eq!(count(&live, "server-theme-toggle"), 2, "one toggle, one script hook");
        assert_eq!(count(&live, "loct-theme"), 2, "one storage key, read once and written once");
        assert!(theme_head_script().contains("localStorage.getItem('loct-theme')"));
        assert!(theme_control_script().contains("localStorage.setItem('loct-theme', next)"));
    }
}
