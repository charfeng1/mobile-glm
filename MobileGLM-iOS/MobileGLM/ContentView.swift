import SwiftUI

struct ContentView: View {

    @StateObject private var webSocket = WebSocketClient()
    @StateObject private var phoneDisplay = PhoneDisplayViewModel()

    // Persisted settings using AppStorage (saved automatically after first edit)
    // Default IP is placeholder - users should enter their computer's IP on first use
    @AppStorage("serverHost") private var serverHost = "192.168.1.100"
    @AppStorage("serverPort") private var serverPort = "8765"

    @State private var showingSettings = false
    @State private var showingChat = false

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                if case .connected = webSocket.connectionState, webSocket.deviceInfo != nil {
                    // Connected - show phone display with chat
                    VStack(spacing: 0) {
                        if showingChat {
                            // Split view: phone + chat
                            GeometryReader { geo in
                                VStack(spacing: 0) {
                                    // Phone display (40% of height)
                                    PhoneDisplayView(viewModel: phoneDisplay)
                                        .frame(height: geo.size.height * 0.4)

                                    // Chat view (60% of height)
                                    ChatView(webSocket: webSocket)
                                        .frame(height: geo.size.height * 0.6)
                                }
                            }
                        } else {
                            // Full phone view
                            PhoneDisplayView(viewModel: phoneDisplay)
                        }

                        // Compact navigation buttons
                        HStack(spacing: 30) {
                            Button(action: { disconnect() }) {
                                Image(systemName: "xmark")
                                    .font(.caption)
                                    .foregroundColor(.gray)
                                    .frame(width: 32, height: 32)
                            }

                            Spacer()

                            Button(action: { webSocket.sendBack() }) {
                                Image(systemName: "arrow.backward")
                                    .font(.body)
                                    .foregroundColor(.white)
                                    .frame(width: 44, height: 32)
                            }

                            Button(action: { webSocket.sendHome() }) {
                                Image(systemName: "circle")
                                    .font(.body)
                                    .foregroundColor(.white)
                                    .frame(width: 44, height: 32)
                            }

                            Button(action: { webSocket.sendRecents() }) {
                                Image(systemName: "square.on.square")
                                    .font(.body)
                                    .foregroundColor(.white)
                                    .frame(width: 44, height: 32)
                            }

                            Spacer()

                            // Chat toggle button
                            Button(action: { showingChat.toggle() }) {
                                Image(systemName: showingChat ? "message.fill" : "message")
                                    .font(.caption)
                                    .foregroundColor(showingChat ? .blue : .gray)
                                    .frame(width: 32, height: 32)
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 6)
                        .background(Color.black)
                    }
                } else {
                    // Not connected - show connection UI
                    connectionView
                }
            }
            .navigationTitle("MobileGLM")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: { showingSettings = true }) {
                        Image(systemName: "gear")
                    }
                }

                if case .connected = webSocket.connectionState {
                    ToolbarItem(placement: .navigationBarLeading) {
                        Button("Disconnect") {
                            disconnect()
                        }
                    }
                }
            }
            .sheet(isPresented: $showingSettings) {
                settingsView
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Connection View

    private var connectionView: some View {
        VStack(spacing: 24) {
            Image(systemName: "iphone.and.arrow.forward")
                .font(.system(size: 60))
                .foregroundColor(.gray)

            Text("Connect to Phone")
                .font(.title2)
                .foregroundColor(.white)

            VStack(spacing: 16) {
                HStack {
                    TextField("Server IP", text: $serverHost)
                        .textFieldStyle(.roundedBorder)
                        .keyboardType(.decimalPad)
                        .autocapitalization(.none)

                    Text(":")
                        .foregroundColor(.gray)

                    TextField("Port", text: $serverPort)
                        .textFieldStyle(.roundedBorder)
                        .keyboardType(.numberPad)
                        .frame(width: 80)
                }
                .padding(.horizontal)

                Button(action: connect) {
                    HStack {
                        if case .connecting = webSocket.connectionState {
                            ProgressView()
                                .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                .padding(.trailing, 8)
                        }
                        Text(connectButtonTitle)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.blue)
                    .foregroundColor(.white)
                    .cornerRadius(10)
                }
                .padding(.horizontal)
                .disabled(isConnecting)
            }

            if case .error(let message) = webSocket.connectionState {
                Text(message)
                    .foregroundColor(.red)
                    .font(.caption)
                    .padding()
            }

            Spacer()

            Text("Run the WebSocket bridge on your computer:\nuv run python scrcpy_ws_bridge.py")
                .font(.caption)
                .foregroundColor(.gray)
                .multilineTextAlignment(.center)
                .padding()
        }
        .padding(.top, 60)
    }

    // MARK: - Settings View

    private var settingsView: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    HStack {
                        Text("Host")
                        Spacer()
                        TextField("IP Address", text: $serverHost)
                            .multilineTextAlignment(.trailing)
                            .keyboardType(.decimalPad)
                    }
                    HStack {
                        Text("Port")
                        Spacer()
                        TextField("Port", text: $serverPort)
                            .multilineTextAlignment(.trailing)
                            .keyboardType(.numberPad)
                    }
                }

                if let device = webSocket.deviceInfo {
                    Section("Device") {
                        HStack {
                            Text("Name")
                            Spacer()
                            Text(device.name)
                                .foregroundColor(.gray)
                        }
                        HStack {
                            Text("Resolution")
                            Spacer()
                            Text("\(device.width) x \(device.height)")
                                .foregroundColor(.gray)
                        }
                        HStack {
                            Text("Serial")
                            Spacer()
                            Text(device.serial)
                                .foregroundColor(.gray)
                                .font(.caption)
                        }
                    }
                }

                Section("About") {
                    HStack {
                        Text("Version")
                        Spacer()
                        Text("1.0.0")
                            .foregroundColor(.gray)
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        showingSettings = false
                    }
                }
            }
        }
    }

    // MARK: - Actions

    private func connect() {
        guard let port = Int(serverPort) else {
            webSocket.connectionState = .error("Invalid port number")
            return
        }

        // Reset decoder state before reconnecting to clear stale SPS/PPS
        phoneDisplay.reset()

        // Configure callbacks
        webSocket.onVideoData = { data in
            phoneDisplay.handleVideoData(data)
        }

        webSocket.onDeviceInfo = { info in
            phoneDisplay.configure(with: info, webSocket: webSocket)
        }

        webSocket.connect(to: serverHost, port: port)
    }

    private func disconnect() {
        webSocket.disconnect()
        phoneDisplay.reset()
    }

    // MARK: - Helpers

    private var isConnecting: Bool {
        if case .connecting = webSocket.connectionState {
            return true
        }
        return false
    }

    private var connectButtonTitle: String {
        switch webSocket.connectionState {
        case .connecting:
            return "Connecting..."
        default:
            return "Connect"
        }
    }
}

#Preview {
    ContentView()
}
