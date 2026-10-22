// Vibecrafted — canonical system-appearance menu-bar mark.
import AppKit

/// A consumer of the control-plane work count; service health is independent.
enum TrayWorkActivity: Equatable, Sendable {
  case unavailable
  case idle
  case running(Int)

  var isRunning: Bool {
    if case .running = self { return true }
    return false
  }

  var description: String {
    switch self {
    case .unavailable: "work activity unavailable"
    case .idle: "no executing dispatched runs"
    case .running(let count): "\(count) executing dispatched run\(count == 1 ? "" : "s")"
    }
  }
}

struct TrayWorkObservation: Equatable, Sendable {
  let activity: TrayWorkActivity
  let observedAt: TimeInterval

  func current(at now: TimeInterval) -> TrayWorkActivity {
    guard now >= observedAt, now - observedAt < 10 else { return .unavailable }
    return activity
  }
}

func decodeTrayWorkActivity(data: Data, terminationStatus: Int32) -> TrayWorkActivity {
  struct Snapshot: Decodable {
    struct Summary: Decodable { let lanes: Int; let running: Int }
    let schema_version: String
    let summary: Summary
  }
  guard terminationStatus == 0,
    let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data),
    snapshot.schema_version == "vibecrafted.lifecycle-activity.v1",
    snapshot.summary.running >= 0,
    snapshot.summary.running <= snapshot.summary.lanes
  else { return .unavailable }
  return snapshot.summary.running == 0 ? .idle : .running(snapshot.summary.running)
}

/// One discrete 60-degree step; nothing changes the Dock icon.
struct TrayActivityFrame {
  private(set) var step = 0

  mutating func advance(activity: TrayWorkActivity, reduceMotion: Bool) {
    step = activity.isRunning && !reduceMotion ? (step + 1) % 6 : 0
  }

  mutating func reset() { step = 0 }
}

/// The white activity circle moves position instead of the whole mark
/// rotating. Founder direction 2026-10-22: the three-circle shape is the
/// static mark; the filled circle is the state indicator. Working advances
/// the filled circle one slot clockwise per tick; idle, unavailable and
/// reduce-motion keep the reference slot. `step` is a 60-degree slot index
/// (three circles sit on a triangle ring), but only the circle is redrawn.
struct TrayActivitySlots {
  /// The reference slot: the lower-center circle of the mark (as in TrayIcon).
  static let referenceSlot = 0

  /// The three source-mark circle centers on the 88px TrayIcon mask,
  /// measured by flood-fill of the enclosed interiors. In mask coordinates
  /// (y grows downward) they form a triangle pointing up; with the menu-bar
  /// render's `respectFlipped: true` the visual result is two upper circles
  /// and one lower-center — the reference. Advance order is clockwise from
  /// the reference on screen: lower → upper right → upper left.
  static let circleCenters: [(x: Int, y: Int)] = [
    (43, 28),  // slot 0: lower-center on screen (reference)
    (61, 60),  // slot 1: upper right on screen
    (25, 60),  // slot 2: upper left on screen
  ]

  static func slot(for step: Int, activity: TrayWorkActivity) -> Int {
    guard activity.isRunning else { return referenceSlot }
    let normalized = ((step % 3) + 3) % 3
    return normalized
  }
}



@MainActor
enum TrayGlyph {
  static let minimumPointSize: CGFloat = 16
  static let preferredPointSize: CGFloat = 18
  private static let source = Bundle.main.image(forResource: "TrayIcon")

  static func clampedPointSize(_ requested: CGFloat) -> CGFloat {
    min(max(requested, minimumPointSize), preferredPointSize)
  }

  static func templateImage(size requested: CGFloat = preferredPointSize) -> NSImage {
    statusImage(health: .neutral, size: requested)
  }

  /// The source contours stay unchanged. AppKit colors this alpha mask for
  /// the actual menu bar, including light and dark system appearances.
  static func statusImage(
    health: TrayServerHealth,
    size requested: CGFloat = preferredPointSize,
    activity: TrayWorkActivity = .unavailable,
    step: Int = 0,
    source suppliedSource: NSImage? = nil
  ) -> NSImage {
    let side = clampedPointSize(requested)
    let size = NSSize(width: side, height: side)
    // Founder direction 2026-10-22: the three-circle mark is static; the
    // white activity circle moves to the active slot instead of the whole
    // glyph rotating. `step` is a 60-degree slot index (three circles sit on
    // a hexagon ring), advancing one slot per tick while work is running.
    let slot = TrayActivitySlots.slot(for: step, activity: activity)
    let base: NSImage
    if let suppliedSource {
      base = activity.isRunning ? fillingActivityCircle(at: slot, in: suppliedSource) : suppliedSource
    } else {
      base = (activity.isRunning ? workingSource(at: slot) : source) ?? fallbackImage(size: size)
    }
    let image = renderMask(source: base, side: side, rotation: 0)
    image.isTemplate = true
    image.accessibilityDescription = "\(accessibilityLabel(for: health)) — \(activity.description)"
    return image
  }

