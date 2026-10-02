// Inspect only windows owned by our synthetic CI application. Window titles and
// screenshots are not needed, nor are Accessibility/Screen Recording grants.
import AppKit
import CoreGraphics
import Foundation

let app = URL(fileURLWithPath: CommandLine.arguments[1])
let configuration = NSWorkspace.OpenConfiguration()
configuration.createsNewApplicationInstance = true
NSWorkspace.shared.openApplication(at: app, configuration: configuration) { running, error in
    guard error == nil, let running = running else {
        fputs("Installed application could not launch\n", stderr)
        exit(1)
    }
    var failed = true
    let deadline = Date().addingTimeInterval(30)
    while Date() < deadline && !running.isTerminated {
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        if windows.contains(where: {
            ($0[kCGWindowOwnerPID as String] as? Int32) == running.processIdentifier &&
            ($0[kCGWindowLayer as String] as? Int) == 0
        }) {
            failed = false
            break
        }
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
    }
    if failed { fputs("Installed application did not open a window\n", stderr) }
    else { print("Installed macOS application opened a window.") }
    running.forceTerminate()
    exit(failed ? 1 : 0)
}
RunLoop.main.run()
