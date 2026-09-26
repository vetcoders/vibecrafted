// Vibecrafted — native source-list destinations
// Created by Vetcoders

import Foundation

/// Routes the web console already serves. The native sidebar selects these;
/// it does not invent destinations. Help and About are footer doors, not
/// section peers.
enum CommandDeckDestination: String, CaseIterable, Hashable, Identifiable {
  case overview
  case runs
  case projects
  case usage
  case skills
  case artifacts
  case structure
  case history
  case settings
  case diagnostics
  case help
  case about

  var id: String { rawValue }

  var title: String {
    switch self {
    case .overview: "Overview"
    case .runs: "Runs"
    case .projects: "Projects"
    case .usage: "Costs & usage"
    case .skills: "Skills"
    case .artifacts: "Artifacts"
    case .structure: "Code intelligence"
    case .history: "History & context"
    case .settings: "Settings & config"
    case .diagnostics: "Diagnostics"
    case .help: "Help & docs"
    case .about: "About"
    }
  }

  var symbol: String {
    switch self {
    case .overview: "square.grid.2x2"
    case .runs: "play.rectangle"
    case .projects: "folder"
    case .usage: "chart.bar"
    case .skills: "puzzlepiece"
    case .artifacts: "archivebox"
    case .structure: "point.3.connected.trianglepath.dotted"
    case .history: "clock.arrow.circlepath"
    case .settings: "gearshape"
    case .diagnostics: "stethoscope"
    case .help: "questionmark.circle"
    case .about: "info.circle"
    }
  }

  var path: String {
    switch self {
    case .overview: "/"
    case .runs: "/runs"
    case .projects: "/projects"
    case .usage: "/usage"
    case .skills: "/skills"
    case .artifacts: "/artifacts"
    case .structure: "/structure"
    case .history: "/history"
    case .settings: "/settings"
    case .diagnostics: "/diagnostics"
    case .help: "/help"
    case .about: "/about"
    }
  }

  /// The empty run document stays off pages that are not a run list.
  var showsRunDocument: Bool {
    self == .overview || self == .runs
  }

  /// Footer doors have no section. They are not peers of the shelf rows.
  var section: CommandDeckDestinationSection? {
    switch self {
    case .overview, .runs, .projects, .usage:
      .work
    case .skills, .artifacts, .structure, .history:
      .trace
    case .settings, .diagnostics:
      .machine
    case .help, .about:
      nil
    }
  }

  static func inSection(_ section: CommandDeckDestinationSection) -> [CommandDeckDestination] {
    allCases.filter { $0.section == section }
  }

  static var footerCases: [CommandDeckDestination] {
    allCases.filter { $0.section == nil }
  }
}

enum CommandDeckDestinationSection: String, CaseIterable, Identifiable {
  case work
  case trace
  case machine

  var id: String { rawValue }

  var title: String {
    switch self {
    case .work: "Work"
    case .trace: "Trace"
    case .machine: "Machine"
    }
  }
}
