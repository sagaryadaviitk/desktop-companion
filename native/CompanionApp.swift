import SwiftUI
import AppKit
import Combine
import WebKit

private let ink = Color.primary
private let green = CompanionTheme.success
private let desktopAppPath = Bundle.main.object(forInfoDictionaryKey: "CompanionDesktopApp") as? String ?? "/Applications/ChatGPT.app"
private let cliDirectory = Bundle.main.object(forInfoDictionaryKey: "CompanionCLIPath") as? String ?? ""

private let canvas = CompanionTheme.canvas

struct Component: Decodable, Identifiable {
    let id, title, state, detail, icon: String
    let action, action_title: String?
}
struct Device: Decodable, Identifiable {
    let id, name, status: String
    let created, expires: Double
    let last_seen, revoked_at: Double?
}
struct PairingEvent: Decodable, Identifiable {
    let id, status: String
    let created, expires: Double
}
struct PairCode: Decodable {
    let id, code: String
    let expires_at: Double
}
struct PowerSnapshot: Decodable {
    let source: String
    let battery_percent: Int?
    let lid_closed, closed_lid_enabled: Bool?
    let lock_immediate: Bool?
    let display_sleep_battery_minutes, display_sleep_adapter_minutes: Int?
    let idle_guard_active: Bool
}
struct ComputerUseSnapshot: Decodable {
    let state: String
    let installed_version, available_version: String?
    let update_available: Bool
    let checked_at: Double?
    let stale: Bool
}
struct Snapshot: Decodable {
    let components: [Component]
    let devices: [Device]
    let pairing_history: [PairingEvent]
    let phone_url, dashboard_url: String
    let can_pair, running, plugged_in: Bool
    let checked_at: Double
    let access_error: String?
    let power: PowerSnapshot?
    let computer_use: ComputerUseSnapshot?
    let summary: SystemSummary?
}
struct SystemSummary: Decodable { let state, title, detail: String }
struct Reply: Decodable {
    let ok: Bool
    let snapshot: Snapshot?
    let pairing: PairCode?
    let message: String?
}

final class ControlModel: ObservableObject {
    let conversations = ConversationSession()
    @Published var page = "overview"
    @Published var showRestartDesktop = false
    @Published var snapshot: Snapshot?
    @Published var code: PairCode?
    @Published var notice = ""
    @Published var error = false
    @Published var busy = false
    @Published var refreshing = false
    private var timer: AnyCancellable?
    private var actionGeneration = 0
    private var waitingForRecovery = false
    private var expectedSleepSetting: Bool?

    init() {
        refresh()
        timer = Timer.publish(every: 5, on: .main, in: .common).autoconnect().sink { [weak self] _ in self?.refresh() }
    }

