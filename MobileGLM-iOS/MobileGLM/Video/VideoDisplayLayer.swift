import SwiftUI
import AVFoundation
import CoreMedia
import CoreVideo

/// UIView wrapper for AVSampleBufferDisplayLayer
class VideoDisplayUIView: UIView {

    override class var layerClass: AnyClass {
        AVSampleBufferDisplayLayer.self
    }

    var displayLayer: AVSampleBufferDisplayLayer {
        layer as! AVSampleBufferDisplayLayer
    }

    private var timebase: CMTimebase?

    override init(frame: CGRect) {
        super.init(frame: frame)
        setup()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setup()
    }

    private func setup() {
        backgroundColor = .black

        // Configure display layer for low latency
        displayLayer.videoGravity = .resizeAspect

        // Create and set control timebase for realtime playback
        var timebase: CMTimebase?
        CMTimebaseCreateWithSourceClock(
            allocator: kCFAllocatorDefault,
            sourceClock: CMClockGetHostTimeClock(),
            timebaseOut: &timebase
        )

        if let tb = timebase {
            self.timebase = tb
            displayLayer.controlTimebase = tb
            CMTimebaseSetTime(tb, time: .zero)
            CMTimebaseSetRate(tb, rate: 1.0)
        }
    }

    /// Display a decoded pixel buffer
    func enqueue(pixelBuffer: CVPixelBuffer, presentationTime: CMTime) {
        // Create format description from pixel buffer
        var formatDescription: CMVideoFormatDescription?
        CMVideoFormatDescriptionCreateForImageBuffer(
            allocator: kCFAllocatorDefault,
            imageBuffer: pixelBuffer,
            formatDescriptionOut: &formatDescription
        )

        guard let formatDesc = formatDescription else {
            print("[VideoDisplay] Failed to create format description")
            return
        }

        // Create timing info - use current time for low latency
        let now = CMClockGetTime(CMClockGetHostTimeClock())
        var timing = CMSampleTimingInfo(
            duration: CMTime(value: 1, timescale: 30),
            presentationTimeStamp: now,
            decodeTimeStamp: .invalid
        )

        // Update timebase to current time
        if let tb = timebase {
            CMTimebaseSetTime(tb, time: now)
        }

        // Create sample buffer
        var sampleBuffer: CMSampleBuffer?
        CMSampleBufferCreateForImageBuffer(
            allocator: kCFAllocatorDefault,
            imageBuffer: pixelBuffer,
            dataReady: true,
            makeDataReadyCallback: nil,
            refcon: nil,
            formatDescription: formatDesc,
            sampleTiming: &timing,
            sampleBufferOut: &sampleBuffer
        )

        guard let sb = sampleBuffer else {
            print("[VideoDisplay] Failed to create sample buffer")
            return
        }

        // Mark for immediate display
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: true)
        if let attachments = attachments, CFArrayGetCount(attachments) > 0 {
            let dict = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(dict,
                Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                Unmanaged.passUnretained(kCFBooleanTrue).toOpaque()
            )
        }

        // Enqueue for display
        if displayLayer.isReadyForMoreMediaData {
            displayLayer.enqueue(sb)
        }
    }

    /// Flush pending frames
    func flush() {
        displayLayer.flushAndRemoveImage()
    }
}

/// SwiftUI wrapper for VideoDisplayUIView
struct VideoDisplayView: UIViewRepresentable {

    let displayView: VideoDisplayUIView

    init(displayView: VideoDisplayUIView) {
        self.displayView = displayView
    }

    func makeUIView(context: Context) -> VideoDisplayUIView {
        displayView
    }

    func updateUIView(_ uiView: VideoDisplayUIView, context: Context) {
        // No updates needed
    }
}
