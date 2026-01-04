import Foundation

/// H.264 NAL Unit types
enum NALUnitType: UInt8 {
    case unspecified = 0
    case nonIDR = 1        // Non-IDR slice (P/B frame)
    case partitionA = 2
    case partitionB = 3
    case partitionC = 4
    case idr = 5           // IDR slice (keyframe)
    case sei = 6           // Supplemental enhancement info
    case sps = 7           // Sequence Parameter Set
    case pps = 8           // Picture Parameter Set
    case accessUnitDelimiter = 9
    case endOfSequence = 10
    case endOfStream = 11
    case fillerData = 12

    var isParameterSet: Bool {
        self == .sps || self == .pps
    }

    var isKeyframe: Bool {
        self == .idr
    }
}

/// Parsed NAL unit with type and raw data
struct NALUnit {
    let type: NALUnitType
    let data: Data  // NAL unit data WITHOUT start code

    var typeValue: UInt8 {
        type.rawValue
    }
}

/// Parser for H.264 Annex B NAL units
/// Handles both 3-byte (0x000001) and 4-byte (0x00000001) start codes
class NALUParser {

    // Accumulated buffer for incomplete NAL units
    private var buffer = Data()

    // Stored parameter sets
    private(set) var sps: Data?
    private(set) var pps: Data?

    var hasParameterSets: Bool {
        sps != nil && pps != nil
    }

    /// Parse incoming data and extract complete NAL units
    /// - Parameter data: Raw H.264 Annex B data from scrcpy
    /// - Returns: Array of parsed NAL units
    func parse(_ data: Data) -> [NALUnit] {
        // Check if this message starts with a start code
        let startsWithStartCode = startsWithNALStartCode(data)

        // If message starts with start code and buffer is empty,
        // this is likely a complete NAL unit from the bridge (post-fix)
        if startsWithStartCode && buffer.isEmpty {
            // Try to parse as a single complete NAL unit
            if let unit = parseSingleNAL(data) {
                updateParameterSets(unit)
                return [unit]
            }
        }

        // Fall back to streaming parser for fragmented data
        buffer.append(data)

        var units: [NALUnit] = []
        var searchStart = 0

        while searchStart < buffer.count {
            // Find next start code
            guard let (startCodePos, startCodeLen) = findStartCode(in: buffer, from: searchStart) else {
                break
            }

            // Find the start code after this one (marks end of current NAL)
            let nalStart = startCodePos + startCodeLen

            if let (nextStartCode, _) = findStartCode(in: buffer, from: nalStart) {
                // Extract complete NAL unit
                let nalData = buffer.subdata(in: nalStart..<nextStartCode)
                if let unit = parseNALUnit(nalData) {
                    units.append(unit)
                    updateParameterSets(unit)
                }
                searchStart = nextStartCode
            } else {
                // Incomplete NAL - keep in buffer
                buffer = buffer.subdata(in: startCodePos..<buffer.count)
                return units
            }
        }

        // Clear processed data
        buffer.removeAll()
        return units
    }

    /// Check if data starts with NAL start code
    private func startsWithNALStartCode(_ data: Data) -> Bool {
        guard data.count >= 3 else { return false }
        if data.count >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1 {
            return true  // 4-byte start code
        }
        if data[0] == 0 && data[1] == 0 && data[2] == 1 {
            return true  // 3-byte start code
        }
        return false
    }

    /// Parse a single complete NAL unit (with start code)
    private func parseSingleNAL(_ data: Data) -> NALUnit? {
        guard data.count >= 5 else { return nil }  // At least start code + 1 byte

        // Determine start code length
        let startCodeLen: Int
        if data.count >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1 {
            startCodeLen = 4
        } else if data[0] == 0 && data[1] == 0 && data[2] == 1 {
            startCodeLen = 3
        } else {
            return nil
        }

        // Extract NAL data (without start code)
        let nalData = data.subdata(in: startCodeLen..<data.count)
        return parseNALUnit(nalData)
    }

    /// Find start code position in data
    private func findStartCode(in data: Data, from offset: Int) -> (position: Int, length: Int)? {
        guard offset < data.count - 3 else { return nil }

        for i in offset..<(data.count - 3) {
            // Check for 4-byte start code: 0x00000001
            if i < data.count - 3 &&
               data[i] == 0x00 && data[i+1] == 0x00 &&
               data[i+2] == 0x00 && data[i+3] == 0x01 {
                return (i, 4)
            }
            // Check for 3-byte start code: 0x000001
            if data[i] == 0x00 && data[i+1] == 0x00 && data[i+2] == 0x01 {
                return (i, 3)
            }
        }
        return nil
    }

    /// Parse a NAL unit from raw data (without start code)
    private func parseNALUnit(_ data: Data) -> NALUnit? {
        guard !data.isEmpty else { return nil }

        let nalTypeValue = data[0] & 0x1F
        let nalType = NALUnitType(rawValue: nalTypeValue) ?? .unspecified

        return NALUnit(type: nalType, data: data)
    }

    /// Store parameter sets for decoder initialization
    private func updateParameterSets(_ unit: NALUnit) {
        switch unit.type {
        case .sps:
            sps = unit.data
        case .pps:
            pps = unit.data
        default:
            break
        }
    }

    /// Convert NAL unit to AVCC format (4-byte length prefix instead of start code)
    /// Required by VideoToolbox
    static func toAVCC(_ nalUnit: Data) -> Data {
        var length = UInt32(nalUnit.count).bigEndian
        var avccData = Data(bytes: &length, count: 4)
        avccData.append(nalUnit)
        return avccData
    }

    /// Convert multiple NAL units to single AVCC buffer
    static func toAVCC(_ nalUnits: [NALUnit]) -> Data {
        var avccData = Data()
        for unit in nalUnits {
            avccData.append(toAVCC(unit.data))
        }
        return avccData
    }

    /// Reset parser state
    func reset() {
        buffer.removeAll()
        sps = nil
        pps = nil
    }
}
