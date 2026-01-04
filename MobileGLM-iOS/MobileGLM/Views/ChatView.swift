import SwiftUI

/// Chat input and message display view
struct ChatView: View {
    @ObservedObject var webSocket: WebSocketClient
    @State private var inputText = ""
    @FocusState private var isInputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            // Messages list
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(webSocket.chatMessages) { message in
                            MessageBubble(message: message)
                                .id(message.id)
                        }

                        // Show agent status
                        if webSocket.agentStatus == .thinking {
                            HStack(spacing: 8) {
                                ProgressView()
                                    .progressViewStyle(CircularProgressViewStyle(tint: .gray))
                                    .scaleEffect(0.8)
                                Text("Thinking...")
                                    .font(.caption)
                                    .foregroundColor(.gray)
                            }
                            .padding(.horizontal)
                            .id("status")
                        }

                        // Show error if any
                        if let error = webSocket.agentError {
                            Text(error)
                                .font(.caption)
                                .foregroundColor(.red)
                                .padding(.horizontal)
                                .id("error")
                        }
                    }
                    .padding(.vertical, 8)
                }
                .onChange(of: webSocket.chatMessages.count) { _ in
                    // Scroll to bottom when new message arrives
                    if let lastMessage = webSocket.chatMessages.last {
                        withAnimation {
                            proxy.scrollTo(lastMessage.id, anchor: .bottom)
                        }
                    }
                }
                .onChange(of: webSocket.agentStatus) { status in
                    if status == .thinking {
                        withAnimation {
                            proxy.scrollTo("status", anchor: .bottom)
                        }
                    }
                }
                .onAppear {
                    // Scroll to bottom when chat view appears
                    if let lastMessage = webSocket.chatMessages.last {
                        proxy.scrollTo(lastMessage.id, anchor: .bottom)
                    }
                }
            }

            Divider()

            // Input area
            HStack(spacing: 8) {
                // New conversation button
                Button(action: { webSocket.newSession() }) {
                    Image(systemName: "plus.circle")
                        .font(.title2)
                        .foregroundColor(.gray)
                }

                TextField("Ask something...", text: $inputText)
                    .textFieldStyle(.roundedBorder)
                    .focused($isInputFocused)
                    .onSubmit {
                        sendMessage()
                    }

                if webSocket.agentStatus == .thinking || webSocket.agentStatus == .responding {
                    // Show cancel button when agent is working
                    Button(action: { webSocket.cancelAgent() }) {
                        Image(systemName: "stop.circle.fill")
                            .font(.title2)
                            .foregroundColor(.red)
                    }
                } else {
                    // Show send button when idle
                    Button(action: sendMessage) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                            .foregroundColor(inputText.isEmpty ? .gray : .blue)
                    }
                    .disabled(inputText.isEmpty)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color(.systemGray6))
        }
        // Messages are now added directly in WebSocketClient when agent_step messages arrive
    }

    private func sendMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        // Check connection before sending
        guard case .connected = webSocket.connectionState else {
            // Not connected - show error in chat
            webSocket.chatMessages.append(ChatMessage(content: "⚠️ Not connected to server", isUser: false))
            return
        }

        // Add user message
        webSocket.chatMessages.append(ChatMessage(content: text, isUser: true))
        inputText = ""

        // Send to agent
        webSocket.sendChat(text)
    }
}

/// Message bubble component
struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.isUser { Spacer(minLength: 40) }

            Text(markdownContent)
                .lineLimit(nil)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(message.isUser ? Color.blue : Color(.systemGray5))
                .foregroundColor(message.isUser ? .white : .primary)
                .cornerRadius(16)

            if !message.isUser { Spacer(minLength: 40) }
        }
        .padding(.horizontal, 12)
    }

    /// Parse markdown content, falling back to plain text if parsing fails
    private var markdownContent: AttributedString {
        // For agent responses, use interpretedSyntax to preserve newlines
        // CommonMark parsing treats single \n as soft break, which loses formatting
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )

        if let attributed = try? AttributedString(markdown: message.content, options: options) {
            return attributed
        }
        return AttributedString(message.content)
    }
}

#Preview {
    ChatView(webSocket: WebSocketClient())
}
