import SwiftUI
import AppKit

/// Neutral content surfaces shared by the control center and its system map.
enum CompanionTheme {
    static func adaptive(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: Double((value >> 16) & 255) / 255,
                           green: Double((value >> 8) & 255) / 255,
                           blue: Double(value & 255) / 255, alpha: 1)
        })
    }
    static let canvas = adaptive(0xf5f5f7, 0x202020)
    static let surface = adaptive(0xffffff, 0x232323)
    static let sidebar = adaptive(0xededf0, 0x202020)
    static let line = adaptive(0xdedee1, 0x383838)
    static let action = adaptive(0x2864c7, 0x2d66c3)
    static let actionText = Color.white
    static let success = adaptive(0x277451, 0x81cfa7)
    static let warning = adaptive(0x91610c, 0xedc57e)
    static let danger = adaptive(0xba3746, 0xf2999c)
}

private struct CompanionCard: ViewModifier {
    var radius: CGFloat
    func body(content: Content) -> some View {
        content.background(CompanionTheme.surface, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).stroke(CompanionTheme.line.opacity(0.65), lineWidth: 0.5).allowsHitTesting(false))
    }
}

private struct CompanionSelection: ViewModifier {
    let selected: Bool
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @ViewBuilder func body(content: Content) -> some View {
        if selected {
            #if compiler(>=6.2)
            if #available(macOS 26.0, *), !reduceTransparency {
                content.glassEffect(.regular, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            } else {
                content.background(CompanionTheme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            #else
            content.background(CompanionTheme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            #endif
        } else { content }
    }
}

private struct CompanionControl: ViewModifier {
    var prominent: Bool
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @ViewBuilder func body(content: Content) -> some View {
        if prominent {
            content.buttonStyle(CompanionPrimaryButtonStyle())
        } else {
            #if compiler(>=6.2)
            if #available(macOS 26.0, *), !reduceTransparency { content.buttonStyle(.glass) }
            else { content.buttonStyle(.bordered) }
            #else
            content.buttonStyle(.bordered)
            #endif
        }
    }
}

/// Opaque primary actions keep a predictable contrast over either material.
private struct CompanionPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 12, weight: .semibold))
            .padding(.horizontal, 17).padding(.vertical, 10)
            .foregroundStyle(CompanionTheme.actionText)
            .background(CompanionTheme.action, in: Capsule())
            .opacity(enabled ? (configuration.isPressed ? 0.8 : 1) : 0.45)
    }
}

extension View {
    func companionCard(radius: CGFloat = 20) -> some View { modifier(CompanionCard(radius: radius)) }
    func companionSelection(_ selected: Bool) -> some View { modifier(CompanionSelection(selected: selected)) }
    func companionControl(prominent: Bool = false) -> some View { modifier(CompanionControl(prominent: prominent)) }
}