  /// All states share one centered viewport. The original 88px mask has
  /// contour pixels 45.1px from its canvas center; a three-source-pixel inset
  /// retains the complete mark through every 60-degree orientation.
  static func renderMask(source: NSImage, side: CGFloat, rotation: Int,
    canvasPadding: CGFloat = 0) -> NSImage {
    let canvas = side + canvasPadding * 2
    let image = NSImage(size: NSSize(width: canvas, height: canvas), flipped: false) { rect in
      NSGraphicsContext.saveGraphicsState()
      defer { NSGraphicsContext.restoreGraphicsState() }
      let transform = NSAffineTransform()
      transform.translateX(by: rect.midX, yBy: rect.midY)
      transform.rotate(byDegrees: CGFloat(rotation))
      transform.translateX(by: -rect.midX, yBy: -rect.midY)
      transform.concat()
      let inset = side * 3 / 88
      let viewport = NSRect(x: canvasPadding + inset, y: canvasPadding + inset,
        width: side - inset * 2, height: side - inset * 2)
      source.draw(in: viewport, from: .zero, operation: .sourceOver, fraction: 1,
        respectFlipped: true, hints: [.interpolation: NSImageInterpolation.high])
      return true
    }
    image.isTemplate = true
    return image
  }

  /// Fill only the original lower-left enclosed eye. Flooding the transparent
  /// interior preserves the exact hand-drawn contour, with no guessed oval,
  /// corner badge, halo, replacement artwork or theme-specific paint.
  static func fillingActivityCircle(in source: NSImage) -> NSImage {
    fillingActivityCircle(at: TrayActivitySlots.referenceSlot, in: source)
  }

  static func workingSource(at slot: Int) -> NSImage? {
    guard let base = source else { return nil }
    return fillingActivityCircle(at: slot, in: base)
  }

  /// Fill the enclosed eye of the circle at `slot` (0…5 on the 6-slot ring;
  /// the three source circles sit at slots 1, 3, 5). Slotting the seed into
  /// the chosen circle's interior keeps the fill inside exactly one circle
  /// while the other two contours stay unchanged. A non-enclosed or foreign
  /// seed never fills the background.
  static func fillingActivityCircle(at slot: Int, in source: NSImage) -> NSImage {
    guard let data = source.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data), bitmap.hasAlpha,
      bitmap.bitsPerSample == 8, !bitmap.isPlanar, bitmap.samplesPerPixel >= 2
    else { return source }
    let width = bitmap.pixelsWide
    let height = bitmap.pixelsHigh
    guard width > 0, height > 0 else { return source }
    let alphaIndex = bitmap.bitmapFormat.contains(.alphaFirst) ? 0 : bitmap.samplesPerPixel - 1
    var pixel = [Int](repeating: 0, count: bitmap.samplesPerPixel)
    // Slot centers on the 88px source mask, measured by flood-fill of the
    // three enclosed interiors (pinned by the CommandDeck integration test).
    // In mask coordinates y grows downward; the menu-bar render flips
    // vertically (`respectFlipped: true`), so mask (43, 28) is the
    // lower-center circle on screen — the reference slot from TrayIcon.
    let clampedSlot = min(max(slot, 0), TrayActivitySlots.circleCenters.count - 1)
    let center = TrayActivitySlots.circleCenters[clampedSlot]
    let seed = (x: center.x * width / 88, y: center.y * height / 88)
    var visited = Set<Int>()
    var pending = [seed]
    while let point = pending.popLast() {
      guard point.x >= 0, point.x < width, point.y >= 0, point.y < height else {
        // A non-enclosed region must never fill the background.
        return source
      }
      let index = point.y * width + point.x
      guard !visited.contains(index) else { continue }
      bitmap.getPixel(&pixel, atX: point.x, y: point.y)
      guard pixel[alphaIndex] == 0 else { continue }
      visited.insert(index)
      pending += [(point.x - 1, point.y), (point.x + 1, point.y),
        (point.x, point.y - 1), (point.x, point.y + 1)]
    }
    for index in visited {
      pixel = [Int](repeating: 0, count: bitmap.samplesPerPixel)
      pixel[alphaIndex] = 255
      bitmap.setPixel(&pixel, atX: index % width, y: index / width)
    }
    let image = NSImage(size: source.size)
    image.addRepresentation(bitmap)
    image.isTemplate = true
    return image
  }

  private static func fallbackImage(size: NSSize) -> NSImage {
    let image = NSImage(systemSymbolName: "point.3.connected.trianglepath.dotted",
      accessibilityDescription: "Vibecrafted") ?? NSImage(size: size)
    image.size = size
    return image
  }

  private static func accessibilityLabel(for health: TrayServerHealth) -> String {
    switch health {
    case .checking: "Vibecrafted — checking"
    case .healthy: "Vibecrafted — online"
    case .transitioning: "Vibecrafted — transitioning"
    case .failed: "Vibecrafted — failed"
    case .neutral: "Vibecrafted"
    }
  }
}
