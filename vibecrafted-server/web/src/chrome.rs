//! Shared operator chrome for every Leptos-owned vc-server route.
//!
//! The raw scaffold editor has its own document renderer, but mirrors this
//! route vocabulary so an operator never lands in a navigation dead end.

use leptos::prelude::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ServerSection {
    Overview,
    Workspaces,
    Sessions,
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
                        <a class=active.nav_class(ServerSection::Runs) href="/runs">
                            <span>"04"</span><strong>"Agent Manager"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Lifecycle) href="/lifecycle">
                            <span>"05"</span><strong>"Control"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Activity) href="/activity">
                            <span>"06"</span><strong>"Activity"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Structure) href="/structure">
                            <span>"07"</span><strong>"Structure"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Scaffold) href="/scaffold">
                            <span>"08"</span><strong>"Plans / Scaffold"</strong>
                        </a>
                        <a class=active.nav_class(ServerSection::Guide) href="/guide">
                            <span>"09"</span><strong>"Guide"</strong>
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
                <a class=active.nav_class(ServerSection::Runs) href="/runs">
                    <span>"04"</span><strong>"Agents"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Lifecycle) href="/lifecycle">
                    <span>"05"</span><strong>"Control"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Activity) href="/activity">
                    <span>"06"</span><strong>"Activity"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Structure) href="/structure">
                    <span>"07"</span><strong>"Structure"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Scaffold) href="/scaffold">
                    <span>"08"</span><strong>"Plans"</strong>
                </a>
                <a class=active.nav_class(ServerSection::Guide) href="/guide">
                    <span>"09"</span><strong>"Guide"</strong>
                </a>
            </nav>
        </div>
    }
}
