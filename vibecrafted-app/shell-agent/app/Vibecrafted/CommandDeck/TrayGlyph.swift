// Vibecrafted — Command Deck tray glyph
// Created by Vetcoders
//
// Sole monochrome menu-bar mark for the shell App. Never derived from the
// Dock/App icon image (identity roles stay separate).

import AppKit

/// Constructs the dedicated tray mark at menu-bar scale (16–18 pt).
///
/// The base glyph is a template image so Light/Dark menu bars tint it.
/// Status is a restrained corner badge only — the mark itself is never
/// recolored into a traffic light.
enum TrayGlyph {
  static let minimumPointSize: CGFloat = 16
  static let preferredPointSize: CGFloat = 18

  /// Clamp to the legible menu-bar band required by the identity contract.
  static func clampedPointSize(_ requested: CGFloat) -> CGFloat {
    min(max(requested, minimumPointSize), preferredPointSize)
  }

  /// Monochrome template glyph. Drawing is vector-local; no App icon reuse.
  static func templateImage(size requested: CGFloat = preferredPointSize) -> NSImage {
    let side = clampedPointSize(requested)
    let size = NSSize(width: side, height: side)
    let image = NSImage(size: size, flipped: false) { rect in
      drawMark(in: rect)
      return true
    }
    image.isTemplate = true
    image.accessibilityDescription = "Vibecrafted"
    return image
  }

  /// Template mark plus a small health badge. The composed bitmap is not a
  /// template because the badge carries semantic color; the mark layer itself
  /// remains monochrome ink.
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
    image.isTemplate = false
    image.accessibilityDescription = accessibilityLabel(for: health)
    return image
  }

  // MARK: - Drawing

  /// Compact instrument mark: rounded deck frame with a centered vertical
  /// fader — readable at 16–18 pt without borrowing Dock artwork.
  private static func drawMark(in rect: NSRect) {
    let inset = rect.insetBy(dx: rect.width * 0.12, dy: rect.height * 0.12)
    let radius = min(inset.width, inset.height) * 0.18

    let frame = NSBezierPath(
      roundedRect: inset,
      xRadius: radius,
      yRadius: radius
    )
    frame.lineWidth = max(1.1, rect.width * 0.08)
    NSColor.black.setStroke()
    frame.stroke()

    let railWidth = max(1.2, rect.width * 0.09)
    let railHeight = inset.height * 0.55
    let rail = NSRect(
      x: inset.midX - railWidth / 2,
      y: inset.midY - railHeight / 2,
      width: railWidth,
      height: railHeight
    )
    NSColor.black.setFill()
    NSBezierPath(roundedRect: rail, xRadius: railWidth / 2, yRadius: railWidth / 2).fill()

    let knobSide = max(2.4, rect.width * 0.22)
    let knob = NSRect(
      x: inset.midX - knobSide / 2,
      y: inset.midY - knobSide / 2 + railHeight * 0.12,
      width: knobSide,
      height: knobSide
    )
    NSBezierPath(ovalIn: knob).fill()
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
    badgeColor(for: health).setFill()
    NSBezierPath(ovalIn: badge).fill()
  }

  private static func badgeColor(for health: TrayServerHealth) -> NSColor {
    switch health {
    case .checking, .neutral:
      return .systemGray
    case .healthy:
      return .systemGreen
    case .transitioning:
      return .systemOrange
    case .failed:
      return .systemRed
    }
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
