// Vibecrafted — native source-list destinations
// Created by Vetcoders

import Foundation

/// Routes the web console already serves. The native sidebar selects these;
/// it does not invent destinations.
enum CommandDeckDestination: String, CaseIterable, Hashable, Identifiable {
  case overview
  case transcripts
  case usage
  case structure
  case plans
  case frame
  case active
  case failures
  case health
  case workspaces
  case sessions
  case agents
  case live
  case lifecycle
  case activity
  case guide

  var id: String { rawValue }

  var title: String {
    switch self {
    case .overview: "Overview"
    case .transcripts: "Transcripts"
    case .usage: "Usage"
    case .structure: "Structure"
    case .plans: "Plans"
    case .frame: "Frame"
    case .active: "Active"
    case .failures: "Failures"
    case .health: "Health"
    case .workspaces: "Workspaces"
    case .sessions: "Sessions"
    case .agents: "Agents"
    case .live: "Live"
    case .lifecycle: "Control"
    case .activity: "Activity"
    case .guide: "Guide"
    }
  }

  var symbol: String {
    switch self {
    case .overview: "square.grid.2x2"
    case .transcripts: "text.alignleft"
    case .usage: "chart.bar"
    case .structure: "point.3.connected.trianglepath.dotted"
    case .plans: "list.bullet.rectangle"
    case .frame: "rectangle.split.3x1"
    case .active: "circle.fill"
    case .failures: "exclamationmark.circle"
    case .health: "heart"
    case .workspaces: "folder"
    case .sessions: "person.2"
    case .agents: "cpu"
    case .live: "dot.radiowaves.left.and.right"
    case .lifecycle: "slider.horizontal.3"
    case .activity: "waveform"
    case .guide: "book"
    }
  }

  var path: String {
    switch self {
    case .overview: "/"
    case .transcripts: "/transcripts"
    case .usage: "/usage"
    case .structure: "/structure"
    case .plans: "/scaffold"
    case .frame: "/frame"
    case .active: "/?rail=active"
    case .failures: "/?rail=failures"
    case .health: "/?rail=health"
    case .workspaces: "/workspaces"
    case .sessions: "/sessions"
    case .agents: "/agents"
    case .live: "/runs"
    case .lifecycle: "/lifecycle"
    case .activity: "/activity"
    case .guide: "/guide"
    }
  }

  var section: CommandDeckDestinationSection {
    switch self {
    case .overview, .transcripts, .usage, .structure, .plans, .frame:
      .work
    case .active, .failures, .health, .workspaces, .sessions, .agents, .live:
      .observe
    case .lifecycle, .activity, .guide:
      .control
    }
  }

  static func inSection(_ section: CommandDeckDestinationSection) -> [CommandDeckDestination] {
    allCases.filter { $0.section == section }
  }
}

enum CommandDeckDestinationSection: String, CaseIterable, Identifiable {
  case work
  case observe
  case control

  var id: String { rawValue }

  var title: String {
    switch self {
    case .work: "Work"
    case .observe: "Observe"
    case .control: "Control"
    }
  }
}
