//! Shared voc geometry. Draw and mouse hit-testing must use the same splits
//! so a click or wheel lands in one pane, never both.

use crate::app::AppTab;
use crate::observe::ConsoleView;
use ratatui::layout::{Constraint, Direction, Layout, Rect};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PaneId {
    DispatchDeck,
    DispatchPlaybook,
    DispatchTrail,
    MonitorList,
    MonitorDossier,
    MonitorTimeline,
    ObserveList,
    ObserveTranscript,
    ControlsActions,
    ControlsArtifacts,
    ControlsTimeline,
    Mission(u8),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HitTarget {
    Tab(usize),
    DispatchStat(usize),
    DispatchDeck { inner_row: u16 },
    DispatchPlaybook,
    DispatchTrail,
    MonitorStat(usize),
    MonitorList { inner_row: u16 },
    MonitorDossier,
    MonitorTimeline,
    ObserveList { inner_row: u16 },
    ObserveTranscript,
    ControlsStat(usize),
    ControlsActions { inner_row: u16 },
    ControlsArtifacts,
    ControlsTimeline,
    MissionPanel(u8),
}

#[derive(Debug, Clone, Copy)]
pub struct RootLayout {
    pub header: Rect,
    pub tabs: Rect,
    pub body: Rect,
    pub footer: Rect,
}

#[derive(Debug, Clone, Copy)]
pub struct DispatchLayout {
    pub stats: [Rect; 3],
    pub deck: Rect,
    pub playbook: Rect,
    pub trail: Rect,
}

#[derive(Debug, Clone, Copy)]
pub struct MonitorLayout {
    pub stats: [Rect; 3],
    pub mux: Option<Rect>,
    pub polarize: Option<Rect>,
    pub list: Rect,
    pub dossier: Rect,
    pub timeline: Rect,
}

#[derive(Debug, Clone, Copy)]
pub struct ObserveLayout {
    pub list: Rect,
    pub transcript: Rect,
}

#[derive(Debug, Clone, Copy)]
pub struct ControlsLayout {
    pub stats: [Rect; 3],
    pub actions: Rect,
    pub artifacts: Rect,
    pub timeline: Rect,
}

#[derive(Debug, Clone, Copy)]
pub struct MissionLayout {
    pub stats: [Rect; 3],
    pub panels: [Rect; 7],
    pub footer: Rect,
}

pub fn root_layout(area: Rect) -> RootLayout {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .split(area);
    RootLayout {
        header: rows[0],
        tabs: rows[1],
        body: rows[2],
        footer: rows[3],
    }
}

pub fn stat_strip(area: Rect) -> [Rect; 3] {
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Ratio(1, 3); 3])
        .split(area);
    [columns[0], columns[1], columns[2]]
}

pub fn dispatch_layout(body: Rect) -> DispatchLayout {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(5), Constraint::Min(12)])
        .split(body);
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(rows[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(44), Constraint::Percentage(56)])
        .split(columns[1]);
    DispatchLayout {
        stats: stat_strip(rows[0]),
        deck: columns[0],
        playbook: right[0],
        trail: right[1],
    }
}

pub fn mux_panel_height(line_count: usize) -> u16 {
    if line_count == 0 {
        0
    } else {
        (line_count as u16 + 2).clamp(3, 10)
    }
}

pub fn polarize_panel_height(line_count: usize) -> u16 {
    if line_count == 0 {
        0
    } else {
        (line_count as u16 + 2).clamp(3, 9)
    }
}

pub fn monitor_layout(body: Rect, mux_height: u16, polarize_height: u16) -> MonitorLayout {
    let mut constraints = vec![Constraint::Length(5)];
    if mux_height > 0 {
        constraints.push(Constraint::Length(mux_height));
    }
    if polarize_height > 0 {
        constraints.push(Constraint::Length(polarize_height));
    }
    constraints.push(Constraint::Min(8));
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(body);
    let mut idx = 1;
    let mux = if mux_height > 0 {
        let rect = rows[idx];
        idx += 1;
        Some(rect)
    } else {
        None
    };
    let polarize = if polarize_height > 0 {
        let rect = rows[idx];
        idx += 1;
        Some(rect)
    } else {
        None
    };
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(36), Constraint::Percentage(64)])
        .split(rows[idx]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(62), Constraint::Percentage(38)])
        .split(columns[1]);
    MonitorLayout {
        stats: stat_strip(rows[0]),
        mux,
        polarize,
        list: columns[0],
        dossier: right[0],
        timeline: right[1],
    }
}

