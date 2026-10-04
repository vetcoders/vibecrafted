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

@MainActor
enum TrayGlyph {
  static let minimumPointSize: CGFloat = 16
  static let preferredPointSize: CGFloat = 18
  private static let source = Bundle.main.image(forResource: "TrayIcon")
  private static let workingSource = source.map { fillingActivityCircle(in: $0) }

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
    let base: NSImage
    if let suppliedSource {
      base = activity.isRunning ? fillingActivityCircle(in: suppliedSource) : suppliedSource
    } else {
      base = (activity.isRunning ? workingSource : source) ?? fallbackImage(size: size)
    }
    let rotation = activity.isRunning ? (step % 6) * 60 : 0
    let image = NSImage(size: size, flipped: false) { rect in
      NSGraphicsContext.saveGraphicsState()
      defer { NSGraphicsContext.restoreGraphicsState() }
      let transform = AffineTransform(
        translationByX: rect.midX, byY: rect.midY)
      var rotationTransform = transform
      rotationTransform.rotate(byDegrees: CGFloat(rotation))
      rotationTransform.translate(x: -rect.midX, y: -rect.midY)
      (rotationTransform as NSAffineTransform).concat()
      base.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1,
        respectFlipped: true, hints: [.interpolation: NSImageInterpolation.high])
      return true
    }
    image.isTemplate = true
    image.accessibilityDescription = "\(accessibilityLabel(for: health)) — \(activity.description)"
    return image
  }

  /// Fill only the original lower-left enclosed eye. Flooding the transparent
  /// interior preserves the exact hand-drawn contour, with no guessed oval,
  /// corner badge, halo, replacement artwork or theme-specific paint.
  static func fillingActivityCircle(in source: NSImage) -> NSImage {
    guard let data = source.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data), bitmap.hasAlpha,
      bitmap.bitsPerSample == 8, !bitmap.isPlanar, bitmap.samplesPerPixel >= 2
    else { return source }
    let width = bitmap.pixelsWide
    let height = bitmap.pixelsHigh
    guard width > 0, height > 0 else { return source }
    let alphaIndex = bitmap.bitmapFormat.contains(.alphaFirst) ? 0 : bitmap.samplesPerPixel - 1
    var pixel = [Int](repeating: 0, count: bitmap.samplesPerPixel)
    let seed = (x: width * 25 / 88, y: height * 61 / 88)
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
