// Vibecrafted — Command Deck theme
// Created by Vetcoders

import SwiftUI

enum CommandDeckMetrics {
  static let chromePadding: CGFloat = 12
  static let chromeSpacing: CGFloat = 10
  static let overlayPadding: CGFloat = 28
  static let overlayMaxWidth: CGFloat = 420
  static let cornerRadius: CGFloat = 8
  static let strokeWidth: CGFloat = 1
  static let contrastStrokeWidth: CGFloat = 1.5
  static let motionDuration: Double = 0.22
}

struct CommandDeckPalette: Equatable {
  var surface: Color
  var surfaceRaised: Color
  var ink: Color
  var bone: Color
  var muted: Color
  var amber: Color
  var destructive: Color
  var stroke: Color

  static func resolved(
    scheme: ColorScheme,
    contrast: ColorSchemeContrast
  ) -> CommandDeckPalette {
    let increased = contrast == .increased
    switch scheme {
    case .light:
      return CommandDeckPalette(
        surface: Color(red: increased ? 1.00 : 0.96, green: increased ? 0.99 : 0.94, blue: increased ? 0.96 : 0.89),
        surfaceRaised: Color(red: 0.99, green: 0.98, blue: 0.95),
        ink: Color(red: increased ? 0.08 : 0.18, green: increased ? 0.07 : 0.17, blue: increased ? 0.06 : 0.15),
        bone: Color(red: 0.96, green: 0.94, blue: 0.89),
        muted: Color(red: increased ? 0.28 : 0.42, green: increased ? 0.26 : 0.40, blue: increased ? 0.24 : 0.37),
        amber: Color(red: increased ? 0.58 : 0.72, green: increased ? 0.34 : 0.45, blue: increased ? 0.02 : 0.08),
        destructive: Color(red: increased ? 0.62 : 0.72, green: increased ? 0.08 : 0.16, blue: increased ? 0.08 : 0.14),
        stroke: Color(red: increased ? 0.38 : 0.78, green: increased ? 0.35 : 0.74, blue: increased ? 0.30 : 0.68)
      )
    default:
      return CommandDeckPalette(
        surface: Color(red: increased ? 0.07 : 0.13, green: increased ? 0.07 : 0.13, blue: increased ? 0.06 : 0.12),
        surfaceRaised: Color(red: increased ? 0.12 : 0.18, green: increased ? 0.12 : 0.17, blue: increased ? 0.10 : 0.16),
        ink: Color(red: increased ? 0.98 : 0.93, green: increased ? 0.96 : 0.90, blue: increased ? 0.92 : 0.84),
        bone: Color(red: increased ? 0.98 : 0.93, green: increased ? 0.96 : 0.90, blue: increased ? 0.92 : 0.84),
        muted: Color(red: increased ? 0.82 : 0.70, green: increased ? 0.80 : 0.67, blue: increased ? 0.74 : 0.62),
        amber: Color(red: increased ? 1.00 : 0.89, green: increased ? 0.72 : 0.62, blue: increased ? 0.18 : 0.22),
        destructive: Color(red: increased ? 0.95 : 0.86, green: increased ? 0.36 : 0.32, blue: increased ? 0.32 : 0.28),
        stroke: Color(red: increased ? 0.62 : 0.32, green: increased ? 0.58 : 0.30, blue: increased ? 0.50 : 0.27)
      )
    }
  }
}

struct CommandDeckTheme: Equatable {
  var palette: CommandDeckPalette
  var strokeWidth: CGFloat

  static let standard = CommandDeckTheme(
    palette: .resolved(scheme: .dark, contrast: .standard),
    strokeWidth: CommandDeckMetrics.strokeWidth
  )

  static func resolved(
    scheme: ColorScheme,
    contrast: ColorSchemeContrast
  ) -> CommandDeckTheme {
    CommandDeckTheme(
      palette: .resolved(scheme: scheme, contrast: contrast),
      strokeWidth: contrast == .increased
        ? CommandDeckMetrics.contrastStrokeWidth
        : CommandDeckMetrics.strokeWidth
    )
  }

}

private struct CommandDeckThemeKey: EnvironmentKey {
  static let defaultValue = CommandDeckTheme.standard
}

extension EnvironmentValues {
  var commandDeckTheme: CommandDeckTheme {
    get { self[CommandDeckThemeKey.self] }
    set { self[CommandDeckThemeKey.self] = newValue }
  }
}

struct CommandDeckThemeResolver: ViewModifier {
  @Environment(\.colorScheme) private var colorScheme
  @Environment(\.colorSchemeContrast) private var colorSchemeContrast

  func body(content: Content) -> some View {
    content.environment(
      \.commandDeckTheme,
      .resolved(scheme: colorScheme, contrast: colorSchemeContrast)
    )
  }
}

extension View {
  func commandDeckThemed() -> some View {
    modifier(CommandDeckThemeResolver())
  }
}

struct CommandDeckAccentButtonStyle: ButtonStyle {
  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.isEnabled) private var isEnabled
  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.body)
      .bold()
      .foregroundStyle(theme.palette.surface)
      .padding(.horizontal, 14)
      .padding(.vertical, 7)
      .background(theme.palette.amber.opacity(isEnabled ? 1 : 0.45), in: RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius))
      .overlay {
        if differentiateWithoutColor {
          RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius)
            .strokeBorder(theme.palette.ink.opacity(0.35), lineWidth: theme.strokeWidth)
        }
      }
      .opacity(configuration.isPressed ? 0.82 : 1)
  }
}

struct CommandDeckQuietButtonStyle: ButtonStyle {
  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.isEnabled) private var isEnabled

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.body)
      .foregroundStyle(theme.palette.ink.opacity(isEnabled ? 1 : 0.45))
      .padding(.horizontal, 12)
      .padding(.vertical, 7)
      .background(theme.palette.surfaceRaised, in: RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius))
      .overlay {
        RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius)
          .strokeBorder(theme.palette.stroke, lineWidth: theme.strokeWidth)
      }
      .opacity(configuration.isPressed ? 0.82 : 1)
  }
}

struct CommandDeckDestructiveButtonStyle: ButtonStyle {
  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.body)
      .foregroundStyle(theme.palette.destructive)
      .padding(.horizontal, 12)
      .padding(.vertical, 7)
      .background(theme.palette.surfaceRaised, in: RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius))
      .overlay {
        RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius)
          .strokeBorder(
            differentiateWithoutColor ? theme.palette.destructive : theme.palette.stroke,
            lineWidth: theme.strokeWidth
          )
      }
      .opacity(configuration.isPressed ? 0.82 : 1)
  }
}

extension ButtonStyle where Self == CommandDeckAccentButtonStyle {
  static var commandDeckAccent: CommandDeckAccentButtonStyle { .init() }
}

extension ButtonStyle where Self == CommandDeckQuietButtonStyle {
  static var commandDeckQuiet: CommandDeckQuietButtonStyle { .init() }
}

extension ButtonStyle where Self == CommandDeckDestructiveButtonStyle {
  static var commandDeckDestructive: CommandDeckDestructiveButtonStyle { .init() }
}
