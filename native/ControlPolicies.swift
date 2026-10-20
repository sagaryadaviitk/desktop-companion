import Foundation

struct UnattendedSettings: Equatable {
    static let timeouts = [1, 2, 5, 10, 15, 30, 60]
    static let defaultBattery = 5
    static let defaultAdapter = 10
    let keepWorking: Bool
    let batteryMinutes: Int
    let adapterMinutes: Int

    var command: String? {
        guard Self.timeouts.contains(batteryMinutes), Self.timeouts.contains(adapterMinutes) else { return nil }
        // Only bounded integers and fixed executables enter the administrator script.
        return "/usr/bin/pmset -b displaysleep \(batteryMinutes) && /usr/bin/pmset -c displaysleep \(adapterMinutes) && /usr/bin/pmset -a disablesleep \(keepWorking ? 1 : 0)"
    }
    func matches(keepWorking: Bool?, battery: Int?, adapter: Int?) -> Bool {
        keepWorking == self.keepWorking && battery == batteryMinutes && adapter == adapterMinutes
    }
}

enum StatusFreshness {
    static func isFresh(checkedAt: Double?, failed: Bool, now: Double) -> Bool {
        guard !failed, let checkedAt = checkedAt else { return false }
        return checkedAt.isFinite && now - checkedAt >= -2 && now - checkedAt <= 20
    }
}
