import SwiftUI
import Combine

final class SystemMapSelection: ObservableObject { @Published var selected: String? }

/// A live connection path, with supporting services and explicit failure details.
struct SystemMap: View {
    let components: [Component]
    let onAction: (Component) -> Void
    @StateObject private var selection = SystemMapSelection()
    private var selected: String? { selection.selected }
    private let path = ["phone", "cloudflare", "tunnel", "desktop", "server", "gateway"]
    private let support = ["network", "background", "awake", "lid", "history", "access", "queue"]
    private let labels = ["phone":"Phone access", "cloudflare":"Cloudflare edge", "tunnel":"Tunnel connector", "desktop":"ChatGPT", "server":"Companion", "gateway":"Protected gateway"]
    private func item(_ id: String) -> Component? { components.first { $0.id == id } }
    private var readyCount: Int { components.filter { $0.state == "ready" }.count }
    private var current: Component? { item(selected ?? "") ?? components.first { $0.state == "warning" || $0.state == "stopped" } ?? components.first }
    private func linkReady(_ a: String, _ b: String) -> Bool { item(a)?.state == "ready" && item(b)?.state == "ready" }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("System map").font(.system(size: 18, weight: .semibold)).tracking(-0.4)
                    Text("Follow the connection. Tap any node to inspect it.").font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                Text("\(readyCount) / \(components.count) ready").font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(Color.primary).padding(.horizontal, 12).padding(.vertical, 7).background(CompanionTheme.line.opacity(0.35), in: Capsule())
            }
            ProgressView(value: Double(readyCount), total: Double(max(components.count, 1))).tint(readyCount == components.count ? CompanionTheme.success : CompanionTheme.warning)
                .accessibilityLabel("Components ready").accessibilityValue("\(readyCount) of \(components.count)")
            GeometryReader { geometry in
                let width = (geometry.size.width - 60) / 3
                let x = [width / 2, width * 1.5 + 30, width * 2.5 + 60]
                let y: [CGFloat] = [54, 204]
                ForEach(Array(path.enumerated()), id: \.element) { index, id in
                    if let component = item(id) {
                        node(component, compact: false).frame(width: width, height: 108).position(x: x[index % 3], y: y[index / 3])
                    }
                }
                arrow("arrow.right", ready: linkReady("phone","cloudflare")).position(x: (x[0] + x[1]) / 2, y: y[0])
                arrow("arrow.right", ready: linkReady("cloudflare","tunnel")).position(x: (x[1] + x[2]) / 2, y: y[0])
                arrow("arrow.down", ready: linkReady("tunnel","gateway")).position(x: x[2], y: 129)
                arrow("arrow.left", ready: linkReady("gateway","server")).position(x: (x[1] + x[2]) / 2, y: y[1])
                arrow("arrow.left", ready: linkReady("server","desktop")).position(x: (x[0] + x[1]) / 2, y: y[1])
            }.frame(height: 258)
            HStack(spacing: 8) {
                Image(systemName: "point.3.connected.trianglepath.dotted").foregroundStyle(.secondary)
                Text("Supporting systems keep the path running").font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), spacing: 8)], spacing: 8) {
                ForEach(support, id: \.self) { id in if let component = item(id) { node(component, compact: true) } }
            }
            if let component = current {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: component.icon).font(.system(size: 19)).foregroundStyle(stateColor(component.state)).frame(width: 28)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(component.title + " · " + stateLabel(component.state)).font(.system(size: 13, weight: .semibold))
                        Text(component.detail).font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3)
                    }
                    Spacer(minLength: 5)
                    if component.action != nil { Button(component.action_title ?? "Open") { onAction(component) }.font(.system(size: 11)).buttonStyle(.bordered).controlSize(.small) }
                }.padding(15).frame(maxWidth: .infinity, alignment: .leading).background(stateColor(component.state).opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
            }
            HStack(spacing: 14) {
                legend("Ready", color: stateColor("ready")); legend("Needs attention", color: stateColor("warning")); legend("Offline", color: stateColor("stopped"))
                Spacer()
                Text("Live checks · no chat content").font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }.padding(22).companionCard(radius: 24)
    }

    private func node(_ component: Component, compact: Bool) -> some View {
        let color = stateColor(component.state)
        return Button { selection.selected = component.id } label: {
            Group {
                if compact {
                    HStack(spacing: 7) {
                        Image(systemName: component.icon).font(.system(size: 12)).frame(width: 17)
                        Text(component.title).font(.system(size: 10, weight: .medium)).lineLimit(2)
                        Spacer(minLength: 0)
                        Image(systemName: component.state == "ready" ? "checkmark.circle.fill" : "exclamationmark.circle.fill").font(.system(size: 10))
                    }.padding(10).frame(maxWidth: .infinity, minHeight: 42)
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: component.icon).font(.system(size: 22)).frame(height: 26)
                        Text(labels[component.id] ?? component.title).font(.system(size: 12, weight: .semibold)).lineLimit(1)
                        HStack(spacing: 4) { Image(systemName: component.state == "ready" ? "checkmark.circle.fill" : "exclamationmark.circle.fill"); Text(stateLabel(component.state)) }.font(.system(size: 10, weight: .medium))
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }.foregroundStyle(color).background(color.opacity(0.055), in: RoundedRectangle(cornerRadius: compact ? 10 : 15))
                .overlay(RoundedRectangle(cornerRadius: compact ? 10 : 15).stroke(selected == component.id ? color : color.opacity(0.17), lineWidth: selected == component.id ? 2 : 1))
        }.buttonStyle(.plain).accessibilityLabel(component.title + ", " + stateLabel(component.state) + ". " + component.detail)
    }
    private func arrow(_ icon: String, ready: Bool) -> some View {
        Image(systemName: icon).font(.system(size: 15, weight: .medium)).foregroundStyle(ready ? stateColor("ready").opacity(0.6) : stateColor("warning"))
    }
    private func legend(_ text: String, color: Color) -> some View {
        HStack(spacing: 5) { Circle().fill(color).frame(width: 5, height: 5); Text(text).font(.system(size: 10)) }.foregroundStyle(.secondary)
    }
}