pub fn observe_layout(body: Rect) -> ObserveLayout {
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(body);
    ObserveLayout {
        list: columns[0],
        transcript: columns[1],
    }
}

pub fn controls_layout(body: Rect) -> ControlsLayout {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(5), Constraint::Min(12)])
        .split(body);
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(46), Constraint::Percentage(54)])
        .split(rows[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(columns[1]);
    ControlsLayout {
        stats: stat_strip(rows[0]),
        actions: columns[0],
        artifacts: right[0],
        timeline: right[1],
    }
}

pub fn mission_layout(body: Rect) -> MissionLayout {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Min(8),
            Constraint::Length(6),
        ])
        .split(body);
    let grid = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Ratio(1, 3),
            Constraint::Ratio(1, 3),
            Constraint::Ratio(1, 3),
        ])
        .split(rows[1]);
    let top = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(grid[0]);
    let middle = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
        .split(grid[1]);
    let bottom = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(34),
            Constraint::Percentage(33),
            Constraint::Percentage(33),
        ])
        .split(grid[2]);
    MissionLayout {
        stats: stat_strip(rows[0]),
        panels: [
            top[0], top[1], middle[0], middle[1], bottom[0], bottom[1], bottom[2],
        ],
        footer: rows[2],
    }
}

pub fn contains(area: Rect, column: u16, row: u16) -> bool {
    column >= area.x
        && column < area.x.saturating_add(area.width)
        && row >= area.y
        && row < area.y.saturating_add(area.height)
}

pub fn inner_row(area: Rect, row: u16) -> u16 {
    row.saturating_sub(area.y.saturating_add(1))
}

pub fn inner_height(area: Rect) -> u16 {
    area.height.saturating_sub(2)
}

pub fn tab_index_at(tabs: Rect, column: u16) -> Option<usize> {
    if !contains(tabs, column, tabs.y) || tabs.width == 0 {
        return None;
    }
    let inner = tabs.width.saturating_sub(2).max(1);
    let x = column.saturating_sub(tabs.x.saturating_add(1));
    Some(((x as usize) * AppTab::TITLES.len() / inner as usize).min(AppTab::TITLES.len() - 1))
}

fn first_stat(stats: &[Rect; 3], column: u16, row: u16) -> Option<usize> {
    stats.iter().position(|rect| contains(*rect, column, row))
}

pub fn hit_test(
    area: Rect,
    tab: AppTab,
    view: ConsoleView,
    mux_height: u16,
    polarize_height: u16,
    column: u16,
    row: u16,
) -> Option<HitTarget> {
    let root = root_layout(area);
    if contains(root.tabs, column, row) {
        return tab_index_at(root.tabs, column).map(HitTarget::Tab);
    }
    if !contains(root.body, column, row) {
        return None;
    }
    match tab {
        AppTab::Monitor if view == ConsoleView::Observe => {
            let layout = observe_layout(root.body);
            if contains(layout.list, column, row) {
                Some(HitTarget::ObserveList {
                    inner_row: inner_row(layout.list, row),
                })
            } else if contains(layout.transcript, column, row) {
                Some(HitTarget::ObserveTranscript)
            } else {
                None
            }
        }
        AppTab::Monitor => {
            let layout = monitor_layout(root.body, mux_height, polarize_height);
            if let Some(index) = first_stat(&layout.stats, column, row) {
                Some(HitTarget::MonitorStat(index))
            } else if contains(layout.list, column, row) {
                Some(HitTarget::MonitorList {
                    inner_row: inner_row(layout.list, row),
                })
            } else if contains(layout.dossier, column, row) {
                Some(HitTarget::MonitorDossier)
            } else if contains(layout.timeline, column, row) {
                Some(HitTarget::MonitorTimeline)
            } else {
                None
            }
        }
        AppTab::Dispatch => {
            let layout = dispatch_layout(root.body);
            if let Some(index) = first_stat(&layout.stats, column, row) {
                Some(HitTarget::DispatchStat(index))
            } else if contains(layout.deck, column, row) {
                Some(HitTarget::DispatchDeck {
                    inner_row: inner_row(layout.deck, row),
                })
            } else if contains(layout.playbook, column, row) {
                Some(HitTarget::DispatchPlaybook)
            } else if contains(layout.trail, column, row) {
                Some(HitTarget::DispatchTrail)
            } else {
                None
            }
        }
        AppTab::Controls => {
            let layout = controls_layout(root.body);
            if let Some(index) = first_stat(&layout.stats, column, row) {
                Some(HitTarget::ControlsStat(index))
            } else if contains(layout.actions, column, row) {
                Some(HitTarget::ControlsActions {
                    inner_row: inner_row(layout.actions, row),
                })
            } else if contains(layout.artifacts, column, row) {
                Some(HitTarget::ControlsArtifacts)
            } else if contains(layout.timeline, column, row) {
                Some(HitTarget::ControlsTimeline)
            } else {
                None
            }
        }
        AppTab::MissionControl => {
            let layout = mission_layout(root.body);
            layout.panels.iter().enumerate().find_map(|(index, rect)| {
                contains(*rect, column, row).then_some(HitTarget::MissionPanel(index as u8))
            })
        }
    }
}

