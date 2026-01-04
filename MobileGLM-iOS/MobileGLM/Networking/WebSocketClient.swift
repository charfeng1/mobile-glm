import Foundation
import Combine

/// Connection state
enum ConnectionState {
    case disconnected
    case connecting
    case connected
    case error(String)
}

/// Agent status
enum AgentStatus {
    case idle
    case thinking
    case responding
}

/// A single chat message
struct ChatMessage: Identifiable {
    let id = UUID()
    let content: String
    let isUser: Bool
    let timestamp = Date()
}

/// WebSocket client for connecting to scrcpy bridge
class WebSocketClient: NSObject, ObservableObject, URLSessionWebSocketDelegate {

    @Published var connectionState: ConnectionState = .disconnected
    @Published var deviceInfo: DeviceInfo?
    @Published var agentStatus: AgentStatus = .idle
    @Published var agentResponse: String = ""
    @Published var agentError: String?
    @Published var chatMessages: [ChatMessage] = []

    private var webSocket: URLSessionWebSocketTask?
    private var session: URLSession?

    // Callbacks
    var onVideoData: ((Data) -> Void)?
    var onDeviceInfo: ((DeviceInfo) -> Void)?
    var onAgentResponse: ((String, Bool) -> Void)?  // (content, isDone)

    override init() {
        super.init()
    }

    /// Connect to WebSocket server
    func connect(to host: String, port: Int) {
        disconnect()

        connectionState = .connecting

        let urlString = "ws://\(host):\(port)"
        guard let url = URL(string: urlString) else {
            connectionState = .error("Invalid URL")
            return
        }

        // Create session with self as delegate to track connection state
        session = URLSession(configuration: .default, delegate: self, delegateQueue: .main)
        webSocket = session?.webSocketTask(with: url)

        webSocket?.resume()
        // Note: connectionState will be set to .connected in delegate callback

        print("[WebSocket] Connecting to \(urlString)")

        // Start receiving messages
        receiveMessage()
    }

