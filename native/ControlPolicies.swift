import Foundation

struct BrowserAccessPolicy: Decodable, Equatable {
    let default_days, max_days: Int

    func duration(days: String, never: Bool) -> Int? {
        if never { return 0 }
        guard max_days >= 1, let value = Int(days), (1...max_days).contains(value) else { return nil }
        return value
    }
}

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

enum StatusRetry {
    // Equal jitter avoids synchronized retries without a near-zero busy loop.
    static func delay(failures: Int, random: Double) -> Double {
        let cap = min(30.0, pow(2.0, Double(max(0, min(failures - 1, 10)))))
        return cap * (0.5 + max(0, min(1, random)) * 0.5)
    }
}

enum CompanionWebNavigation {
    static let conversations = URL(string: "http://127.0.0.1:8765/?embedded=1")!
    static let localChats = URL(string: "http://127.0.0.1:8765/local?embedded=1")!

    static func isTrusted(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == 8765 &&
        url.user == nil && url.password == nil
    }

    static func isLocalChat(_ url: URL) -> Bool {
        isTrusted(url) && (url.path == "/local" || url.path.hasPrefix("/local/"))
    }

    static func canOpenInBrowser(_ url: URL) -> Bool {
        ["http", "https"].contains(url.scheme ?? "") && url.host != nil &&
        url.user == nil && url.password == nil
    }

    static func embedded(_ url: URL) -> URL? {
        guard isTrusted(url), var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        parts.queryItems = (parts.queryItems ?? []).filter { $0.name != "embedded" } + [URLQueryItem(name: "embedded", value: "1")]
        return parts.url
    }

    static func browserURL(_ url: URL) -> URL? {
        guard isTrusted(url), var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        let items = (parts.queryItems ?? []).filter { $0.name != "embedded" }
        parts.queryItems = items.isEmpty ? nil : items
        return parts.url
    }
}
