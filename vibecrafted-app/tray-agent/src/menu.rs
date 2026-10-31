use std::cell::RefCell;
use std::env;
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::ipc_client::{ClientKind, client_label};
use anyhow::Result;
use muda::accelerator::{Accelerator, Code, Modifiers};
use muda::{Menu, MenuItem, PredefinedMenuItem, Submenu};

use crate::types::MenuIds;

thread_local! {
    static STATUS_MENU_ITEM: RefCell<Option<MenuItem>> = const { RefCell::new(None) };
    static SERVICE_COUNT_MENU_ITEM: RefCell<Option<MenuItem>> = const { RefCell::new(None) };
    static RECENT_RUNS_SUBMENU: RefCell<Option<Submenu>> = const { RefCell::new(None) };
    static RECENT_RUN_ITEMS: RefCell<Vec<MenuItem>> = const { RefCell::new(Vec::new()) };
    static CONTINUE_ONBOARDING_MENU_ITEM: RefCell<Option<MenuItem>> = const { RefCell::new(None) };
}

static SERVICE_COUNT: AtomicUsize = AtomicUsize::new(0);

pub fn current_service_count() -> usize {
    SERVICE_COUNT.load(Ordering::SeqCst)
}

pub fn refresh_service_count(count: usize) {
    SERVICE_COUNT.store(count, Ordering::SeqCst);
}

fn publish_configured_service_count() {
    refresh_service_count(configured_services().len());
}

pub fn build_menu() -> Result<(Menu, MenuIds)> {
    publish_configured_service_count();
    let menu = Menu::new();
    let status_item = MenuItem::new(
        format!("Status: Idle ({} services)", current_service_count()),
        false,
        None,
    );
    menu.append(&status_item)?;
    STATUS_MENU_ITEM.with(|cell| *cell.borrow_mut() = Some(status_item));

    let show_dashboard = MenuItem::new("Show Mux Dashboard", true, None);
    let show_dashboard_id = show_dashboard.id().clone();
    menu.append(&show_dashboard)?;
    let open_logs = MenuItem::new("Open Mux Logs", true, None);
    let open_logs_id = open_logs.id().clone();
    menu.append(&open_logs)?;
    let service_count = MenuItem::new(
        format!("Services: {}", current_service_count()),
        false,
        None,
    );
    menu.append(&service_count)?;
    SERVICE_COUNT_MENU_ITEM.with(|cell| *cell.borrow_mut() = Some(service_count));
    menu.append(&PredefinedMenuItem::separator())?;

    let recent_runs_menu = Submenu::new("Recent runs (0)", true);
    let mut recent_runs = Vec::new();
    let mut recent_run_items = Vec::new();
    for index in 0..5 {
        let item = MenuItem::new(format!("Run {}", index + 1), false, None);
        recent_runs.push(item.id().clone());
        recent_runs_menu.append(&item)?;
        recent_run_items.push(item);
    }
    RECENT_RUNS_SUBMENU.with(|cell| *cell.borrow_mut() = Some(recent_runs_menu.clone()));
    RECENT_RUN_ITEMS.with(|cell| *cell.borrow_mut() = recent_run_items);
    menu.append(&recent_runs_menu)?;

    let mut restart_services = Vec::new();
    let restart_menu = Submenu::new("Restart Service", true);
    for name in configured_services() {
        let item = MenuItem::new(&name, true, None);
        restart_services.push((name, item.id().clone()));
        restart_menu.append(&item)?;
    }
    menu.append(&restart_menu)?;

    let mut verify_clients = Vec::new();
    let verify_menu = Submenu::new("Verify Clients", true);
    for kind in [ClientKind::Claude, ClientKind::Codex, ClientKind::Gemini] {
        let item = MenuItem::new(client_label(&kind), true, None);
        verify_clients.push((kind, item.id().clone()));
        verify_menu.append(&item)?;
    }
    menu.append(&verify_menu)?;

    let diagnostics = Submenu::new("Diagnostics", true);
    let copy_routing = MenuItem::new("Copy Routing Table", true, None);
    let copy_routing_id = copy_routing.id().clone();
    diagnostics.append(&copy_routing)?;
    let copy_diagnostics = MenuItem::new("Copy Diagnostics", true, None);
    let copy_diagnostics_id = copy_diagnostics.id().clone();
    diagnostics.append(&copy_diagnostics)?;
    menu.append(&diagnostics)?;
    menu.append(&PredefinedMenuItem::separator())?;

    let continue_onboarding = if should_show_onboarding() {
        let item = MenuItem::new("Continue Onboarding...", true, None);
        let id = item.id().clone();
        menu.append(&item)?;
        CONTINUE_ONBOARDING_MENU_ITEM.with(|cell| *cell.borrow_mut() = Some(item));
        Some(id)
    } else {
        None
    };
    let settings = MenuItem::new("Settings", true, None);
    let settings_id = settings.id().clone();
    menu.append(&settings)?;
    let help = MenuItem::new("Help", true, None);
    let help_id = help.id().clone();
    menu.append(&help)?;
    let about = MenuItem::new("About", true, None);
    let about_id = about.id().clone();
    menu.append(&about)?;
    menu.append(&PredefinedMenuItem::separator())?;
    let quit = MenuItem::new(
        "Quit Mux",
        true,
        Some(Accelerator::new(Some(Modifiers::SUPER), Code::KeyQ)),
    );
    let quit_id = quit.id().clone();
    menu.append(&quit)?;

    Ok((
        menu,
        MenuIds {
            show_dashboard: show_dashboard_id,
            open_logs: open_logs_id,
            copy_routing_table: copy_routing_id,
            copy_diagnostics: copy_diagnostics_id,
            continue_onboarding,
            open_settings: settings_id,
            help: help_id,
            about: about_id,
            quit: quit_id,
            restart_services,
            verify_clients,
            recent_runs,
        },
    ))
}

