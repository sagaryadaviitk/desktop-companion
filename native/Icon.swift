import AppKit

let folder = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let side = CGFloat(pixels)
        let shape = NSBezierPath(roundedRect: NSRect(x: side * 0.07, y: side * 0.07, width: side * 0.86, height: side * 0.86), xRadius: side * 0.20, yRadius: side * 0.20)
        NSGradient(starting: NSColor(white: 0.24, alpha: 1), ending: NSColor(white: 0.10, alpha: 1))!.draw(in: shape, angle: -65)
        NSColor(white: 1, alpha: 0.20).setStroke()
        shape.lineWidth = max(1, side * 0.005)
        shape.stroke()
        if let symbol = NSImage(systemSymbolName: "macbook.and.iphone", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: side * 0.42, weight: .medium))?
            .withSymbolConfiguration(.init(paletteColors: [.white])) {
            symbol.draw(in: NSRect(x: side * 0.22, y: side * 0.28, width: side * 0.56, height: side * 0.44))
        }
        image.unlockFocus()
        let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let suffix = scale == 2 ? "@2x" : ""
        try rep.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
