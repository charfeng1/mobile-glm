import Foundation

/// Device information received from the WebSocket bridge
struct DeviceInfo: Codable {
    let type: String
    let name: String
    let width: Int
    let height: Int
    let serial: String

    var aspectRatio: CGFloat {
        guard height > 0 else { return 1.0 }
        return CGFloat(width) / CGFloat(height)
    }
}