pub fn update_status_label(label: &str) {
    STATUS_MENU_ITEM.with(|cell| {
        if let Some(item) = cell.borrow().as_ref() {
            item.set_text(label);
        }
    });
}

pub fn update_service_count_label() {
    publish_configured_service_count();
    let label = format!("Services: {}", current_service_count());
    SERVICE_COUNT_MENU_ITEM.with(|cell| {
        if let Some(item) = cell.borrow().as_ref() {
            item.set_text(&label);
        }
    });
}

pub fn update_recent_runs_menu() {
    let recent = crate::state::recent_spawns();
    RECENT_RUNS_SUBMENU.with(|cell| {
        if let Some(menu) = cell.borrow().as_ref() {
            menu.set_text(format!("Recent runs ({})", recent.len()));
        }
    });
    RECENT_RUN_ITEMS.with(|cell| {
        for (index, item) in cell.borrow().iter().enumerate() {
            if let Some(entry) = recent.get(index) {
                item.set_text(entry.menu_label());
                item.set_enabled(true);
            } else {
                item.set_text("No recent run");
                item.set_enabled(false);
            }
        }
    });
}

pub fn update_onboarding_item() {
    CONTINUE_ONBOARDING_MENU_ITEM.with(|cell| {
        if let Some(item) = cell.borrow().as_ref() {
            item.set_enabled(should_show_onboarding());
        }
    });
}

fn configured_services() -> Vec<String> {
    env::var("VIBECRAFTED_MUX_SERVICES")
        .ok()
        .map(|raw| {
            raw.split(',')
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .map(ToOwned::to_owned)
                .collect::<Vec<_>>()
        })
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| vec!["mux-agent".to_string()])
}

fn should_show_onboarding() -> bool {
    let Some(home) = env::var_os("HOME") else {
        return true;
    };
    !std::path::PathBuf::from(home)
        .join(".vibecrafted/onboarding/wizard.completed")
        .exists()
}
