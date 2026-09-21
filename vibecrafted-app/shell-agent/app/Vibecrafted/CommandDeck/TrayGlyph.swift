// Vibecrafted — Command Deck tray glyph
// Created by Vetcoders
//
// Canonical monochrome menu-bar mark for the shell App. It uses the dedicated
// three-marbles contour asset, separate from the vc-terminal Dock icon.

import AppKit

/// Constructs the dedicated tray mark at menu-bar scale (16–18 pt).
///
/// The black source is a native template mask, so AppKit supplies the correct
/// foreground color for the active menu-bar appearance. Status is a restrained corner badge only — the mark itself is
/// never recolored into a traffic light.
enum TrayGlyph {
  static let minimumPointSize: CGFloat = 16
  static let preferredPointSize: CGFloat = 18

  /// Clamp to the legible menu-bar band required by the identity contract.
  static func clampedPointSize(_ requested: CGFloat) -> CGFloat {
    min(max(requested, minimumPointSize), preferredPointSize)
  }

  /// Canonical monochrome tray glyph, distinct from both application Dock icons.
  static func templateImage(size requested: CGFloat = preferredPointSize) -> NSImage {
    let side = clampedPointSize(requested)
    let size = NSSize(width: side, height: side)
    let source = Bundle.main.image(forResource: "TrayIcon")
    let image = source?.copy() as? NSImage ?? fallbackImage(size: size)
    image.size = size
    image.isTemplate = true
    image.accessibilityDescription = "Vibecrafted"
    return image
  }

  /// The complete image stays a template in both menu-bar appearances.
  /// Health is also named in the accessibility label and full native tooltip.
  static func statusImage(
    health: TrayServerHealth,
    size requested: CGFloat = preferredPointSize
  ) -> NSImage {
    let side = clampedPointSize(requested)
    let size = NSSize(width: side, height: side)
    let base = templateImage(size: side)
    let image = NSImage(size: size, flipped: false) { rect in
      base.draw(
        in: rect,
        from: .zero,
        operation: .sourceOver,
        fraction: 1.0,
        respectFlipped: true,
        hints: [.interpolation: NSImageInterpolation.high]
      )
      drawBadge(health: health, in: rect)
      return true
    }
    image.isTemplate = true
    image.accessibilityDescription = accessibilityLabel(for: health)
    return image
  }

  // MARK: - Drawing

  private static func fallbackImage(size: NSSize) -> NSImage {
    let image = NSImage(
      systemSymbolName: "point.3.connected.trianglepath.dotted",
      accessibilityDescription: "Vibecrafted") ?? NSImage(size: size)
    image.size = size
    return image
  }

  private static func drawBadge(health: TrayServerHealth, in rect: NSRect) {
    let diameter = max(4.5, rect.width * 0.32)
    let badge = NSRect(
      x: rect.maxX - diameter - rect.width * 0.04,
      y: rect.minY + rect.height * 0.04,
      width: diameter,
      height: diameter
    )
    // Punch a thin halo so the badge stays legible on both appearances.
    NSColor.windowBackgroundColor.setFill()
    NSBezierPath(ovalIn: badge.insetBy(dx: -1.0, dy: -1.0)).fill()
    NSColor.black.setFill()
    NSBezierPath(ovalIn: badge).fill()
  }

  private static func accessibilityLabel(for health: TrayServerHealth) -> String {
    switch health {
    case .checking:
      return "Vibecrafted — checking"
    case .healthy:
      return "Vibecrafted — online"
    case .transitioning:
      return "Vibecrafted — transitioning"
    case .failed:
      return "Vibecrafted — failed"
    case .neutral:
      return "Vibecrafted"
    }
  }
}