    // MARK: - URLSessionWebSocketDelegate

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        print("[WebSocket] Connection established")
        connectionState = .connected
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        print("[WebSocket] Connection closed: \(closeCode)")
        DispatchQueue.main.async {
            self.connectionState = .disconnected
        }
    }

    /// Called when the task completes (including errors)
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            print("[WebSocket] Task completed with error: \(error)")
            DispatchQueue.main.async {
                self.connectionState = .error(error.localizedDescription)
            }
        }
    }

    /// Disconnect from server
    func disconnect() {
        webSocket?.cancel(with: .normalClosure, reason: nil)
        webSocket = nil
        session?.invalidateAndCancel()
        session = nil
        deviceInfo = nil
        connectionState = .disconnected
        print("[WebSocket] Disconnected")
    }

    /// Receive messages recursively
    private func receiveMessage() {
        webSocket?.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                self.handleMessage(message)
                // Continue receiving
                self.receiveMessage()

            case .failure(let error):
                DispatchQueue.main.async {
                    self.connectionState = .error(error.localizedDescription)
                }
                print("[WebSocket] Receive error: \(error)")
            }
        }
    }

    /// Handle incoming message
    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .string(let text):
            // JSON message (device info)
            handleJSONMessage(text)

        case .data(let data):
            // Binary H.264 data
            onVideoData?(data)

        @unknown default:
            print("[WebSocket] Unknown message type")
        }
    }

    /// Parse JSON message
    private func handleJSONMessage(_ text: String) {
        guard let data = text.data(using: .utf8) else { return }

        do {
            // Try to parse as a generic dictionary first to check type
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let type = json["type"] as? String else {
                print("[WebSocket] Unknown message format")
                return
            }

            switch type {
            case "device_info":
                let info = try JSONDecoder().decode(DeviceInfo.self, from: data)
                DispatchQueue.main.async {
                    self.deviceInfo = info
                }
                onDeviceInfo?(info)
                print("[WebSocket] Device: \(info.name) (\(info.width)x\(info.height))")

            case "agent_status":
                if let status = json["status"] as? String {
                    DispatchQueue.main.async {
                        switch status {
                        case "thinking":
                            self.agentStatus = .thinking
                            self.agentError = nil
                        default:
                            break
                        }
                    }
                    print("[WebSocket] Agent status: \(status)")
                }

            case "agent_step":
                // Intermediate step: thinking, tool_call, tool_result, response
                let stepType = json["step_type"] as? String ?? "unknown"
                let content = json["content"] as? String ?? ""
                let metadata = json["metadata"] as? [String: Any] ?? [:]

                // Filter out internal statuses that shouldn't be shown to user
                let status = metadata["status"] as? String ?? ""
                let internalStatuses = ["stuck", "blocked"]
                let shouldHide = stepType == "tool_result" && internalStatuses.contains(status)

                DispatchQueue.main.async {
                    self.agentStatus = .responding
                    // Only show user-facing steps, hide internal ones like "stuck"
                    if !shouldHide {
                        let prefix = self.stepTypePrefix(stepType)
                        self.chatMessages.append(ChatMessage(content: "\(prefix) \(content)", isUser: false))
                    }
                }
                print("[WebSocket] Agent step (\(stepType)): \(content.prefix(50))...")

            case "agent_response":
                let content = json["content"] as? String ?? ""
                let done = json["done"] as? Bool ?? true
                DispatchQueue.main.async {
                    self.agentStatus = done ? .idle : .responding
                    // Only add non-empty final responses (steps already added content)
                    if !content.isEmpty {
                        self.agentResponse = content
                    }
                }
                onAgentResponse?(content, done)
                print("[WebSocket] Agent response: done=\(done)")

            case "agent_error":
                let error = json["error"] as? String ?? "Unknown error"
                DispatchQueue.main.async {
                    self.agentStatus = .idle
                    self.agentError = error
                    self.chatMessages.append(ChatMessage(content: "❌ \(error)", isUser: false))
                }
                print("[WebSocket] Agent error: \(error)")

            case "session_cleared":
                let message = json["message"] as? String ?? "新对话已开始"
                DispatchQueue.main.async {
                    self.chatMessages.removeAll()
                    self.chatMessages.append(ChatMessage(content: message, isUser: false))
                    self.agentStatus = .idle
                    self.agentError = nil
                    self.agentResponse = ""
                }
                print("[WebSocket] Session cleared: \(message)")

            default:
                print("[WebSocket] Unknown message type: \(type)")
            }
        } catch {
            print("[WebSocket] Failed to parse JSON: \(error)")
        }
    }

    // MARK: - Control Messages

    /// Send touch event
    func sendTouch(action: String, x: CGFloat, y: CGFloat) {
        let message: [String: Any] = [
            "type": "touch",
            "action": action,
            "x": x,
            "y": y
        ]
        sendJSON(message)
    }

    /// Send back button
    func sendBack() {
        sendJSON(["type": "back"])
    }

    /// Send home button
    func sendHome() {
        sendJSON(["type": "home"])
    }

    /// Send recents button
    func sendRecents() {
        sendJSON(["type": "recents"])
    }

    /// Send chat message to agent
    func sendChat(_ message: String) {
        DispatchQueue.main.async {
            self.agentStatus = .thinking
            self.agentError = nil
            self.agentResponse = ""
        }
        sendJSON(["type": "chat", "message": message])
    }

    /// Cancel the current agent task
    func cancelAgent() {
        sendJSON(["type": "cancel"])
        DispatchQueue.main.async {
            self.agentStatus = .idle
            self.chatMessages.append(ChatMessage(content: "[Cancelled]", isUser: false))
        }
    }

    /// Start a new conversation (clear session history)
    func newSession() {
        sendJSON(["type": "new_session"])
    }

    /// Send key event
    func sendKey(keycode: Int, action: String) {
        let message: [String: Any] = [
            "type": "key",
            "keycode": keycode,
            "action": action
        ]
        sendJSON(message)
    }

    /// Get prefix for step type
    private func stepTypePrefix(_ stepType: String) -> String {
        switch stepType {
        case "thinking":
            return "[Thinking]"
        case "tool_call":
            return "[Action]"
        case "tool_result":
            return "[Result]"
        case "response":
            return ""  // No prefix for final response
        default:
            return ""
        }
    }

    /// Send JSON message
    private func sendJSON(_ dict: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: dict),
              let text = String(data: data, encoding: .utf8) else {
            print("[WebSocket] Failed to serialize JSON: \(dict)")
            return
        }

        print("[WebSocket] Sending: \(text)")

        guard let socket = webSocket else {
            print("[WebSocket] No socket connection!")
            return
        }

        socket.send(.string(text)) { error in
            if let error = error {
                print("[WebSocket] Send error: \(error)")
            } else {
                print("[WebSocket] Sent successfully")
            }
        }
    }
}
