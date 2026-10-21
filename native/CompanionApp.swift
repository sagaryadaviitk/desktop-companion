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
    let localConversations = ConversationSession(initialURL: CompanionWebNavigation.localChats)
    @Published var page = "overview"
    @Published var showRestartDesktop = false
    @Published var snapshot: Snapshot?
    @Published var code: PairCode?
    @Published var notice = ""
    @Published var error = false
    @Published var busy = false
    @Published var refreshing = false
    private var timer: AnyCancellable?
    private var activation: AnyCancellable?
    private var actionGeneration = 0
    private var waitingForRecovery = false
    private var expectedPowerSettings: UnattendedSettings?
    private var nextStatusRead = Date.distantPast
    private var statusFailures = 0
    private let statusSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 3
        configuration.timeoutIntervalForResource = 4
        return URLSession(configuration: configuration)
    }()
    @Published private var statusReadFailed = false
    @Published private var statusClock = Date()
    var statusStale: Bool { !StatusFreshness.isFresh(checkedAt: snapshot?.checked_at, failed: statusReadFailed, now: statusClock.timeIntervalSince1970) }
    var components: [Component] { (snapshot?.components ?? []).map { item in
        guard statusStale else { return item }
        return Component(id: item.id, title: item.title, state: "unknown", detail: "Current status unavailable. Last recorded: " + item.detail, icon: item.icon, action: item.action, action_title: item.action_title)
    } }

    init() {
        conversations.onOpenLocal = { [weak self] url in
            guard let self = self else { return }
            self.localConversations.open(url)
            self.page = "local"
        }
        refresh()
        timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect().sink { [weak self] _ in self?.refresh(force: false) }
        activation = NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification).sink { [weak self] _ in self?.refresh() }
    }

    private func request(_ action: String, id: String? = nil, completion: @escaping (Reply?) -> Void) {
        guard action == "status" else { requestFromProcess(action, id: id, completion: completion); return }
        // Only observational status uses HTTP. Actions keep the existing native
        // process path and are never retried after a lost acknowledgement.
        let url = URL(string: "http://127.0.0.1:8765/api/control/status")!
        statusSession.dataTask(with: url) { [weak self] data, response, _ in
            if let data = data, (response as? HTTPURLResponse)?.statusCode == 200,
               let reply = try? JSONDecoder().decode(Reply.self, from: data), reply.ok, reply.snapshot != nil {
                DispatchQueue.main.async { completion(reply) }
            } else {
                // Recovery remains usable when the web service is stopped or an
                // older installed service has not acquired this endpoint yet.
                self?.requestFromProcess("status", completion: completion)
            }
        }.resume()
    }

    private func requestFromProcess(_ action: String, id: String? = nil, completion: @escaping (Reply?) -> Void) {
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
                DispatchQueue.global().asyncAfter(deadline: .now() + (action == "update_locked_use" ? 150 : action == "status" ? 8 : 30), execute: deadline)
                defer { deadline.cancel() }
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if process.terminationStatus == 0 { reply = try JSONDecoder().decode(Reply.self, from: data) }
            } catch { reply = nil }
            DispatchQueue.main.async { completion(reply) }
        }
    }

    func refresh(force: Bool = true) {
        statusClock = Date()
        guard !refreshing, !busy else { return }
        guard force || statusClock >= nextStatusRead else { return }
        refreshing = true
        let generation = actionGeneration
        request("status") { [weak self] reply in
            guard let self = self else { return }
            self.refreshing = false
            guard self.actionGeneration == generation else { return }
            if let value = reply?.snapshot {
                self.statusFailures = 0
                self.nextStatusRead = Date().addingTimeInterval(NSApp.isActive ? 2 : 15)
                self.snapshot = value
                if self.statusReadFailed { self.notice = "Connection restored. Status is current."; self.error = false }
                self.statusReadFailed = false
                self.statusClock = Date()
                if let expected = self.expectedPowerSettings, expected.matches(keepWorking: value.power?.closed_lid_enabled, battery: value.power?.display_sleep_battery_minutes, adapter: value.power?.display_sleep_adapter_minutes) {
                    self.expectedPowerSettings = nil
                    self.notice = "Settings verified: screen off after \(expected.batteryMinutes) min on battery and \(expected.adapterMinutes) min on power. " + (expected.keepWorking ? "Background work can continue with the lid closed." : "Normal lid-close sleep is restored.")
                    self.error = false
                } else if self.expectedPowerSettings != nil {
                    self.expectedPowerSettings = nil
                    self.notice = "The requested settings could not all be verified. Check Currently applied below and apply again when ready."
                    self.error = true
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
            } else {
                self.statusFailures = min(self.statusFailures + 1, 16)
                self.nextStatusRead = Date().addingTimeInterval(StatusRetry.delay(failures: self.statusFailures, random: Double.random(in: 0...1)))
                self.statusReadFailed = true
                self.statusClock = Date()
                self.notice = "Current status is unavailable. Previous readings are shown as unknown; try Refresh before choosing recovery."
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

    func applyPowerSettings(_ settings: UnattendedSettings) {
        guard !busy, let command = settings.command else { return }
        actionGeneration += 1
        busy = true
        error = false
        notice = "Authenticate with macOS to apply screen timeouts and lid behavior."
        // Fixed system commands only. macOS owns the password prompt; no password is read or stored by this app.
        let source = "do shell script \"" + command + "\" with administrator privileges"
        DispatchQueue.global(qos: .userInitiated).async {
            var failure: NSDictionary?
            if let script = NSAppleScript(source: source) {
                script.executeAndReturnError(&failure)
            } else { failure = [NSAppleScript.errorNumber: -1] }
            let code = failure?[NSAppleScript.errorNumber] as? Int
            DispatchQueue.main.async {
                self.busy = false
                if failure == nil {
                    self.expectedPowerSettings = settings
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
    @Published var loading = false
    var onOpenLocal: ((URL) -> Void)?
    private let initial: URL
    private var lastRequested: URL
    private var hasLoaded = false
    init(initialURL: URL = CompanionWebNavigation.conversations) {
        initial = initialURL
        lastRequested = initialURL
        super.init()
    }
    lazy var webView: WKWebView = {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        return view
    }()
    func startIfNeeded() { if !hasLoaded { open(initial) } }
    func open(_ url: URL) {
        guard let target = CompanionWebNavigation.embedded(url) else { return }
        hasLoaded = true
        lastRequested = target
        if failed { failed = false }
        webView.load(URLRequest(url: target, timeoutInterval: 15))
    }
    func reload() { open(failed ? lastRequested : webView.url ?? lastRequested) }
    func refreshLocal() {
        if failed || !hasLoaded || webView.url == nil { reload(); return }
        guard !loading, let url = webView.url, CompanionWebNavigation.isLocalChat(url) else { return }
        // The page's observational refresh keeps drafts and pending requests.
        // This fixed DOM action exposes no native command or script interface.
        webView.evaluateJavaScript("document.getElementById('local-refresh')?.click();", completionHandler: nil)
    }
    func openInBrowser() {
        if let target = CompanionWebNavigation.browserURL(webView.url ?? lastRequested) { NSWorkspace.shared.open(target) }
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { loading = true }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { failed = false; loading = false }
    private func navigationFailed(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        failed = true
        loading = false
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { failed = true; loading = false }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if CompanionWebNavigation.isTrusted(url) {
            if navigationAction.targetFrame?.isMainFrame != false, CompanionWebNavigation.isLocalChat(url), let openLocal = onOpenLocal {
                openLocal(url)
                decisionHandler(.cancel)
                return
            }
            if navigationAction.targetFrame?.isMainFrame == true { lastRequested = url }
            decisionHandler(.allow)
            return
        }
        // Only an explicit clicked link opens a browser. Redirects, frames and
        // script navigation cannot place external content in this local view.
        if navigationAction.navigationType == .linkActivated && CompanionWebNavigation.canOpenInBrowser(url) { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = navigationResponse.response.url, CompanionWebNavigation.isTrusted(url) else { decisionHandler(.cancel); return }
        if navigationResponse.isForMainFrame, let response = navigationResponse.response as? HTTPURLResponse, response.statusCode >= 400 {
            failed = true
            loading = false
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url else { return nil }
        if CompanionWebNavigation.isTrusted(url) {
            if CompanionWebNavigation.isLocalChat(url), let openLocal = onOpenLocal { openLocal(url) }
            else { open(url) }
        } else if navigationAction.navigationType == .linkActivated && CompanionWebNavigation.canOpenInBrowser(url) { NSWorkspace.shared.open(url) }
        return nil
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard let url = frame.request.url, CompanionWebNavigation.isTrusted(url), let window = webView.window else { completionHandler(false); return }
        let alert = NSAlert()
        alert.messageText = "Confirm in Desktop Companion"
        alert.informativeText = String(message.prefix(4000)) + "\n\nRequested by Companion on this Mac (127.0.0.1:8765)."
        alert.addButton(withTitle: "Continue")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { response in completionHandler(response == .alertFirstButtonReturn) }
    }
}

struct ConversationWebView: NSViewRepresentable {
    let session: ConversationSession
    func makeNSView(context: Context) -> WKWebView { session.startIfNeeded(); return session.webView }
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

struct LocalChatsPane: View {
    @ObservedObject var session: ConversationSession
    let showOverview: () -> Void
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(systemName: "sparkles").font(.system(size: 19)).foregroundStyle(CompanionTheme.action)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Local chats").font(.system(size: 17, weight: .semibold))
                    Text("On this Mac · the same chats as your phone").font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer()
                if session.loading { ProgressView().controlSize(.small).accessibilityLabel("Loading local chats") }
                Button("Open in browser") { session.openInBrowser() }
                Button("Refresh") { session.refreshLocal() }.disabled(session.loading)
            }.font(.system(size: 11)).padding(.horizontal, 22).padding(.vertical, 16).background(CompanionTheme.surface)
            ZStack {
                ConversationWebView(session: session)
                if session.failed {
                    VStack(spacing: 16) {
                        Image(systemName: "desktopcomputer.trianglebadge.exclamationmark").font(.system(size: 34)).foregroundStyle(CompanionTheme.action)
                        Text("Local chats are unavailable right now").font(.system(size: 19, weight: .semibold))
                        Text("Your saved chats stay on this Mac. Check the desktop service in Overview, then try again.")
                            .font(.system(size: 13)).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 390)
                        HStack(spacing: 12) {
                            Button("Check Overview", action: showOverview).companionControl()
                            Button("Try again") { session.reload() }.companionControl(prominent: true)
                        }.controlSize(.large)
                    }.padding(35).frame(maxWidth: .infinity, maxHeight: .infinity).background(canvas)
                }
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
    @AppStorage("unattended-battery-minutes") private var batteryMinutes = UnattendedSettings.defaultBattery
    @AppStorage("unattended-adapter-minutes") private var adapterMinutes = UnattendedSettings.defaultAdapter
    @AppStorage("unattended-keep-working") private var keepWorking = true
    private var selectedPowerSettings: UnattendedSettings { UnattendedSettings(keepWorking: keepWorking, batteryMinutes: batteryMinutes, adapterMinutes: adapterMinutes) }
    private var powerSettingsApplied: Bool { !model.statusStale && selectedPowerSettings.matches(keepWorking: model.snapshot?.power?.closed_lid_enabled, battery: model.snapshot?.power?.display_sleep_battery_minutes, adapter: model.snapshot?.power?.display_sleep_adapter_minutes) }
    private var attentionCount: Int { model.components.filter { $0.state == "warning" || $0.state == "stopped" }.count }
    private var overviewTitle: String {
        guard model.snapshot != nil else { return "Checking your system" }
        if model.statusStale { return "Current status unavailable" }
        if attentionCount > 0 { return "\(attentionCount) component\(attentionCount == 1 ? "" : "s") need\(attentionCount == 1 ? "s" : "") attention" }
        return model.components.allSatisfy { $0.state == "ready" } ? "All components are ready" : "Your available services are ready"
    }
    private var page: String { model.page }
    private let columns = [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)]

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            if page == "local" {
                LocalChatsPane(session: model.localConversations) { model.page = "overview" }
            } else if page == "chats" {
                VStack(spacing: 0) {
                    HStack(spacing: 12) {
                        Text("Conversations").font(.system(size: 16, weight: .semibold))
                        Spacer()
                        Button("Open in browser") { model.openInBrowser() }
                        Button("Reload") { model.conversations.reload() }
                        Button("Restart service") { model.perform("restart_web") }.disabled(model.busy)
                    }.font(.system(size: 11)).padding(.horizontal, 22).padding(.vertical, 15).background(CompanionTheme.surface)
                    if model.components.first(where: {$0.id == "server"})?.state == "stopped" {
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
                navButton("Local chats", icon: "sparkles", value: "local")
                navButton("Conversations", icon: "bubble.left.and.bubble.right", value: "chats")
                navButton("Unattended mode", icon: "moon.zzz", value: "power")
                navButton("Phone access", icon: "iphone", value: "phone")
            }
            Spacer()
            VStack(alignment: .leading, spacing: 12) {
                Text("YOUR PHONE ADDRESS").font(.system(size: 9, weight: .medium)).tracking(1).foregroundStyle(.secondary)
                Text(model.snapshot?.phone_url.isEmpty == false ? (model.snapshot?.phone_url ?? "") : "Not configured").font(.system(size: 11, weight: .medium)).textSelection(.enabled)
                Rectangle().fill(CompanionTheme.line).frame(height: 1).padding(.vertical, 6)
                HStack(spacing: 7) { Circle().fill(model.statusStale ? Color.secondary : model.snapshot?.running == true ? Color.green.opacity(0.8) : Color.orange).frame(width: 5, height: 5); Text(model.statusStale ? "Status unavailable" : model.snapshot?.running == true ? "Running in background" : "System is stopped").font(.system(size: 11)) }
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
                Image(systemName: model.statusStale ? "questionmark.circle" : model.snapshot?.running != true ? "power" : attentionCount == 0 ? "checkmark.shield" : "exclamationmark.triangle").font(.system(size: 30)).foregroundStyle(model.statusStale ? Color.secondary : attentionCount > 0 ? CompanionTheme.warning : green)
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.statusStale ? overviewTitle : model.snapshot?.running == true ? overviewTitle : "Start your background system").font(.system(size: 17, weight: .semibold))
                    Text(model.statusStale ? "Live checks are unavailable. Historical values are retained; no component is reported ready until checks recover." : model.snapshot?.summary?.detail ?? "Automatic recovery stays on after you close this window.").font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                Button(model.snapshot?.running == true ? "Restart system" : "Start system") { model.perform(model.snapshot?.running == true ? "restart" : "start") }
                    .companionControl(prominent: true).controlSize(.large).disabled(model.busy)
            }.padding(22).companionCard()
            if model.snapshot != nil {
                SystemMap(components: model.components) { item in componentAction(item) }
                DisclosureGroup("Detailed components (\(model.components.count))") {
                    LazyVGrid(columns: columns, spacing: 14) { ForEach(model.components) { item in componentCard(item) } }.padding(.top, 14)
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
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("Keep your Mac working").font(.system(size: 19, weight: .semibold))
                    Spacer()
                    StatusPill(state: model.statusStale ? "unknown" : model.snapshot?.power?.closed_lid_enabled == true ? "ready" : "disabled")
                }
                Toggle("Keep working when the lid is closed", isOn: $keepWorking).font(.system(size: 14, weight: .medium)).disabled(model.busy)
                Text(keepWorking ? "Your screen can turn off and lock while chats continue on battery or power." : "Closing the lid will use normal macOS sleep behavior. Phone access pauses while the Mac sleeps.").font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
                Divider()
                Text("Screen off & lock").font(.system(size: 15, weight: .semibold))
                HStack(spacing: 24) {
                    timeoutPicker("On battery", selection: $batteryMinutes, icon: "battery.50")
                    timeoutPicker("Plugged in", selection: $adapterMinutes, icon: "powerplug")
                }.disabled(model.busy)
                Text("Defaults: 5 min on battery · 10 min plugged in. These are screen timeouts, not a timer for stopping background work.").font(.system(size: 12)).foregroundStyle(.secondary)
                Button("Use default timers") { batteryMinutes = UnattendedSettings.defaultBattery; adapterMinutes = UnattendedSettings.defaultAdapter }.font(.system(size: 12)).disabled(model.busy)
                if model.snapshot?.power?.lock_immediate != true {
                    Label("Immediate password lock is not confirmed. Check macOS Lock Screen settings.", systemImage: "lock.trianglebadge.exclamationmark").font(.system(size: 12)).foregroundStyle(CompanionTheme.warning)
                }
                HStack(spacing: 12) {
                    Button(powerSettingsApplied ? "Settings applied" : "Apply settings…") { model.applyPowerSettings(selectedPowerSettings) }.companionControl(prominent: true).controlSize(.large).disabled(model.busy || powerSettingsApplied || selectedPowerSettings.command == nil)
                    Button("Lock screen now") { model.lockNow() }.controlSize(.large).disabled(model.busy || model.statusStale || model.snapshot?.power?.lock_immediate != true)
                }
                Text("macOS asks for administrator authentication. Lid behavior is system-wide and remains set after Companion closes; switch it off here to restore normal lid sleep. No low-battery cutoff is configured.").font(.system(size: 11)).foregroundStyle(.secondary).lineSpacing(3)
                if let power = model.snapshot?.power {
                    Text("\(model.statusStale ? "Last known" : "Currently applied"): battery \(power.display_sleep_battery_minutes.map { "\($0) min" } ?? "unknown") · plugged in \(power.display_sleep_adapter_minutes.map { "\($0) min" } ?? "unknown")").font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }.padding(22).companionCard()
            VStack(alignment: .leading, spacing: 12) {
                Text("Ready for unattended work?").font(.system(size: 16, weight: .semibold))
                readinessRow("Background work", ready: model.snapshot?.power?.idle_guard_active == true, detail: "Companion keeps the Mac awake while it runs.")
                readinessRow("Phone connection", ready: model.components.first(where: {$0.id == "phone"})?.state == "ready", detail: "Checks the route from your phone to this Mac.")
                Button("View system status") { model.page = "overview" }.controlSize(.large)
            }.padding(22).companionCard()
            DisclosureGroup("Use desktop apps while locked") {
                VStack(alignment: .leading, spacing: 12) {
                    Text("ChatGPT Locked Use is separate from keeping the Mac awake. It needs an enabled, current authorization component and a trusted Computer Use turn. macOS can still require a manual unlock.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3)
                    Text("Installed: \(model.snapshot?.computer_use?.installed_version ?? "Not detected") · Available: \(model.snapshot?.computer_use?.available_version ?? "Unavailable")").font(.system(size: 11)).foregroundStyle(.secondary)
                    HStack {
                        Button("Open ChatGPT") { NSWorkspace.shared.open(URL(fileURLWithPath: desktopAppPath)) }.controlSize(.large)
                        Button("Update Locked Use…") { model.perform("update_locked_use") }.controlSize(.large).disabled(model.busy || model.snapshot?.computer_use?.update_available != true)
                    }
                }.padding(.top, 12)
            }.font(.system(size: 14, weight: .medium)).padding(22).companionCard()
        }
    }

    private func timeoutPicker(_ title: String, selection: Binding<Int>, icon: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon).font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
            Picker(title, selection: selection) { ForEach(UnattendedSettings.timeouts, id: \.self) { Text("\($0) minutes").tag($0) } }.labelsHidden().frame(maxWidth: .infinity)
        }
    }

    private func readinessRow(_ title: String, ready: Bool, detail: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: model.statusStale ? "questionmark.circle" : ready ? "checkmark.circle.fill" : "exclamationmark.circle").foregroundStyle(model.statusStale ? Color.secondary : ready ? green : CompanionTheme.warning)
            VStack(alignment: .leading, spacing: 3) { Text(title).font(.system(size: 13, weight: .medium)); Text(model.statusStale ? "Current check unavailable" : detail).font(.system(size: 11)).foregroundStyle(.secondary) }
            Spacer()
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
