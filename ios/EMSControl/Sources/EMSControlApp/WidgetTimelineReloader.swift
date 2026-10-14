import WidgetKit
import EMSControlCore

enum WidgetTimelineReloader {
    static func install() {
        NotificationCenter.default.addObserver(
            forName: .emsWidgetTimelinesShouldReload,
            object: nil,
            queue: .main
        ) { _ in
            WidgetCenter.shared.reloadAllTimelines()
        }
    }
}
