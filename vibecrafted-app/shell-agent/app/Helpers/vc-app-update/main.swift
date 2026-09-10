import Foundation

/// Compiled helper twin of `scripts/vc-app-update.sh`.
///
/// Release packaging installs the shell helper when this binary is not built
/// (COMPILE_EMBARGO). Both implementations replace the UI bundle only and never
/// stop Frame, PTYs, workers, or rewrite PATH / MCP.
@main
struct AppUpdateHelper {
  static func main() {
    var source: String?
    var destination: String?
    var receipt: String?
    var waitPID: Int32?
    var relaunch = false
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
      switch args[index] {
      case "--source":
        source = args[safe: index + 1]; index += 2
      case "--destination":
        destination = args[safe: index + 1]; index += 2
      case "--receipt":
        receipt = args[safe: index + 1]; index += 2
      case "--wait-pid":
        if let raw = args[safe: index + 1], let pid = Int32(raw) { waitPID = pid }
        index += 2
      case "--relaunch":
        relaunch = true; index += 1
      default:
        FileHandle.standardError.write(Data("usage: vc-app-update --source APP --destination APP --receipt FILE [--wait-pid PID] [--relaunch]\n".utf8))
        exit(2)
      }
    }
    guard let source, let destination, let receipt else {
      FileHandle.standardError.write(Data("usage: vc-app-update --source APP --destination APP --receipt FILE [--wait-pid PID] [--relaunch]\n".utf8))
      exit(2)
    }
    let request = ProductUpdateReplacementRequest(
      waitPID: waitPID,
      sourceApp: URL(fileURLWithPath: source),
      destinationApp: URL(fileURLWithPath: destination),
      relaunch: relaunch,
      receiptURL: URL(fileURLWithPath: receipt),
      helperURL: nil)
    switch replaceProductUpdateAppInProcess(request) {
    case .success:
      exit(0)
    case .failure(let error):
      FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
      exit(4)
    }
  }
}

private extension Array where Element == String {
  subscript(safe index: Int) -> String? {
    indices.contains(index) ? self[index] : nil
  }
}
