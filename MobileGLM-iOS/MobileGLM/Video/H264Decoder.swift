import Foundation
import VideoToolbox
import CoreMedia
import CoreVideo

/// Callback for decoded frames
protocol H264DecoderDelegate: AnyObject {
    func decoder(_ decoder: H264Decoder, didDecode pixelBuffer: CVPixelBuffer, presentationTime: CMTime)
    func decoder(_ decoder: H264Decoder, didEncounterError error: Error)
}

/// Hardware H.264 decoder using VideoToolbox
class H264Decoder {

    weak var delegate: H264DecoderDelegate?

    private var formatDescription: CMVideoFormatDescription?
    private var decompressionSession: VTDecompressionSession?

    private let nalParser = NALUParser()
    private var frameCount: UInt64 = 0

    // Timing
    private let timebase: CMTimebase
    private var lastPresentationTime: CMTime = .zero

    enum DecoderError: Error {
        case failedToCreateTimebase(OSStatus)
        case failedToCreateFormatDescription(OSStatus)
        case failedToCreateDecompressionSession(OSStatus)
        case failedToCreateBlockBuffer(OSStatus)
        case failedToCreateSampleBuffer(OSStatus)
        case failedToDecode(OSStatus)
        case missingParameterSets
        case invalidNALUnit
    }

    init() throws {
        // Create timebase for low-latency playback
        var timebase: CMTimebase?
        let status = CMTimebaseCreateWithSourceClock(
            allocator: kCFAllocatorDefault,
            sourceClock: CMClockGetHostTimeClock(),
            timebaseOut: &timebase
        )
        guard status == noErr, let tb = timebase else {
            throw DecoderError.failedToCreateTimebase(status)
        }
        self.timebase = tb
        CMTimebaseSetRate(tb, rate: 1.0)
    }

    /// Process incoming H.264 data from WebSocket
    func decode(_ data: Data) {
        let nalUnits = nalParser.parse(data)

        // Debug: log what we received
        if !nalUnits.isEmpty {
            let types = nalUnits.map { "\($0.type.rawValue)" }.joined(separator: ",")
            print("[H264Decoder] Parsed \(nalUnits.count) NAL units: types=[\(types)]")
        }

        // Check if we received parameter sets
        if nalParser.hasParameterSets && formatDescription == nil {
            do {
                try initializeDecoder()
                print("[H264Decoder] Decoder initialized successfully")
            } catch {
                print("[H264Decoder] Failed to initialize: \(error)")
                delegate?.decoder(self, didEncounterError: error)
                return
            }
        }

        // Decode video NAL units
        for unit in nalUnits {
            // Skip parameter sets (already processed)
            guard !unit.type.isParameterSet else {
                print("[H264Decoder] Skipping parameter set: type=\(unit.type.rawValue)")
                continue
            }

            // Need decoder initialized before decoding frames
            guard decompressionSession != nil else {
                print("[H264Decoder] Skipping frame - no session yet")
                continue
            }

            do {
                try decodeNALUnit(unit)
            } catch {
                print("[H264Decoder] Decode error: \(error)")
                delegate?.decoder(self, didEncounterError: error)
            }
        }
    }

    /// Initialize decoder with SPS/PPS
    private func initializeDecoder() throws {
        guard let sps = nalParser.sps, let pps = nalParser.pps else {
            throw DecoderError.missingParameterSets
        }

        // Create format description from parameter sets
        let parameterSets = [sps, pps]
        let parameterSetSizes = parameterSets.map { $0.count }

        var formatDesc: CMFormatDescription?

        let status = parameterSets.withUnsafeBufferPointers { pointers in
            let pointerArray = pointers.map { $0.baseAddress! }
            return pointerArray.withUnsafeBufferPointer { ptrBuffer in
                parameterSetSizes.withUnsafeBufferPointer { sizeBuffer in
                    CMVideoFormatDescriptionCreateFromH264ParameterSets(
                        allocator: kCFAllocatorDefault,
                        parameterSetCount: parameterSets.count,
                        parameterSetPointers: ptrBuffer.baseAddress!,
                        parameterSetSizes: sizeBuffer.baseAddress!,
                        nalUnitHeaderLength: 4,
                        formatDescriptionOut: &formatDesc
                    )
                }
            }
        }

        guard status == noErr, let fd = formatDesc else {
            throw DecoderError.failedToCreateFormatDescription(status)
        }

        formatDescription = fd
        try createDecompressionSession(formatDescription: fd)

        print("[H264Decoder] Initialized with SPS/PPS")
    }

    /// Create VideoToolbox decompression session
    private func createDecompressionSession(formatDescription: CMVideoFormatDescription) throws {
        // Decoder specification - VideoToolbox uses hardware by default
        // nil lets the system choose the best decoder automatically
        let decoderSpecification: CFDictionary? = nil

        // Output pixel buffer attributes
        let destinationAttributes: [CFString: Any] = [
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
            kCVPixelBufferMetalCompatibilityKey: true
        ]

        // Callback for decoded frames
        var callback = VTDecompressionOutputCallbackRecord(
            decompressionOutputCallback: { decompressionOutputRefCon, sourceFrameRefCon, status, infoFlags, imageBuffer, presentationTimeStamp, presentationDuration in
                guard let refCon = decompressionOutputRefCon else { return }
                let decoder = Unmanaged<H264Decoder>.fromOpaque(refCon).takeUnretainedValue()
                decoder.handleDecodedFrame(status: status, imageBuffer: imageBuffer, pts: presentationTimeStamp)
            },
            decompressionOutputRefCon: Unmanaged.passUnretained(self).toOpaque()
        )

        var session: VTDecompressionSession?
        let status = VTDecompressionSessionCreate(
            allocator: kCFAllocatorDefault,
            formatDescription: formatDescription,
            decoderSpecification: decoderSpecification,
            imageBufferAttributes: destinationAttributes as CFDictionary,
            outputCallback: &callback,
            decompressionSessionOut: &session
        )

        guard status == noErr, let s = session else {
            throw DecoderError.failedToCreateDecompressionSession(status)
        }

        // Configure for low latency
        VTSessionSetProperty(s, key: kVTDecompressionPropertyKey_RealTime, value: kCFBooleanTrue)

        decompressionSession = s
        print("[H264Decoder] Decompression session created")
    }

