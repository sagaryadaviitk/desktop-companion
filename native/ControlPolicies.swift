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
        return PowerGuard.installCommand(enabled: keepWorking, battery: batteryMinutes, adapter: adapterMinutes)
    }
    func matches(keepWorking: Bool?, battery: Int?, adapter: Int?, cutoff: Int? = nil, source: String? = nil, percent: Int? = nil) -> Bool {
        let awake = source == "adapter" || (source == "battery" && (percent.map { (20...100).contains($0) } ?? false))
        return keepWorking == (self.keepWorking && awake) &&
            (self.keepWorking ? cutoff == 20 : cutoff == nil) &&
            battery == batteryMinutes && adapter == adapterMinutes
    }
}

enum PowerGuard {
    static let label = "me.sagaryadav.desktop-companion.power"
    static let helper = "/Library/PrivilegedHelperTools/" + label
    static let plist = "/Library/LaunchDaemons/" + label + ".plist"
    // launchd executes a root-owned copy, never code in a writable checkout.
    static let script = #"""
#!/bin/sh
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
LC_ALL=C
export LC_ALL
desired() {
    case "$1" in
        adapter) echo 1 ;;
        battery)
            case "$2" in ''|*[!0-9]*|????*) echo 0; return ;; esac
            if [ "$2" -ge 20 ] && [ "$2" -le 100 ]; then echo 1; else echo 0; fi ;;
        *) echo 0 ;;
    esac
}
if [ "${1-}" = --decide ]; then desired "${2-}" "${3-}"; exit; fi
status=/var/run/me.sagaryadav.desktop-companion.power.status
cleanup() {
    /usr/bin/pmset -a disablesleep 0 || :
    /bin/rm -f "$status"
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM
while :; do
    snapshot=$(/usr/bin/pmset -g batt) || snapshot=''
    source=unknown
    case "$snapshot" in
        "Now drawing from 'AC Power'"*) source=adapter ;;
        "Now drawing from 'Battery Power'"*) source=battery ;;
    esac
    percent=$(printf '%s\n' "$snapshot" | /usr/bin/sed -nE 's/.*[[:space:]]([0-9]{1,3})%;.*/\1/p' | /usr/bin/head -1)
    target=$(desired "$source" "$percent")
    current=$(/usr/bin/pmset -g | /usr/bin/awk '$1 == "SleepDisabled" {print $2; exit}')
    if [ "$current" != "$target" ]; then /usr/bin/pmset -a disablesleep "$target"; fi
    temporary=$(/usr/bin/mktemp "$status.XXXXXX")
    printf '%s %s\n' "$(/bin/date +%s)" "$target" > "$temporary"
    /bin/chmod 644 "$temporary"
    /bin/mv -f "$temporary" "$status"
    /bin/sleep 15
done
"""#

    static func installCommand(enabled: Bool, battery: Int, adapter: Int) -> String {
        let definition: [String: Any] = ["Label": label, "ProgramArguments": [helper], "RunAtLoad": true,
                                       "KeepAlive": true, "ThrottleInterval": 10, "ExitTimeOut": 20]
        let plistData = try! PropertyListSerialization.data(fromPropertyList: definition, format: .xml, options: 0)
        let enable = """
/bin/mkdir -p /Library/PrivilegedHelperTools
test ! -L /Library/PrivilegedHelperTools
test ! -L \(helper)
test ! -L \(plist)
printf '%s' '\(Data(script.utf8).base64EncodedString())' | /usr/bin/base64 -D > \(helper)
/usr/sbin/chown root:wheel \(helper)
/bin/chmod 755 \(helper)
printf '%s' '\(plistData.base64EncodedString())' | /usr/bin/base64 -D > \(plist)
/usr/sbin/chown root:wheel \(plist)
/bin/chmod 644 \(plist)
/bin/launchctl enable system/\(label)
/bin/launchctl bootstrap system \(plist)
for attempt in 1 2 3 4 5; do
    test -f /var/run/\(label).status && break
    /bin/sleep 1
done
test -f /var/run/\(label).status
"""
        let disable = "/bin/rm -f \(helper) \(plist)"
        let install = """
set -eu
# Replace the legacy permanent override with the managed policy.
if /bin/launchctl print system/\(label) >/dev/null 2>&1; then
    /bin/launchctl bootout system/\(label)
fi
/usr/bin/pmset -a disablesleep 0
/bin/rm -f /var/run/\(label).status
\(enabled ? enable : disable)
/usr/bin/pmset -b displaysleep \(battery)
/usr/bin/pmset -c displaysleep \(adapter)
"""
        // No dynamic shell paths or quoting enter the AppleScript string.
        return "printf '%s' '\(Data(install.utf8).base64EncodedString())' | /usr/bin/base64 -D | /bin/sh"
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
