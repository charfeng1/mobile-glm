import SwiftUI
import CoreMedia
import CoreVideo

/// View that displays phone screen and handles touch forwarding
struct PhoneDisplayView: View {

    @ObservedObject var viewModel: PhoneDisplayViewModel

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Video display
                VideoDisplayView(displayView: viewModel.displayView)
                    .aspectRatio(viewModel.aspectRatio, contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                // Touch overlay
                TouchOverlay(
                    onTouchBegan: { point in
                        viewModel.handleTouch(action: "down", point: point, in: geometry.size)
                    },
                    onTouchMoved: { point in
                        viewModel.handleTouch(action: "move", point: point, in: geometry.size)
                    },
                    onTouchEnded: { point in
                        viewModel.handleTouch(action: "up", point: point, in: geometry.size)
                    }
                )
            }
            .background(Color.black)
        }
    }
}

/// Touch overlay using UIViewRepresentable for precise touch handling
struct TouchOverlay: UIViewRepresentable {

    var onTouchBegan: (CGPoint) -> Void
    var onTouchMoved: (CGPoint) -> Void
    var onTouchEnded: (CGPoint) -> Void

    func makeUIView(context: Context) -> TouchCaptureView {
        let view = TouchCaptureView()
        view.onTouchBegan = onTouchBegan
        view.onTouchMoved = onTouchMoved
        view.onTouchEnded = onTouchEnded
        return view
    }

    func updateUIView(_ uiView: TouchCaptureView, context: Context) {
        uiView.onTouchBegan = onTouchBegan
        uiView.onTouchMoved = onTouchMoved
        uiView.onTouchEnded = onTouchEnded
    }
}

/// UIView for capturing touch events
class TouchCaptureView: UIView {

    var onTouchBegan: ((CGPoint) -> Void)?
    var onTouchMoved: ((CGPoint) -> Void)?
    var onTouchEnded: ((CGPoint) -> Void)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = false
        backgroundColor = .clear
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = touches.first else { return }
        let point = touch.location(in: self)
        onTouchBegan?(point)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = touches.first else { return }
        let point = touch.location(in: self)
        onTouchMoved?(point)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = touches.first else { return }
        let point = touch.location(in: self)
        onTouchEnded?(point)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let touch = touches.first else { return }
        let point = touch.location(in: self)
        onTouchEnded?(point)
    }
}

/// View model managing video display and touch handling
class PhoneDisplayViewModel: ObservableObject {

    @Published var aspectRatio: CGFloat = 9.0 / 16.0  // Default phone aspect ratio
    @Published var decoderError: String?

    let displayView = VideoDisplayUIView()
    private(set) var decoder: H264Decoder?
    weak var webSocketClient: WebSocketClient?

    private var deviceWidth: Int = 1080
    private var deviceHeight: Int = 1920

    init() {
        do {
            decoder = try H264Decoder()
            decoder?.delegate = self
        } catch {
            print("[PhoneDisplay] Failed to create decoder: \(error)")
            decoderError = "Failed to initialize video decoder: \(error)"
        }
    }

    /// Configure with device info
    func configure(with deviceInfo: DeviceInfo, webSocket: WebSocketClient) {
        deviceWidth = deviceInfo.width
        deviceHeight = deviceInfo.height
        webSocketClient = webSocket

        DispatchQueue.main.async {
            self.aspectRatio = deviceInfo.aspectRatio
        }
    }

    /// Handle incoming video data
    func handleVideoData(_ data: Data) {
        decoder?.decode(data)
    }

    /// Handle touch event
    func handleTouch(action: String, point: CGPoint, in size: CGSize) {
        // Calculate video frame rect (accounting for aspect ratio fit)
        let videoAspect = aspectRatio
        let viewAspect = size.width / size.height

        var videoRect: CGRect
        if videoAspect > viewAspect {
            // Video is wider - letterbox top/bottom
            let height = size.width / videoAspect
            let y = (size.height - height) / 2
            videoRect = CGRect(x: 0, y: y, width: size.width, height: height)
        } else {
            // Video is taller - letterbox left/right
            let width = size.height * videoAspect
            let x = (size.width - width) / 2
            videoRect = CGRect(x: x, y: 0, width: width, height: size.height)
        }

        // Check if touch is within video bounds
        guard videoRect.contains(point) else { return }

        // Convert to normalized coordinates (0.0 - 1.0)
        let normalizedX = (point.x - videoRect.minX) / videoRect.width
        let normalizedY = (point.y - videoRect.minY) / videoRect.height

        // Send to WebSocket
        webSocketClient?.sendTouch(action: action, x: normalizedX, y: normalizedY)
    }

    /// Reset state
    func reset() {
        decoder?.reset()
        displayView.flush()
    }
}

// MARK: - H264DecoderDelegate

extension PhoneDisplayViewModel: H264DecoderDelegate {

    func decoder(_ decoder: H264Decoder, didDecode pixelBuffer: CVPixelBuffer, presentationTime: CMTime) {
        // Display frame on main thread
        DispatchQueue.main.async {
            self.displayView.enqueue(pixelBuffer: pixelBuffer, presentationTime: presentationTime)
        }
    }

    func decoder(_ decoder: H264Decoder, didEncounterError error: Error) {
        print("[PhoneDisplay] Decoder error: \(error)")
    }
}
