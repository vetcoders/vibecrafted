#![cfg(feature = "ssr")]

use leptos::prelude::*;
use vibecrafted_server_web::app::UsagePage;
use vibecrafted_server_web::theme::provide_theme_context;

#[test]
fn usage_heatmap_lock() {
    let owner = Owner::new();
    let html = owner.with(|| {
        leptos_meta::provide_meta_context();
        provide_theme_context();
        UsagePage().to_html()
    });

    let heat = element_with_id(&html, "usage-chart-heat");
    assert!(
        heat.starts_with("<figure"),
        "heat figure was rewritten: {heat}"
    );
    assert!(
        heat.contains("id=\"usage-chart-heat\""),
        "rendered page lost the heat figure id"
    );
    assert!(
        html.contains("id=\"usage-chart-heat-title\""),
        "rendered heat figure lost its title"
    );
    assert!(
        html.contains("id=\"usage-chart-heat-plot\""),
        "rendered heat figure lost its plot"
    );
    assert_eq!(
        html.matches("id=\"usage-chart-heat\"").count(),
        1,
        "the heat figure must stay a single figure"
    );

    let hero = element_with_class(&html, "usage-hero-kicker");
    assert!(
        visible_text(&hero) == "Known tokens",
        "Known tokens hero was rewritten: {hero}"
    );
    let total = element_with_id(&html, "usage-total-tokens");
    assert!(
        total.starts_with("<dd"),
        "Known tokens total was rewritten: {total}"
    );
    assert!(
        html.contains("id=\"usage-hero-tokens\""),
        "rendered page lost the Known tokens hero figure"
    );

    let sidebar = region_from_class(&html, "server-sidebar", "</aside>");
    let usage_doors = anchors(&sidebar)
        .into_iter()
        .filter(|(href, _)| href == "/usage")
        .collect::<Vec<_>>();
    assert_eq!(
        usage_doors.len(),
        1,
        "sidebar must have one Costs & usage door to /usage, found {usage_doors:?}"
    );
    assert_eq!(
        usage_doors[0].1, "Costs & usage",
        "Costs & usage door must point at /usage, found {usage_doors:?}"
    );
}

fn element_with_id(html: &str, id: &str) -> String {
    let needle = format!("id=\"{id}\"");
    let id_at = html
        .find(&needle)
        .unwrap_or_else(|| panic!("rendered page has no {needle}"));
    let start = html[..id_at].rfind('<').unwrap_or(id_at);
    let rest = &html[start..];
    let end = rest
        .find('>')
        .map(|offset| offset + 1)
        .unwrap_or(rest.len());
    rest[..end].to_string()
}

fn element_with_class(html: &str, class_name: &str) -> String {
    let needle = format!("class=\"{class_name}\"");
    let class_at = html
        .find(&needle)
        .unwrap_or_else(|| panic!("rendered page has no {needle}"));
    let start = html[..class_at].rfind('<').unwrap_or(class_at);
    let rest = &html[start..];
    let end = rest.find("</").unwrap_or(rest.len());
    rest[..end].to_string()
}

fn region_from_class<'a>(html: &'a str, class_name: &str, end_marker: &str) -> &'a str {
    let needle = format!("class=\"{class_name}\"");
    let class_at = html
        .find(&needle)
        .unwrap_or_else(|| panic!("rendered page has no {needle}"));
    let start = html[..class_at].rfind('<').unwrap_or(class_at);
    let rest = &html[start..];
    let end = rest
        .find(end_marker)
        .unwrap_or_else(|| panic!("rendered page has no {end_marker} after {needle}"));
    &rest[..end]
}

fn anchors(html: &str) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut rest = html;
    while let Some(start) = rest.find("<a ") {
        rest = &rest[start..];
        let Some(end) = rest.find("</a>") else {
            break;
        };
        let anchor = &rest[..end];
        found.push((href_of(anchor).unwrap_or_default(), visible_text(anchor)));
        rest = &rest[end + 4..];
    }
    found
}

fn href_of(tag: &str) -> Option<String> {
    let key = "href=\"";
    let start = tag.find(key)? + key.len();
    let end = tag[start..].find('"')? + start;
    Some(tag[start..end].to_string())
}

fn visible_text(fragment: &str) -> String {
    let mut text = String::new();
    let mut in_tag = false;
    for ch in fragment.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => text.push(ch),
            _ => {}
        }
    }
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
}