    private func request(_ action: String, id: String? = nil, completion: @escaping (Reply?) -> Void) {
        let root = Bundle.main.object(forInfoDictionaryKey: "CompanionRoot") as? String ?? ""
        let python = Bundle.main.object(forInfoDictionaryKey: "CompanionPython") as? String ?? ""
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [root + "/control.py", action] + (id.map { ["--id", $0] } ?? [])
            process.currentDirectoryURL = URL(fileURLWithPath: root)
            process.environment = ["HOME": NSHomeDirectory(), "PATH": cliDirectory + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"]
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            var reply: Reply?
            do {
                try process.run()
                let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + (action == "update_locked_use" ? 150 : 30), execute: deadline)
                defer { deadline.cancel() }
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if process.terminationStatus == 0 { reply = try JSONDecoder().decode(Reply.self, from: data) }
            } catch { reply = nil }
            DispatchQueue.main.async { completion(reply) }
        }
    }

    func refresh() {
        guard !refreshing, !busy else { return }
        refreshing = true
        let generation = actionGeneration
        request("status") { [weak self] reply in
            guard let self = self else { return }
            self.refreshing = false
            guard self.actionGeneration == generation else { return }
            if let value = reply?.snapshot {
                self.snapshot = value
                if let expected = self.expectedSleepSetting, value.power?.closed_lid_enabled == expected,
                   !expected || (value.power?.display_sleep_battery_minutes == 1 && value.power?.display_sleep_adapter_minutes == 1) {
                    self.expectedSleepSetting = nil
                    self.notice = expected ? "Unattended mode is enabled, with a one-minute display timeout. Close the lid and check the phone dashboard to verify your Mac stays reachable." : "Lid-triggered sleep is restored. The one-minute display timeout and background idle guard remain configured."
                    self.error = false
                }
                if self.waitingForRecovery && value.running && value.components.filter({ $0.id == "server" || $0.id == "phone" }).allSatisfy({ $0.state == "ready" }) {
                    self.waitingForRecovery = false
                    self.notice = "Your system is ready."
                    self.error = false
                }
                if let code = self.code, let event = value.pairing_history.first(where: {$0.id == code.id}), event.status != "pending" {
                    self.code = nil
                    if event.status == "used" { self.notice = "Your phone is connected."; self.error = false }
                }
            } else if self.snapshot == nil {
                self.notice = "Status is unavailable. You can still use Start or Restart to repair the system."
                self.error = true
            }
        }
    }

    func perform(_ action: String, id: String? = nil) {
        guard !busy else { return }
        actionGeneration += 1
        busy = true
        error = false
        notice = action == "pair" ? "Creating a one-use code…" : action == "update_locked_use" ? "Complete the OpenAI installer’s macOS authentication prompt. This app never reads or stores your password." : "Working on it…"
        if action == "restart" || action == "stop" { code = nil }
        waitingForRecovery = action == "restart" || action == "start"
        request(action, id: id) { [weak self] reply in
            guard let self = self else { return }
            self.busy = false
            if let reply = reply, reply.ok {
                self.notice = reply.message ?? "Done."
                if let pairing = reply.pairing { self.code = pairing }
                if action == "cancel_code" { self.code = nil }
            } else {
                self.waitingForRecovery = false
                self.notice = reply?.message ?? "That action did not complete. Check the status and try again."
                self.error = true
            }
            self.refresh()
        }
    }

    func openDashboard() {
        page = "chats"
    }

    func setClosedLid(_ enabled: Bool) {
        guard !busy else { return }
        actionGeneration += 1
        busy = true
        error = false
        notice = "Authenticate with macOS to change the system sleep setting."
        // Fixed system commands only. macOS owns the password prompt; no password is read or stored by this app.
        let source = enabled
            ? #"do shell script "/usr/bin/pmset -a disablesleep 1 displaysleep 1" with administrator privileges"#
            : #"do shell script "/usr/bin/pmset -a disablesleep 0" with administrator privileges"#
        DispatchQueue.global(qos: .userInitiated).async {
            var failure: NSDictionary?
            if let script = NSAppleScript(source: source) {
                script.executeAndReturnError(&failure)
            } else { failure = [NSAppleScript.errorNumber: -1] }
            let code = failure?[NSAppleScript.errorNumber] as? Int
            DispatchQueue.main.async {
                self.busy = false
                if failure == nil {
                    self.expectedSleepSetting = enabled
                    self.notice = "macOS accepted the change. Checking the actual sleep setting…"
                } else {
                    self.error = code != -128
                    self.notice = code == -128 ? "Authentication cancelled." : "macOS could not change the sleep setting. Use an administrator account and try again."
                }
                self.refresh()
            }
        }
    }

    func openInBrowser() {
        if let url = URL(string: snapshot?.dashboard_url ?? "http://127.0.0.1:8765") { NSWorkspace.shared.open(url) }
    }

    func lockNow() {
        guard !busy else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/pmset")
            process.arguments = ["displaysleepnow"]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            do { try process.run(); process.waitUntilExit() } catch {}
        }
    }

    func openDesktop() {
        NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: desktopAppPath), configuration: NSWorkspace.OpenConfiguration()) { _, _ in }
    }

    func restartDesktop() {
        let bundle = Bundle(url: URL(fileURLWithPath: desktopAppPath))?.bundleIdentifier
        if let bundle = bundle {
            for app in NSRunningApplication.runningApplications(withBundleIdentifier: bundle) { app.terminate() }
        }
        notice = "ChatGPT is restarting. Interrupted work may need to be resumed."
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { self.openDesktop(); self.refresh() }
    }
}