pub fn pane_for_hit(hit: HitTarget) -> Option<PaneId> {
    match hit {
        HitTarget::DispatchDeck { .. } => Some(PaneId::DispatchDeck),
        HitTarget::DispatchPlaybook => Some(PaneId::DispatchPlaybook),
        HitTarget::DispatchTrail => Some(PaneId::DispatchTrail),
        HitTarget::MonitorList { .. } => Some(PaneId::MonitorList),
        HitTarget::MonitorDossier => Some(PaneId::MonitorDossier),
        HitTarget::MonitorTimeline => Some(PaneId::MonitorTimeline),
        HitTarget::ObserveList { .. } => Some(PaneId::ObserveList),
        HitTarget::ObserveTranscript => Some(PaneId::ObserveTranscript),
        HitTarget::ControlsActions { .. } => Some(PaneId::ControlsActions),
        HitTarget::ControlsArtifacts => Some(PaneId::ControlsArtifacts),
        HitTarget::ControlsTimeline => Some(PaneId::ControlsTimeline),
        HitTarget::MissionPanel(index) => Some(PaneId::Mission(index)),
        HitTarget::Tab(_)
        | HitTarget::DispatchStat(_)
        | HitTarget::MonitorStat(_)
        | HitTarget::ControlsStat(_) => None,
    }
}

pub fn clamp_scroll(offset: u16, content_len: usize, view_height: u16) -> u16 {
    let max = content_len.saturating_sub(view_height.max(1) as usize);
    offset.min(max as u16)
}

pub fn step_scroll(offset: u16, delta: i16, content_len: usize, view_height: u16) -> u16 {
    let next = i32::from(offset) + i32::from(delta);
    let next = if next < 0 { 0 } else { next as u16 };
    clamp_scroll(next, content_len, view_height)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn area() -> Rect {
        Rect::new(0, 0, 120, 40)
    }

    #[test]
    fn dispatch_deck_and_trail_do_not_overlap() {
        let layout = dispatch_layout(root_layout(area()).body);
        assert!(layout.deck.width > 0);
        assert!(layout.trail.width > 0);
        assert!(
            layout.deck.x + layout.deck.width <= layout.trail.x
                || layout.deck.y + layout.deck.height <= layout.trail.y
        );
        let deck_point = (layout.deck.x + 2, layout.deck.y + 2);
        let trail_point = (layout.trail.x + 2, layout.trail.y + 2);
        let deck_hit = hit_test(
            area(),
            AppTab::Dispatch,
            ConsoleView::Full,
            0,
            0,
            deck_point.0,
            deck_point.1,
        );
        let trail_hit = hit_test(
            area(),
            AppTab::Dispatch,
            ConsoleView::Full,
            0,
            0,
            trail_point.0,
            trail_point.1,
        );
        assert!(matches!(deck_hit, Some(HitTarget::DispatchDeck { .. })));
        assert_eq!(trail_hit, Some(HitTarget::DispatchTrail));
        assert_ne!(deck_hit, trail_hit);
    }

    #[test]
    fn dispatch_stat_cells_are_distinct() {
        let layout = dispatch_layout(root_layout(area()).body);
        for (index, rect) in layout.stats.iter().enumerate() {
            let hit = hit_test(
                area(),
                AppTab::Dispatch,
                ConsoleView::Full,
                0,
                0,
                rect.x + 2,
                rect.y + 1,
            );
            assert_eq!(hit, Some(HitTarget::DispatchStat(index)));
        }
    }

    #[test]
    fn wheel_step_stays_on_one_offset() {
        let deck = step_scroll(0, 3, 40, 10);
        let trail = step_scroll(0, 0, 40, 10);
        assert_eq!(deck, 3);
        assert_eq!(trail, 0);
        assert_eq!(step_scroll(3, -1, 40, 10), 2);
        assert_eq!(step_scroll(0, -4, 40, 10), 0);
        assert_eq!(step_scroll(100, 1, 12, 10), 2);
    }
}