    /// Decode a single NAL unit
    private func decodeNALUnit(_ unit: NALUnit) throws {
        guard let session = decompressionSession, let formatDesc = formatDescription else {
            return
        }

        // Convert to AVCC format (4-byte length prefix)
        let avccData = NALUParser.toAVCC(unit.data)

        // Create block buffer with COPIED data (critical for async decode)
        var blockBuffer: CMBlockBuffer?

        // First create an empty block buffer
        var status = CMBlockBufferCreateEmpty(
            allocator: kCFAllocatorDefault,
            capacity: 0,
            flags: 0,
            blockBufferOut: &blockBuffer
        )

        guard status == noErr, let bb = blockBuffer else {
            throw DecoderError.failedToCreateBlockBuffer(status)
        }

        // Append data with copy flag - this copies the data into the buffer
        status = avccData.withUnsafeBytes { (rawBuffer: UnsafeRawBufferPointer) -> OSStatus in
            CMBlockBufferAppendMemoryBlock(
                bb,
                memoryBlock: nil,  // nil = allocate new memory
                length: avccData.count,
                blockAllocator: kCFAllocatorDefault,
                customBlockSource: nil,
                offsetToData: 0,
                dataLength: avccData.count,
                flags: 0
            )
        }

        guard status == noErr else {
            throw DecoderError.failedToCreateBlockBuffer(status)
        }

        // Copy the actual data into the allocated block
        status = avccData.withUnsafeBytes { (rawBuffer: UnsafeRawBufferPointer) -> OSStatus in
            CMBlockBufferReplaceDataBytes(
                with: rawBuffer.baseAddress!,
                blockBuffer: bb,
                offsetIntoDestination: 0,
                dataLength: avccData.count
            )
        }

        guard status == noErr else {
            throw DecoderError.failedToCreateBlockBuffer(status)
        }

        // Create sample buffer
        var sampleBuffer: CMSampleBuffer?
        var sampleSize = avccData.count

        // Calculate presentation time
        frameCount += 1
        let pts = CMTime(value: CMTimeValue(frameCount), timescale: 30) // Assume 30fps

        var timingInfo = CMSampleTimingInfo(
            duration: CMTime(value: 1, timescale: 30),
            presentationTimeStamp: pts,
            decodeTimeStamp: .invalid
        )

        status = CMSampleBufferCreateReady(
            allocator: kCFAllocatorDefault,
            dataBuffer: bb,
            formatDescription: formatDesc,
            sampleCount: 1,
            sampleTimingEntryCount: 1,
            sampleTimingArray: &timingInfo,
            sampleSizeEntryCount: 1,
            sampleSizeArray: &sampleSize,
            sampleBufferOut: &sampleBuffer
        )

        guard status == noErr, let sb = sampleBuffer else {
            throw DecoderError.failedToCreateSampleBuffer(status)
        }

        // Decode
        var flags = VTDecodeFrameFlags._EnableAsynchronousDecompression
        var infoFlags = VTDecodeInfoFlags()

        status = VTDecompressionSessionDecodeFrame(
            session,
            sampleBuffer: sb,
            flags: flags,
            frameRefcon: nil,
            infoFlagsOut: &infoFlags
        )

        if status != noErr {
            throw DecoderError.failedToDecode(status)
        }
    }

    /// Handle decoded frame callback
    private func handleDecodedFrame(status: OSStatus, imageBuffer: CVImageBuffer?, pts: CMTime) {
        guard status == noErr else {
            delegate?.decoder(self, didEncounterError: DecoderError.failedToDecode(status))
            return
        }

        guard let pixelBuffer = imageBuffer else { return }

        lastPresentationTime = pts
        delegate?.decoder(self, didDecode: pixelBuffer, presentationTime: pts)
    }

    /// Flush decoder
    func flush() {
        guard let session = decompressionSession else { return }
        VTDecompressionSessionWaitForAsynchronousFrames(session)
    }

    /// Reset decoder state
    func reset() {
        if let session = decompressionSession {
            VTDecompressionSessionInvalidate(session)
        }
        decompressionSession = nil
        formatDescription = nil
        nalParser.reset()
        frameCount = 0
    }

    deinit {
        reset()
    }
}

// MARK: - Helper extension for parameter set handling

extension Array where Element == Data {
    func withUnsafeBufferPointers<R>(_ body: ([UnsafeBufferPointer<UInt8>]) -> R) -> R {
        var pointers: [UnsafeBufferPointer<UInt8>] = []

        func withRemainingPointers(index: Int, accumulated: [UnsafeBufferPointer<UInt8>]) -> R {
            if index >= count {
                return body(accumulated)
            }
            return self[index].withUnsafeBytes { (buffer: UnsafeRawBufferPointer) -> R in
                let typedPointer = buffer.bindMemory(to: UInt8.self)
                return withRemainingPointers(index: index + 1, accumulated: accumulated + [typedPointer])
            }
        }

        return withRemainingPointers(index: 0, accumulated: [])
    }
}