final class ConversationSession: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate {
    @Published var failed = false
    private let initial = URL(string: "http://127.0.0.1:8765/?embedded=1")!
    lazy var webView: WKWebView = {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.load(URLRequest(url: initial))
        return view
    }()
    func reload() { failed = false; webView.load(URLRequest(url: webView.url ?? initial)) }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { failed = false }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failed = true }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed = true }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "http" && url.host == "127.0.0.1" && url.port == 8765 { decisionHandler(.allow); return }
        if ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        return nil
    }
}

struct ConversationWebView: NSViewRepresentable {
    let session: ConversationSession
    func makeNSView(context: Context) -> WKWebView { session.webView }
    func updateNSView(_ view: WKWebView, context: Context) { view.appearance = NSAppearance(named: context.environment.colorScheme == .dark ? .darkAqua : .aqua) }
}

struct ConversationPane: View {
    @ObservedObject var session: ConversationSession
    var body: some View {
        ZStack {
            ConversationWebView(session: session)
            if session.failed {
                VStack(spacing: 15) {
                    Image(systemName: "wifi.exclamationmark").font(.system(size: 30)).foregroundStyle(green)
                    Text("The conversation connection was interrupted.").font(.system(size: 16, weight: .semibold))
                    Text("Check the service in Overview, then reload this view.").font(.system(size: 12)).foregroundStyle(.secondary)
                    Button("Reload conversations") { session.reload() }.controlSize(.large)
                }.frame(maxWidth: .infinity, maxHeight: .infinity).background(canvas)
            }
        }
    }
}

func shortDate(_ value: Double) -> String {
    Date(timeIntervalSince1970: value).formatted(date: .abbreviated, time: .shortened)
}
func stateColor(_ state: String) -> Color {
    switch state {
    case "ready", "active", "used": return green
    case "warning", "pending": return CompanionTheme.warning
    case "stopped", "revoked": return CompanionTheme.danger
    default: return .secondary
    }
}
func stateLabel(_ state: String) -> String {
    switch state {
    case "ready": return "Ready"
    case "warning": return "Attention"
    case "stopped": return "Offline"
    case "pending": return "Unused"
    default: return state.capitalized
    }
}

struct StatusPill: View {
    let state: String
    var body: some View {
        HStack(spacing: 5) { Circle().fill(stateColor(state)).frame(width: 5, height: 5); Text(stateLabel(state)).font(.system(size: 11, weight: .semibold)) }
            .foregroundStyle(stateColor(state)).padding(.horizontal, 9).padding(.vertical, 5)
            .background(stateColor(state).opacity(0.08), in: Capsule())
    }
}

struct ControlView: View {
    @StateObject private var model = ControlModel()
    @AppStorage("companion-appearance") private var appearance = "system"
    private var colorScheme: ColorScheme? { appearance == "dark" ? .dark : appearance == "light" ? .light : nil }
    private var attentionCount: Int { model.snapshot?.components.filter { $0.state == "warning" || $0.state == "stopped" }.count ?? 0 }
    private var overviewTitle: String {
        guard let value = model.snapshot else { return "Checking your system" }
        if attentionCount > 0 { return "\(attentionCount) component\(attentionCount == 1 ? "" : "s") need\(attentionCount == 1 ? "s" : "") attention" }
        return value.components.allSatisfy { $0.state == "ready" } ? "All components are ready" : "Your available services are ready"
    }
    private var page: String { model.page }
    private let columns = [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)]

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            if page == "chats" {
                VStack(spacing: 0) {
                    HStack(spacing: 12) {
                        Text("Conversations").font(.system(size: 16, weight: .semibold))
                        Spacer()
                        Button("Open in browser") { model.openInBrowser() }
                        Button("Reload") { model.conversations.reload() }
                        Button("Restart service") { model.perform("restart_web") }.disabled(model.busy)
                    }.font(.system(size: 11)).padding(.horizontal, 22).padding(.vertical, 15).background(CompanionTheme.surface)
                    if model.snapshot?.components.first(where: {$0.id == "server"})?.state == "stopped" {
                        VStack(spacing: 15) {
                            Text("Start the desktop service to see your chats.").font(.system(size: 17, weight: .semibold))
                            Text("Your saved conversations remain on your Mac.").font(.system(size: 12)).foregroundStyle(.secondary)
                            Button("Start system") { model.perform("start") }.companionControl(prominent: true).disabled(model.busy)
                        }.frame(maxWidth: .infinity, maxHeight: .infinity).background(canvas)
                    } else { ConversationPane(session: model.conversations) }
                }
            } else {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header
                    if !model.notice.isEmpty {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: model.error ? "exclamationmark.circle" : "checkmark.circle")
                            Text(model.notice).font(.system(size: 12)).textSelection(.enabled)
                            Spacer()
                        }.foregroundStyle(model.error ? Color.red : green).padding(14)
                            .frame(maxWidth: .infinity, alignment: .leading).background(model.error ? Color.red.opacity(0.06) : green.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                    }
                    if page == "overview" { overview } else if page == "power" { unattended } else { phoneAccess }
                    Text("Closing this window keeps your work running. Your Mac must remain online and logged in.")
                        .font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 4)
                }.padding(30).frame(maxWidth: 1000, alignment: .leading).frame(maxWidth: .infinity)
            }.background(canvas)
            }
        }
        .foregroundStyle(ink).tint(CompanionTheme.action).preferredColorScheme(colorScheme)
        .frame(minWidth: 960, minHeight: 690)
        .confirmationDialog("Restart ChatGPT?", isPresented: $model.showRestartDesktop, titleVisibility: .visible) {
            Button("Restart ChatGPT", role: .destructive) { model.restartDesktop() }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This closes the desktop app and can interrupt active chats. The companion and paired browsers stay available.") }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 28) {
            VStack(alignment: .leading, spacing: 14) {
                Image(systemName: "macbook.and.iphone").font(.system(size: 27, weight: .regular)).foregroundStyle(.primary)
                Text("Desktop\nCompanion").font(.system(size: 22, weight: .semibold)).lineSpacing(2)
                Text("CONTROL CENTER").font(.system(size: 9, weight: .medium)).tracking(1.8).foregroundStyle(.secondary)
            }.padding(.top, 20).padding(.bottom, 4)
            VStack(spacing: 7) {
                navButton("Overview", icon: "square.grid.2x2", value: "overview")
                navButton("Conversations", icon: "bubble.left.and.bubble.right", value: "chats")
                navButton("Unattended mode", icon: "moon.zzz", value: "power")
                navButton("Phone access", icon: "iphone", value: "phone")
            }
            Spacer()
            VStack(alignment: .leading, spacing: 12) {
                Text("YOUR PHONE ADDRESS").font(.system(size: 9, weight: .medium)).tracking(1).foregroundStyle(.secondary)
                Text(model.snapshot?.phone_url.isEmpty == false ? (model.snapshot?.phone_url ?? "") : "Not configured").font(.system(size: 11, weight: .medium)).textSelection(.enabled)
                Rectangle().fill(CompanionTheme.line).frame(height: 1).padding(.vertical, 6)
                HStack(spacing: 7) { Circle().fill(model.snapshot?.running == true ? Color.green.opacity(0.8) : Color.orange).frame(width: 5, height: 5); Text(model.snapshot?.running == true ? "Running in background" : "System is stopped").font(.system(size: 11)) }
                Menu {
                    Picker("Appearance", selection: $appearance) {
                        Text("Match Mac").tag("system")
                        Text("Light").tag("light")
                        Text("Dark").tag("dark")
                    }.pickerStyle(.inline)
                } label: { Label("Appearance", systemImage: "circle.lefthalf.filled").font(.system(size: 12)) }
                .menuStyle(.borderlessButton).fixedSize().help("Light, Dark, or Match Mac")
            }
        }.padding(22).frame(width: 224).frame(maxHeight: .infinity).background(CompanionTheme.sidebar).overlay(alignment: .trailing) { Rectangle().fill(CompanionTheme.line.opacity(0.5)).frame(width: 0.5) }
    }

    private func navButton(_ title: String, icon: String, value: String) -> some View {
        Button { model.page = value } label: {
            HStack(spacing: 10) { Image(systemName: icon).frame(width: 17); Text(title).font(.system(size: 13, weight: .medium)); Spacer() }
                .padding(.horizontal, 12).padding(.vertical, 12).companionSelection(page == value)
        }.buttonStyle(.plain).foregroundStyle(page == value ? Color.primary : Color.secondary)
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 7) {
                Text(page == "overview" ? "Your system, at a glance" : page == "power" ? "Keep working while you’re away" : "Connect your phone").font(.system(size: 27, weight: .semibold)).tracking(-0.6)
                Text(page == "overview" ? "Check every component and get back to work." : page == "power" ? "Battery power, screen lock, and closed-lid operation." : "Create a code, connect a browser, and manage its access.").font(.system(size: 12)).foregroundStyle(.secondary)
            }
            Spacer()
            Button { model.refresh() } label: { Label(model.refreshing ? "Checking…" : "Refresh", systemImage: "arrow.clockwise").font(.system(size: 12)) }
                .companionControl().disabled(model.refreshing || model.busy).controlSize(.large)
        }
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(spacing: 17) {
                Image(systemName: model.snapshot?.running != true ? "power" : attentionCount == 0 ? "checkmark.shield" : "exclamationmark.triangle").font(.system(size: 30)).foregroundStyle(attentionCount > 0 ? CompanionTheme.warning : green)
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.snapshot?.running == true ? overviewTitle : "Start your background system").font(.system(size: 17, weight: .semibold))
                    Text(model.snapshot?.summary?.detail ?? "Automatic recovery stays on after you close this window.").font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                Button(model.snapshot?.running == true ? "Restart system" : "Start system") { model.perform(model.snapshot?.running == true ? "restart" : "start") }
                    .companionControl(prominent: true).controlSize(.large).disabled(model.busy)
            }.padding(22).companionCard()
            if let value = model.snapshot {
                SystemMap(components: value.components) { item in componentAction(item) }
                DisclosureGroup("Detailed components (\(value.components.count))") {
                    LazyVGrid(columns: columns, spacing: 14) { ForEach(value.components) { item in componentCard(item) } }.padding(.top, 14)
                }.font(.system(size: 13, weight: .medium))
            } else {
                ProgressView("Checking your components…").frame(maxWidth: .infinity).padding(50)
            }
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Need a new phone pairing code?").font(.system(size: 13, weight: .semibold))
                    Text("Create one in Phone access. Existing access is listed there too.").font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Manage phone access") { model.page = "phone" }.controlSize(.large)
            }.padding(20).background(green.opacity(0.045), in: RoundedRectangle(cornerRadius: 12))
            HStack {
                Button("Open conversations") { model.openDashboard() }
                Spacer()
                if let value = model.snapshot { Text("Checked " + Date(timeIntervalSince1970: value.checked_at).formatted(date: .omitted, time: .standard)).font(.system(size: 10)).foregroundStyle(.secondary) }
                Button("Stop system") { model.perform("stop") }.foregroundStyle(.secondary).disabled(model.busy || model.snapshot?.running != true)
            }.font(.system(size: 12))
        }
    }

    private func componentAction(_ item: Component) {
        guard let action = item.action else { return }
        if action == "open_desktop" { model.openDesktop() }
        else if action == "open_dashboard" { model.openDashboard() }
        else if action == "open_power" { model.page = "power" }
        else if action == "open_phone" { model.page = "phone" }
        else if action == "open_network", let url = URL(string: "x-apple.systempreferences:com.apple.wifi-settings-extension") { NSWorkspace.shared.open(url) }
        else { model.perform(action) }
    }

    private func componentCard(_ item: Component) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Image(systemName: item.icon).font(.system(size: 19)).foregroundStyle(green).frame(width: 32, height: 32).background(green.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                Spacer()
                StatusPill(state: item.state)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(item.title).font(.system(size: 14, weight: .semibold))
                Text(item.detail).font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3).frame(maxWidth: .infinity, minHeight: 36, alignment: .topLeading)
            }
            HStack {
                if item.action != nil {
                    Button(item.action_title ?? "Restart") { componentAction(item) }.font(.system(size: 11, weight: .medium)).disabled(model.busy)
                }
                if item.id == "desktop" { Spacer(); Button("Restart app…") { model.showRestartDesktop = true }.font(.system(size: 10)).foregroundStyle(.secondary) }
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).companionCard()
    }

    private var unattended: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 14) {
                powerMetric("POWER SOURCE", model.snapshot?.power?.source == "battery" ? "On battery" : model.snapshot?.power?.source == "adapter" ? "On charger" : "Checking…", icon: "battery.100")
                powerMetric("BATTERY", model.snapshot?.power?.battery_percent.map { "\($0)%" } ?? "Unavailable", icon: "bolt")
                powerMetric("LID", model.snapshot?.power?.lid_closed == true ? "Closed" : model.snapshot?.power?.lid_closed == false ? "Open" : "Unavailable", icon: "laptopcomputer")
                powerMetric("PASSWORD LOCK", model.snapshot?.power?.lock_immediate == true ? "Immediate" : model.snapshot?.power?.lock_immediate == false ? "Delayed" : "Unavailable", icon: "lock")
            }
            VStack(alignment: .leading, spacing: 13) {
                HStack { Text("Keep awake on battery and charger").font(.system(size: 17, weight: .semibold)); Spacer(); StatusPill(state: model.snapshot?.power?.idle_guard_active == true ? "ready" : "warning") }
                Text("The background guard prevents idle sleep while the companion runs. Your display can turn off and your Mac can lock. No companion low-battery cutoff is configured.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(4)
                Button(model.snapshot?.running == true ? "Restart guard" : "Start system") { model.perform(model.snapshot?.running == true ? "restart" : "start") }.disabled(model.busy).controlSize(.large)
                HStack {
                    Text("Display timeout: battery \(model.snapshot?.power?.display_sleep_battery_minutes.map { "\($0) min" } ?? "unknown") · charger \(model.snapshot?.power?.display_sleep_adapter_minutes.map { "\($0) min" } ?? "unknown")").font(.system(size: 11)).foregroundStyle(.secondary)
                    Spacer()
                    Button("Lock now & keep working") { model.lockNow() }.disabled(model.busy || model.snapshot?.power?.lock_immediate != true).controlSize(.large)
                }
            }.padding(22).companionCard()
            VStack(alignment: .leading, spacing: 15) {
                HStack { Text("Closed-lid mode").font(.system(size: 17, weight: .semibold)); Spacer(); Text(model.snapshot?.power?.closed_lid_enabled == true ? "ENABLED" : model.snapshot?.power?.closed_lid_enabled == false ? "OFF" : "UNAVAILABLE").font(.system(size: 10, weight: .semibold)).foregroundStyle(green) }
                Text("Keep working with the lid shut, including on battery, and turn the display off after one minute idle on both power sources. Your immediate password requirement locks the Mac when the display turns off. macOS asks for your administrator password to apply the power settings.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(4)
                HStack(spacing: 12) {
                    Button("Enable unattended mode…") { model.setClosedLid(true) }.companionControl(prominent: true).controlSize(.large).disabled(model.busy || (model.snapshot?.power?.closed_lid_enabled == true && model.snapshot?.power?.display_sleep_battery_minutes == 1 && model.snapshot?.power?.display_sleep_adapter_minutes == 1))
                    Button("Restore lid sleep…") { model.setClosedLid(false) }.controlSize(.large).disabled(model.busy || model.snapshot?.power?.closed_lid_enabled == false)
                }
                Text("This is a system-wide setting. It also blocks manual sleep and stays enabled after quitting this app or stopping the companion. Restore lid sleep here when you want normal lid-close behavior.").font(.system(size: 11)).foregroundStyle(.secondary).lineSpacing(3)
            }.padding(22).companionCard()
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text("Desktop apps while locked").font(.system(size: 17, weight: .semibold)); Spacer(); Text(model.snapshot?.computer_use?.update_available == true ? "UPDATE AVAILABLE" : model.snapshot?.computer_use?.state == "passed" ? "LAST TEST PASSED" : "CHECK REQUIRED").font(.system(size: 10, weight: .semibold)).foregroundStyle(green) }
                Text("Keeping your Mac awake does not by itself enable native desktop app access while locked. ChatGPT Locked Use also needs its current authorization component and an active, trusted Computer Use turn.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(4)
                Text("Installed: \(model.snapshot?.computer_use?.installed_version ?? "Not detected") · Available: \(model.snapshot?.computer_use?.available_version ?? "Unavailable")").font(.system(size: 11)).foregroundStyle(.secondary)
                if model.snapshot?.computer_use?.state == "blocked" { Text("Last native Chrome test: automatic unlock did not complete. Background work and direct message delivery continued.").font(.system(size: 12)).foregroundStyle(.orange) }
                if model.snapshot?.computer_use?.state == "passed" { Text("Last test: native Chrome access succeeded after the lock confirmation, with macOS recording an approved Locked Use unlock. Local keyboard or pointer input can pause automatic unlock until you unlock manually.").font(.system(size: 12)).foregroundStyle(.secondary) }
                if let checked = model.snapshot?.computer_use?.checked_at { Text("Tested \(Date(timeIntervalSince1970: checked).formatted(date: .abbreviated, time: .shortened))\(model.snapshot?.computer_use?.stale == true ? " · Previous check" : "")").font(.system(size: 11)).foregroundStyle(.secondary) }
                Button("Update Locked Use…") { model.perform("update_locked_use") }.companionControl(prominent: true).controlSize(.large).disabled(model.busy || model.snapshot?.computer_use?.update_available != true)
                Text("Uses OpenAI’s signed installer and the macOS-owned administrator prompt. After updating, verify native Chrome from your phone with the lid closed; installation alone is not a successful access test.").font(.system(size: 11)).foregroundStyle(.secondary).lineSpacing(3)
            }.padding(22).companionCard()
            VStack(alignment: .leading, spacing: 10) {
                Text("Verify the complete connection").font(.system(size: 14, weight: .semibold))
                Text("After enabling closed-lid mode, close your Mac for a minute and open your configured phone address over mobile data. The header should keep updating with your Mac’s battery and lid state.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(4)
                Text("Leave the running Mac on a hard, ventilated surface. Wi-Fi must remain available; a depleted battery, shutdown, or loss of internet ends the connection.").font(.system(size: 11)).foregroundStyle(.secondary).lineSpacing(3)
            }.padding(20).background(green.opacity(0.045), in: RoundedRectangle(cornerRadius: 12))
        }
    }

    private func powerMetric(_ title: String, _ value: String, icon: String) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            Image(systemName: icon).font(.system(size: 20)).foregroundStyle(green)
            Text(title).font(.system(size: 9, weight: .medium)).tracking(1).foregroundStyle(.secondary)
            Text(value).font(.system(size: 18, weight: .semibold))
        }.padding(19).frame(maxWidth: .infinity, alignment: .leading).companionCard()
    }

    private var phoneAccess: some View {
        VStack(alignment: .leading, spacing: 24) {
            if let issue = model.snapshot?.access_error { Text(issue).font(.system(size: 12)).foregroundStyle(.red) }
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(model.snapshot?.phone_url.isEmpty == false ? "1  Open this address on your phone" : "Phone access is not configured").font(.system(size: 13, weight: .semibold))
                        Text(model.snapshot?.phone_url ?? "").font(.system(size: 16, weight: .medium, design: .monospaced)).foregroundStyle(green).textSelection(.enabled)
                    }
                    Spacer()
                    Button("Copy address") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(model.snapshot?.phone_url ?? "", forType: .string) }.font(.system(size: 11)).disabled(model.snapshot?.phone_url.isEmpty != false)
                }
                Divider()
                Text("2  Create a one-use pairing code").font(.system(size: 13, weight: .semibold))
                Text("Enter the code directly on your phone. It expires in five minutes. Creating a new code replaces any unused code.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3)
                if let code = model.code {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        if context.date.timeIntervalSince1970 * 1000 < code.expires_at {
                            VStack(alignment: .leading, spacing: 12) {
                                Text(code.code).font(.system(size: 28, weight: .medium, design: .monospaced)).tracking(3).privacySensitive().textSelection(.enabled)
                                HStack {
                                    let remaining = max(0, Int(code.expires_at / 1000 - context.date.timeIntervalSince1970))
                                    Text(String(format: "Expires in %d:%02d · Keep this code private", remaining / 60, remaining % 60)).font(.system(size: 11)).foregroundStyle(.secondary)
                                    Spacer()
                                    Button("Cancel code") { model.perform("cancel_code", id: code.id) }.font(.system(size: 11)).disabled(model.busy)
                                }
                            }.padding(18).background(green.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                        } else { Text("That code has expired. Create a new one below.").font(.system(size: 12)).foregroundStyle(.secondary) }
                    }
                }
                HStack {
                    Button(model.code == nil ? "Create pairing code" : "Create replacement code") { model.perform("pair") }
                        .companionControl(prominent: true).controlSize(.large).disabled(model.busy || model.snapshot?.can_pair != true)
                    if model.snapshot?.can_pair != true { Text("Wait for Phone connection to be ready.").font(.system(size: 11)).foregroundStyle(.secondary) }
                }
            }.padding(24).companionCard()
            accessList
            codeHistory
        }
    }

    private var accessList: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Browser access").font(.system(size: 17, weight: .semibold))
            Text("Review active and previous connections. Revoking a browser blocks its next request. Secret tokens are never displayed.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3)
            if let devices = model.snapshot?.devices, !devices.isEmpty {
                VStack(spacing: 0) {
                    ForEach(devices) { device in
                        HStack(alignment: .top, spacing: 14) {
                            Image(systemName: "iphone").font(.system(size: 19)).foregroundStyle(.secondary).padding(.top, 2)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(device.name).font(.system(size: 13, weight: .semibold))
                                Text("Paired " + shortDate(device.created)).font(.system(size: 11)).foregroundStyle(.secondary)
                                Text(device.status == "revoked" ? "Revoked " + shortDate(device.revoked_at ?? device.created) : (device.status == "expired" ? "Expired " : "Expires ") + shortDate(device.expires)).font(.system(size: 11)).foregroundStyle(.secondary)
                                if let last = device.last_seen { Text("Last used " + shortDate(last)).font(.system(size: 11)).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            StatusPill(state: device.status)
                            if device.status == "active" { Button("Revoke access") { model.perform("revoke", id: device.id) }.font(.system(size: 11)).disabled(model.busy) }
                        }.padding(17)
                        if device.id != devices.last?.id { Divider().padding(.horizontal, 17) }
                    }
                }.companionCard()
            } else {
                HStack(spacing: 13) { Image(systemName: "iphone.badge.play").font(.system(size: 22)).foregroundStyle(.secondary); VStack(alignment: .leading, spacing: 5) { Text("No browsers paired yet").font(.system(size: 13, weight: .medium)); Text("Your phone will appear here after you enter a pairing code.").font(.system(size: 12)).foregroundStyle(.secondary) }; Spacer() }.padding(24).companionCard()
            }
        }
    }

    private var codeHistory: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Pairing code history").font(.system(size: 17, weight: .semibold))
            Text("Code values are shown once. History keeps dates and outcomes so old codes stay private.").font(.system(size: 12)).foregroundStyle(.secondary)
            if let events = model.snapshot?.pairing_history, !events.isEmpty {
                VStack(spacing: 0) {
                    ForEach(events) { event in
                        HStack {
                            Image(systemName: "key.horizontal").foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) { Text("Created " + shortDate(event.created)).font(.system(size: 12)); Text("Valid until " + shortDate(event.expires)).font(.system(size: 10)).foregroundStyle(.secondary) }
                            Spacer()
                            StatusPill(state: event.status)
                            if event.status == "pending" { Button("Cancel") { model.perform("cancel_code", id: event.id) }.font(.system(size: 11)).disabled(model.busy) }
                        }.padding(15)
                        if event.id != events.last?.id { Divider().padding(.horizontal, 15) }
                    }
                }.companionCard()
            } else { Text("New pairing codes will appear here.").font(.system(size: 12)).foregroundStyle(.secondary).padding(20).frame(maxWidth: .infinity, alignment: .leading).companionCard() }
        }
    }
}

@main struct DesktopCompanionApp: App {
    var body: some Scene {
        WindowGroup("Desktop Companion") { ControlView() }
            .defaultSize(width: 1080, height: 820)
            .windowStyle(.hiddenTitleBar)
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}
